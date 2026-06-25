"""Python adapter that drives the native ``suggest_config`` kernel.

Usage::

    from goldenmatch.core.suggest import review_config

    suggestions = review_config(df, config)
    for s in suggestions:
        print(s.rationale, s.patch)

If the native wheel is absent, :exc:`SuggestionsNativeRequired` is raised
with an "install goldenmatch[native]" message.

Signature chosen:
    ``review_config(df, config, *, priors=None, verify=True) -> list[Suggestion]``

We accept a raw ``pl.DataFrame`` + ``GoldenMatchConfig`` and run MatchEngine
internally. This is the simplest shape for Task 15's benchmark: run dedupe,
ask for suggestions, compare.  An alternative (pre-computed EngineResult + df)
is slightly cheaper but adds caller boilerplate that hides the integration path.

Self-verification (verify=True, the default)
--------------------------------------------
After the kernel returns ranked suggestions, each one is applied to a candidate
config and the pipeline is re-run.  The candidate's score distribution is
compared to the baseline using an unsupervised health proxy (see
``goldenmatch.core.suggest.health.suggestion_health``).  A suggestion is kept
only if the candidate's health is >= baseline health - EPS.  This prevents
net-negative suggestions (e.g. a threshold change that lowers F1 on an
already-healthy config) from reaching the user.

Cost: one extra pipeline run per candidate (2-5 typical).  Verification can be
disabled with ``verify=False`` (returns raw kernel suggestions for debugging /
bench A/B) or with the environment variable ``GOLDENMATCH_SUGGEST_VERIFY=0``.
"""
from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any

import polars as pl
import pyarrow as pa

from goldenmatch.core.suggest.types import Suggestion, SuggestionsNativeRequired

logger = logging.getLogger(__name__)

# ── Arrow schemas (frozen - must match the Rust kernel exactly) ────────────

_SCORED_PAIRS_SCHEMA = pa.schema([
    pa.field("id_a", pa.int64()),
    pa.field("id_b", pa.int64()),
    pa.field("score", pa.float64()),
])

_CLUSTERS_SCHEMA = pa.schema([
    pa.field("cluster_id", pa.int64()),
    pa.field("size", pa.int64()),
    pa.field("confidence", pa.float64()),
    pa.field("quality", pa.utf8()),
    pa.field("oversized", pa.bool_()),
])

_COLUMN_SIGNALS_SCHEMA = pa.schema([
    pa.field("field", pa.utf8()),
    pa.field("col_type", pa.utf8()),
    pa.field("scorer", pa.utf8()),
    pa.field("in_blocking", pa.bool_()),
    pa.field("in_negative_evidence", pa.bool_()),
    pa.field("identity_score", pa.float64()),
    pa.field("corruption_score", pa.float64()),
    pa.field("collision_rate", pa.float64()),
    pa.field("cardinality_ratio", pa.float64()),
    pa.field("null_rate", pa.float64()),
    pa.field("variant_rate", pa.float64()),
])


# ── Kernel access ──────────────────────────────────────────────────────────

def _require_kernel() -> Any:
    """Return the native module, raising SuggestionsNativeRequired if absent."""
    from goldenmatch.core._native_loader import native_module
    nm = native_module()
    if nm is None or not hasattr(nm, "suggest_config"):
        raise SuggestionsNativeRequired(
            "Config suggestions require the native kernel. "
            "Install with: pip install goldenmatch[native]"
        )
    return nm


# ── Arrow batch builders ───────────────────────────────────────────────────

def _build_scored_pairs_batch(
    scored_pairs: list[tuple[int, int, float]],
) -> pa.RecordBatch:
    """Convert ``[(id_a, id_b, score), ...]`` to the frozen Arrow schema."""
    if not scored_pairs:
        return pa.record_batch(
            {"id_a": pa.array([], type=pa.int64()),
             "id_b": pa.array([], type=pa.int64()),
             "score": pa.array([], type=pa.float64())},
            schema=_SCORED_PAIRS_SCHEMA,
        )
    id_a_list, id_b_list, score_list = zip(*scored_pairs)
    return pa.record_batch(
        {
            "id_a": pa.array(list(id_a_list), type=pa.int64()),
            "id_b": pa.array(list(id_b_list), type=pa.int64()),
            "score": pa.array(list(score_list), type=pa.float64()),
        },
        schema=_SCORED_PAIRS_SCHEMA,
    )


def _build_clusters_batch(
    clusters: dict[int, dict],
) -> pa.RecordBatch:
    """Convert ``EngineResult.clusters`` dict to the frozen Arrow schema."""
    # Only multi-member clusters carry meaningful quality/confidence signals.
    # Singletons are included so the kernel can compute the match rate.
    rows = []
    for cid, info in clusters.items():
        rows.append({
            "cluster_id": cid,
            "size": int(info.get("size", 1)),
            "confidence": float(info.get("confidence", 0.0)),
            "quality": str(info.get("cluster_quality", info.get("quality", "strong"))),
            "oversized": bool(info.get("oversized", False)),
        })
    if not rows:
        return pa.record_batch(
            {
                "cluster_id": pa.array([], type=pa.int64()),
                "size": pa.array([], type=pa.int64()),
                "confidence": pa.array([], type=pa.float64()),
                "quality": pa.array([], type=pa.utf8()),
                "oversized": pa.array([], type=pa.bool_()),
            },
            schema=_CLUSTERS_SCHEMA,
        )
    return pa.record_batch(
        {
            "cluster_id": pa.array([r["cluster_id"] for r in rows], type=pa.int64()),
            "size": pa.array([r["size"] for r in rows], type=pa.int64()),
            "confidence": pa.array([r["confidence"] for r in rows], type=pa.float64()),
            "quality": pa.array([r["quality"] for r in rows], type=pa.utf8()),
            "oversized": pa.array([r["oversized"] for r in rows], type=pa.bool_()),
        },
        schema=_CLUSTERS_SCHEMA,
    )


def _collision_rates(
    clusters: dict[int, dict],
    df: pl.DataFrame,
) -> dict[str, float]:
    """Compute per-column collision rate over multi-member clusters.

    collision_rate[col] = fraction of multi-member clusters where that column
    has >= 2 distinct non-null values among the cluster's member rows.

    A high rate signals the column disagrees inside merged clusters -- the
    negative-evidence signal the kernel uses for Rule 3.
    """
    # Only multi-member clusters are interesting
    multi = {
        cid: info
        for cid, info in clusters.items()
        if info.get("size", 1) > 1 and not info.get("oversized", False)
    }
    if not multi:
        return {}

    # Work on string columns only (what blockers and scorers actually use)
    string_cols = [
        c for c in df.columns
        if not c.startswith("__") and df[c].dtype == pl.String
    ]
    if not string_cols:
        return {}

    collision_count: dict[str, int] = {c: 0 for c in string_cols}
    n_multi = len(multi)

    id_col = "__row_id__" if "__row_id__" in df.columns else None

    for _cid, info in multi.items():
        member_ids: list[int] = list(info.get("members", []))
        if not member_ids:
            continue

        if id_col:
            cluster_df = df.filter(pl.col(id_col).is_in(member_ids))
        else:
            # Fallback: slice by positional index (row_id == positional index)
            valid_ids = [m for m in member_ids if 0 <= m < df.height]
            if not valid_ids:
                continue
            cluster_df = df[valid_ids]

        for col in string_cols:
            distinct_non_null = (
                cluster_df[col]
                .drop_nulls()
                .n_unique()
            )
            if distinct_non_null >= 2:
                collision_count[col] += 1

    return {col: cnt / n_multi for col, cnt in collision_count.items()}


def _config_summary(config: Any) -> dict:
    """Serialize the config to the ConfigSummary shape the kernel expects.

    The kernel expects::

        {
            "matchkeys": [
                {
                    "name": str,
                    "kind": "weighted" | "exact" | "probabilistic",
                    "threshold": float | null,
                    "fields": [{"field": str, "scorer": str | null, "weight": float | null}]
                }
            ],
            "negative_evidence": ["field_name", ...]
        }
    """
    matchkeys_summary = []
    ne_fields: list[str] = []
    try:
        for mk in config.get_matchkeys():
            fields_summary = []
            for f in mk.fields:
                fields_summary.append({
                    "field": f.field or "",
                    "scorer": f.scorer,
                    "weight": f.weight,
                })
            matchkeys_summary.append({
                "name": mk.name,
                "kind": mk.type or "weighted",
                "threshold": mk.threshold,
                "fields": fields_summary,
            })
            if mk.negative_evidence:
                for ne in mk.negative_evidence:
                    if ne.field not in ne_fields:
                        ne_fields.append(ne.field)
    except Exception:
        logger.warning(
            "_config_summary: failed to serialize config", exc_info=True
        )

    return {
        "matchkeys": matchkeys_summary,
        "negative_evidence": ne_fields,
    }


def _build_column_signals_batch(
    df: pl.DataFrame,
    config: Any,
    clusters: dict[int, dict],
) -> pa.RecordBatch:
    """Build the column_signals Arrow batch from the df + config + run results.

    One row per column referenced by the config (or all data columns).

    Sources:
    - col_type: from autoconfig ColumnProfile classification
    - scorer: from matchkey fields
    - in_blocking: from config.blocking fields (via collect_blocking_fields)
    - in_negative_evidence: from matchkey NE fields
    - identity_score, corruption_score: compute_column_priors()
    - cardinality_ratio, null_rate: computed directly from df
    - collision_rate: _collision_rates()
    - variant_rate: blocking_risk() -- defaults to 0.0 when goldencheck absent
    """
    from goldenmatch.core.indicators import compute_column_priors
    from goldenmatch.core.quality import blocking_risk

    # -- Which columns to include (all non-internal data columns) --
    data_cols = [c for c in df.columns if not c.startswith("__")]

    # -- Gather priors (identity + corruption scores) --
    try:
        col_priors = compute_column_priors(df.select(data_cols))
    except Exception:
        logger.debug("compute_column_priors failed; using defaults", exc_info=True)
        col_priors = {}

    # -- Blocking fields --
    blocking_fields: set[str] = set()
    try:
        if config.blocking is not None:
            from goldenmatch.core.blocker import collect_blocking_fields
            blocking_fields = set(collect_blocking_fields(config.blocking))
    except Exception:
        logger.debug("collect_blocking_fields failed; using empty set", exc_info=True)

    # -- Matchkey field → scorer mapping and negative-evidence set --
    field_scorer: dict[str, str] = {}
    ne_fields: set[str] = set()
    try:
        matchkeys = config.get_matchkeys()
        for mk in matchkeys:
            for f in mk.fields:
                fname = f.field or ""
                if fname and f.scorer:
                    field_scorer[fname] = f.scorer
            if mk.negative_evidence:
                for ne in mk.negative_evidence:
                    ne_fields.add(ne.field)
    except Exception:
        logger.debug("matchkey field introspection failed", exc_info=True)

    # -- col_type via the same classifier autoconfig uses --
    col_types: dict[str, str] = {}
    try:
        from goldenmatch.core.autoconfig import profile_columns
        profiles = profile_columns(df.select(data_cols))
        for p in profiles:
            col_types[p.name] = p.col_type
    except Exception:
        logger.debug("profile_columns failed; col_type defaults to 'string'", exc_info=True)

    # -- cardinality_ratio and null_rate directly from df --
    n_rows = max(df.height, 1)
    cardinality_ratios: dict[str, float] = {}
    null_rates: dict[str, float] = {}
    for col in data_cols:
        series = df[col]
        n_non_null = series.drop_nulls().len()
        null_rates[col] = 1.0 - n_non_null / n_rows
        if n_non_null > 0:
            cardinality_ratios[col] = series.drop_nulls().n_unique() / n_non_null
        else:
            cardinality_ratios[col] = 0.0

    # -- collision_rate from run results --
    collision_rates = _collision_rates(clusters, df)

    # -- variant_rate from goldencheck blocking_risk (fail-open: 0.0) --
    try:
        variant_risk = blocking_risk(df.select(data_cols)) or {}
    except Exception:
        variant_risk = {}

    # -- Assemble rows --
    rows_field: list[str] = []
    rows_col_type: list[str] = []
    rows_scorer: list[str] = []
    rows_in_blocking: list[bool] = []
    rows_in_ne: list[bool] = []
    rows_identity: list[float] = []
    rows_corruption: list[float] = []
    rows_collision: list[float] = []
    rows_cardinality: list[float] = []
    rows_null: list[float] = []
    rows_variant: list[float] = []

    for col in data_cols:
        prior = col_priors.get(col)
        rows_field.append(col)
        rows_col_type.append(col_types.get(col, "string"))
        rows_scorer.append(field_scorer.get(col, ""))
        rows_in_blocking.append(col in blocking_fields)
        rows_in_ne.append(col in ne_fields)
        rows_identity.append(float(prior.identity_score) if prior else 0.0)
        rows_corruption.append(float(prior.corruption_score) if prior else 0.0)
        rows_collision.append(float(collision_rates.get(col, 0.0)))
        rows_cardinality.append(float(cardinality_ratios.get(col, 0.0)))
        rows_null.append(float(null_rates.get(col, 0.0)))
        rows_variant.append(float(variant_risk.get(col, 0.0)))

    if not rows_field:
        return pa.record_batch(
            {f.name: pa.array([], type=f.type) for f in _COLUMN_SIGNALS_SCHEMA},
            schema=_COLUMN_SIGNALS_SCHEMA,
        )

    return pa.record_batch(
        {
            "field": pa.array(rows_field, type=pa.utf8()),
            "col_type": pa.array(rows_col_type, type=pa.utf8()),
            "scorer": pa.array(rows_scorer, type=pa.utf8()),
            "in_blocking": pa.array(rows_in_blocking, type=pa.bool_()),
            "in_negative_evidence": pa.array(rows_in_ne, type=pa.bool_()),
            "identity_score": pa.array(rows_identity, type=pa.float64()),
            "corruption_score": pa.array(rows_corruption, type=pa.float64()),
            "collision_rate": pa.array(rows_collision, type=pa.float64()),
            "cardinality_ratio": pa.array(rows_cardinality, type=pa.float64()),
            "null_rate": pa.array(rows_null, type=pa.float64()),
            "variant_rate": pa.array(rows_variant, type=pa.float64()),
        },
        schema=_COLUMN_SIGNALS_SCHEMA,
    )


# ── Environment flag ───────────────────────────────────────────────────────

def _verify_enabled_by_env() -> bool:
    """Check if GOLDENMATCH_SUGGEST_VERIFY env var disables verification.

    Returns True (verify ON) unless GOLDENMATCH_SUGGEST_VERIFY is set to
    "0", "false", or "disabled" (case-insensitive).  Mirrors the repo-wide
    env-flag pattern for kill-switches.
    """
    val = os.environ.get("GOLDENMATCH_SUGGEST_VERIFY", "").strip().lower()
    return val not in {"0", "false", "disabled"}


def _full_dist_enabled() -> bool:
    """When True, source the kernel's scored_pairs from a threshold-0 diagnostic
    run (full pre-threshold distribution) instead of the threshold-filtered run.
    Default OFF -> byte-identical to current behavior."""
    return os.environ.get("GOLDENMATCH_SUGGEST_FULL_DIST", "0").strip().lower() in {"1", "true", "on"}


def _zero_threshold_config(config):
    """Deep-copy the config with every matchkey threshold forced to 0.0. Used
    ONLY to widen the diagnostic scored_pairs run; blocking (the candidate set) is
    unchanged. Never mutates the input."""
    diag = copy.deepcopy(config)
    try:
        for mk in diag.get_matchkeys():
            if getattr(mk, "threshold", None) is not None:
                mk.threshold = 0.0
    except Exception:
        logger.debug("_zero_threshold_config: failed to zero thresholds", exc_info=True)
    return diag


def _diagnostic_scored_pairs(engine, df, config):
    """Run the SAME engine at threshold 0 to capture the full candidate-pair
    score distribution (the sub-threshold tail). Returns scored_pairs; clusters
    discarded. Falls back to None on failure (caller keeps the filtered pairs)."""
    try:
        diag_cfg = _zero_threshold_config(config)
        diag_result = engine._run_pipeline(df, diag_cfg)
        return diag_result.scored_pairs
    except Exception:
        logger.debug("_diagnostic_scored_pairs: diagnostic run failed", exc_info=True)
        return None


# Maximum number of candidate suggestions to verify (avoids runaway cost
# when the kernel returns an unusually large list).
_MAX_VERIFY_CANDIDATES: int = 8

# Epsilon: keep a suggestion if cand_health >= baseline_health - EPS.
# Near-zero so we only suppress genuine health regressions.
_VERIFY_EPS: float = 1e-6


# ── Public API ─────────────────────────────────────────────────────────────

def review_config(
    df: pl.DataFrame,
    config: Any,
    *,
    priors: dict | None = None,
    verify: bool = True,
) -> list[Suggestion]:
    """Analyze a dedupe run and return config improvement suggestions.

    Runs the pipeline (``MatchEngine._run_pipeline``) internally on ``df`` +
    ``config`` to obtain scored pairs and clusters, then assembles the three
    Arrow batches required by the native kernel and calls
    ``goldenmatch._native.suggest_config``.

    Args:
        df: The DataFrame that was (or will be) deduped.  Must already carry
            ``__row_id__`` if you want collision-rate computation to work;
            the adapter adds one automatically if absent.
        config: A ``GoldenMatchConfig`` (or any object with ``get_matchkeys()``,
                ``.blocking``, and ``model_dump()``).
        priors: Optional priors dict passed to the kernel (``{"counts": {}}``
                by default; Plan 2 fills this with cross-run memory).
        verify: When True (default), each suggestion is applied to a candidate
                config and the pipeline is re-run.  Only suggestions whose
                candidate health >= baseline health - EPS are kept.  This
                prevents net-negative suggestions from reaching the user.
                Set False to return raw kernel suggestions (for debugging /
                bench A/B).  Can also be disabled globally with the env var
                ``GOLDENMATCH_SUGGEST_VERIFY=0``.

                Cost: one extra pipeline run per candidate (2-5 typical,
                capped at 8).  Negligible vs the baseline run itself.

    Returns:
        A list of :class:`Suggestion` dataclasses.  Empty when the kernel
        finds nothing to improve (or when all suggestions are health-worsening
        and verify=True).

    Raises:
        SuggestionsNativeRequired: When the native wheel is absent or the
            ``suggest_config`` symbol is missing.
    """
    nm = _require_kernel()

    # Resolve verify: kwarg AND env flag must both be True
    _do_verify = verify and _verify_enabled_by_env()

    # Ensure the df has __row_id__ so collision-rate lookups work
    if "__row_id__" not in df.columns:
        df = df.with_row_index("__row_id__").with_columns(
            pl.col("__row_id__").cast(pl.Int64)
        )

    # -- Run the pipeline to get scored_pairs + clusters --
    from goldenmatch.tui.engine import MatchEngine

    # Deep-copy the config so disabling rerank below NEVER mutates the caller's
    # object (the engine + the suggestion summary all run against the copy).
    _config = copy.deepcopy(config)

    # Disable rerank to avoid HuggingFace model downloads in tests/offline env
    try:
        for mk in _config.get_matchkeys():
            if mk.rerank:
                mk.rerank = False
    except Exception:
        logger.debug(
            "review_config: failed to disable rerank on config copy", exc_info=True
        )

    # Build an engine over the in-memory frame (no file loading).
    engine = MatchEngine.from_dataframe(df)

    try:
        result = engine._run_pipeline(df, _config)
    except Exception as exc:
        raise RuntimeError(
            f"review_config: pipeline failed on the provided df/config: {exc}"
        ) from exc

    scored_pairs = result.scored_pairs
    clusters = result.clusters

    # -- Build Arrow batches --
    scored_pairs_batch = _build_scored_pairs_batch(scored_pairs)
    clusters_batch = _build_clusters_batch(clusters)
    column_signals_batch = _build_column_signals_batch(df, _config, clusters)

    config_json = json.dumps(_config_summary(_config), default=str)
    priors_dict = priors if priors is not None else {"counts": {}}
    priors_json = json.dumps(priors_dict, default=str)

    # -- Call the native kernel --
    try:
        raw_json: str = nm.suggest_config(
            scored_pairs_batch,
            clusters_batch,
            column_signals_batch,
            config_json,
            priors_json,
        )
    except Exception as exc:
        raise RuntimeError(
            f"review_config: native suggest_config kernel failed: {exc}"
        ) from exc

    # -- Parse results --
    try:
        items = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"review_config: kernel returned invalid JSON: {exc}"
        ) from exc

    suggestions: list[Suggestion] = []
    for item in items:
        try:
            suggestions.append(Suggestion(
                id=str(item.get("id", "")),
                kind=str(item.get("kind", "")),
                target=str(item.get("target", "")),
                current_value=item.get("current_value"),
                proposed_value=item.get("proposed_value"),
                rationale=str(item.get("rationale", "")),
                predicted_effect=str(item.get("predicted_effect", "")),
                confidence=float(item.get("confidence", 0.0)),
                patch=dict(item.get("patch", {})),
                evidence=dict(item.get("evidence", {})),
            ))
        except Exception:
            logger.debug("Skipping malformed suggestion item: %r", item, exc_info=True)

    if not _do_verify or not suggestions:
        return suggestions

    # -- Self-verification pass (verify=True) --
    # Compute baseline health from the baseline run's scored pairs + threshold.
    from goldenmatch.core.suggest.apply import apply_suggestion
    from goldenmatch.core.suggest.health import suggestion_health_from_clusters

    # Cluster-based health proxy: immune to the scored-pairs threshold-filter
    # issue (_run_pipeline returns only pairs >= threshold, so mass_above is
    # always 1.0 in scored_pairs -- the scored-pairs proxy is not useful here).
    n_records = df.height
    baseline_health = suggestion_health_from_clusters(clusters, n_records)
    logger.debug(
        "review_config verify: baseline_health=%.4f n_records=%d n_clusters=%d",
        baseline_health, n_records, len(clusters),
    )

    # Cap verification at _MAX_VERIFY_CANDIDATES (cost guard)
    candidates = suggestions[:_MAX_VERIFY_CANDIDATES]
    tail = suggestions[_MAX_VERIFY_CANDIDATES:]  # pass through unverified if any

    verified: list[Suggestion] = []
    for s in candidates:
        try:
            cfg_cand = apply_suggestion(_config, s)
            # Disable rerank on the candidate config too
            try:
                for mk in cfg_cand.get_matchkeys():
                    if getattr(mk, "rerank", False):
                        mk.rerank = False
            except Exception:
                pass

            cand_result = engine._run_pipeline(df, cfg_cand)
            cand_health = suggestion_health_from_clusters(cand_result.clusters, n_records)

            logger.debug(
                "review_config verify: suggestion %r cand_health=%.4f (baseline=%.4f) -> %s",
                s.id, cand_health, baseline_health,
                "KEEP" if cand_health >= baseline_health - _VERIFY_EPS else "DROP",
            )

            if cand_health >= baseline_health - _VERIFY_EPS:
                verified.append(s)
        except Exception as exc:
            # Verification failure is conservative: keep the suggestion
            # (better to surface a potentially-bad suggestion than to silently
            # suppress one that couldn't be checked).
            logger.debug(
                "review_config verify: suggestion %r verification failed (%s) -- keeping",
                s.id, exc, exc_info=True,
            )
            verified.append(s)

    # Tail (beyond _MAX_VERIFY_CANDIDATES) passes through unverified.
    # In practice the kernel returns at most 5 suggestions so this is a no-op.
    if tail:
        logger.debug(
            "review_config verify: %d suggestion(s) beyond _MAX_VERIFY_CANDIDATES=%d "
            "passed through unverified",
            len(tail), _MAX_VERIFY_CANDIDATES,
        )
    return verified + tail
