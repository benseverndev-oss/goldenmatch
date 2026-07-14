"""Measurement stage for the Splink migration upgrade pass (Task U5).

Spec: docs/superpowers/specs/2026-07-14-splink-migration-upgrade-design.md,
"Measurement" section.

Runs ``dedupe_df`` twice on the upgrade pass's (already sampled) data --
baseline config, then upgraded config -- and reports the delta: cluster shape
+ wall per run, pairwise agreement vs an optional ``splink_clusters``
reference, and true pairwise + B-cubed metrics vs optional ``labels``.

Model injection (pinned by the spec): ``dedupe_df`` consumes trained models
via FILE (matchkey ``model_path``, the seam ``load_or_train_em`` reads), so
both EMResults -- baseline as-imported, upgraded post-levers -- are written to
temp files via ``save_json`` and each config COPY runs with its matchkey
``model_path`` pointed at its own file. Without this, ``dedupe_df`` would
silently retrain EM on the sample and measure the wrong models. Temp files
are cleaned up in a ``finally``; the configs handed to ``dedupe_df`` are
copies, so the ``MigrationResult``'s returned configs never carry a temp path.

ID mapping: ``DedupeResult.clusters`` members are ``__row_id__`` values --
0-based row positions of the input frame (verified against the dogfood
bench's probe). Rows are mapped back to user-facing ids via ``id_column``
when given, else the first column among ``unique_id``/``id``/``record_id``
whose values are unique, else positional string indices (which only support
shape metrics; external references then can't match and callers should pass
``id_column``).

``dedupe_df`` is imported lazily inside :func:`run_measurement` -- the lever
module stays import-light and this module only pulls the full pipeline in
when measurement actually runs.
"""
from __future__ import annotations

import shutil
import tempfile
import time
from collections import defaultdict
from itertools import combinations
from pathlib import Path

import polars as pl

from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.config.splink_upgrade import (
    MeasurementResult,
    PairwiseAgreement,
    RunStats,
    SplinkUpgradeError,
    TruthMetrics,
    _LeverContext,
    _load_frame,
)

# Clusters larger than this many members are excluded from pairwise-metric
# pair generation (quadratic blowup guard) -- same cap the dogfood bench
# orchestrator uses.
_PAIR_CAP_MEMBERS = 5000

# A run "snowballs" when its max cluster size exceeds this multiple of the
# reference max (splink_clusters max when provided, else the run's own p99).
_SNOWBALL_FACTOR = 10

_DEFAULT_ID_COLUMNS = ("unique_id", "id", "record_id")

_POSITIONAL_ID_SOURCE = "__position__"


# ── Pure metric helpers ──────────────────────────────────────────────────────


def pair_set(
    mapping: dict[str, str], cap: int = _PAIR_CAP_MEMBERS
) -> tuple[set[tuple[str, str]], int]:
    """Set of sorted within-cluster (a, b) id pairs.

    Clusters with more than ``cap`` members are skipped (quadratic guard);
    returns ``(pairs, n_capped_clusters)``. Ported from the dogfood bench
    orchestrator's ``pair_set``.
    """
    groups: dict[str, list[str]] = defaultdict(list)
    for uid, cid in mapping.items():
        groups[cid].append(uid)
    pairs: set[tuple[str, str]] = set()
    capped = 0
    for members in groups.values():
        if len(members) > cap:
            capped += 1
            continue
        members.sort()
        for a, b in combinations(members, 2):
            pairs.add((a, b))
    return pairs, capped


def pairwise_prf(
    pred_pairs: set[tuple[str, str]], true_pairs: set[tuple[str, str]]
) -> dict[str, float]:
    """Pairwise precision / recall / F1 of ``pred_pairs`` vs ``true_pairs``."""
    tp = len(pred_pairs & true_pairs)
    p = tp / len(pred_pairs) if pred_pairs else 0.0
    r = tp / len(true_pairs) if true_pairs else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": p, "recall": r, "f1": f1}


def bcubed(pred: dict[str, str], true: dict[str, str]) -> dict[str, float]:
    """B-cubed precision / recall / F1 of a predicted clustering vs truth.

    Per-item: precision = |pred-cluster ∩ true-cluster| / |pred-cluster|,
    recall = the same intersection / |true-cluster|, averaged over items
    present in BOTH mappings (ids missing from ``true`` are skipped, but
    still inflate the pred clusters they sit in). Ported (unrounded) from
    the dogfood bench orchestrator's ``bcubed``.
    """
    pred_groups: dict[str, set[str]] = defaultdict(set)
    true_groups: dict[str, set[str]] = defaultdict(set)
    for uid, cid in pred.items():
        pred_groups[cid].add(uid)
    for uid, cid in true.items():
        true_groups[cid].add(uid)
    n = 0
    p_sum = r_sum = 0.0
    for uid, cid in pred.items():
        if uid not in true:
            continue
        cp = pred_groups[cid]
        ct = true_groups[true[uid]]
        inter = len(cp & ct)
        p_sum += inter / len(cp)
        r_sum += inter / len(ct)
        n += 1
    p = p_sum / n if n else 0.0
    r = r_sum / n if n else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"precision": p, "recall": r, "f1": f1}


def snowball_flag(sizes: list[int], reference_max: int | None = None) -> bool:
    """True when the largest cluster exceeds ``_SNOWBALL_FACTOR`` x the
    reference max.

    ``reference_max`` is the splink_clusters max when the caller has one;
    ``None`` falls back to the run's OWN p99 cluster size (spec).
    """
    if not sizes:
        return False
    max_size = max(sizes)
    if reference_max is None:
        ordered = sorted(sizes)
        reference_max = ordered[min(len(ordered) - 1, round(0.99 * (len(ordered) - 1)))]
    if reference_max <= 0:
        return False
    return max_size > _SNOWBALL_FACTOR * reference_max


# ── ID + reference resolution ────────────────────────────────────────────────


def _resolve_ids(df: pl.DataFrame, id_column: str | None) -> tuple[list[str], str]:
    """Return ``(ids, source)``: one user-facing id per row position.

    An explicit ``id_column`` must exist and be unique (raises otherwise).
    The default tries ``unique_id``/``id``/``record_id`` (first unique-valued
    one wins), else positional string indices.
    """
    if id_column is not None:
        if id_column not in df.columns:
            raise SplinkUpgradeError(
                f"measurement id_column '{id_column}' is not a data column"
            )
        ids = [str(v) for v in df[id_column].to_list()]
        if len(set(ids)) != len(ids):
            raise SplinkUpgradeError(
                f"measurement id_column '{id_column}' has duplicate values; "
                "ids must uniquely identify rows"
            )
        return ids, id_column
    for candidate in _DEFAULT_ID_COLUMNS:
        if candidate in df.columns:
            ids = [str(v) for v in df[candidate].to_list()]
            if len(set(ids)) == len(ids):
                return ids, candidate
    return [str(i) for i in range(len(df))], _POSITIONAL_ID_SOURCE


def _load_reference(
    ref: pl.DataFrame | str | Path, sample_ids: set[str]
) -> tuple[dict[str, str], int]:
    """Load an id -> cluster_id reference frame, restricted to the sample ids.

    Column convention: first column = id, second = cluster_id (both cast to
    str). Returns ``(mapping, n_reference_rows)`` -- the raw row count lets
    the caller distinguish "empty reference" from "no ids overlap the
    sample". Raises when the frame has fewer than two columns.
    """
    frame = _load_frame(ref)
    if len(frame.columns) < 2:
        raise SplinkUpgradeError(
            "reference cluster frame needs at least two columns "
            "(id, cluster_id); got " + repr(frame.columns)
        )
    id_col, cluster_col = frame.columns[0], frame.columns[1]
    mapping: dict[str, str] = {}
    for rid, cid in zip(frame[id_col].to_list(), frame[cluster_col].to_list()):
        key = str(rid)
        if key in sample_ids:
            mapping[key] = str(cid)
    return mapping, len(frame)


def _checked_reference(
    ref: pl.DataFrame | str | Path,
    sample_ids: set[str],
    name: str,
    id_source: str,
    ctx: _LeverContext,
) -> dict[str, str] | None:
    """Load a reference and refuse a zero-overlap id join.

    A non-empty reference whose ids share NOTHING with the sample ids is an
    id-join failure (wrong/missing id column), not a real clustering signal --
    computing metrics against it would yield all-0.0 P/R/F1 that looks like a
    catastrophic regression. Warn (naming the id source + the fix) and return
    ``None`` so the metrics block is absent instead of garbage.
    """
    mapping, n_rows = _load_reference(ref, sample_ids)
    if not mapping and n_rows > 0:
        source_desc = (
            "positional row indices"
            if id_source == _POSITIONAL_ID_SOURCE
            else f"column '{id_source}'"
        )
        ctx.report.warn(
            "upgrade:measure",
            f"{name} reference ({n_rows} rows) shares no ids with the sample "
            f"(measurement ids came from {source_desc}) -- this is an id-join "
            f"failure, so metrics vs {name} are skipped rather than reported "
            "as 0.0; pass id_column= naming the data column that matches the "
            "reference ids",
            mapped_to=None,
        )
        return None
    return mapping


# ── Run + stats ──────────────────────────────────────────────────────────────


def _run_once(
    df: pl.DataFrame, config: GoldenMatchConfig, ids: list[str]
) -> tuple[dict[str, str], float]:
    """One ``dedupe_df`` run -> (id -> cluster_id mapping, wall seconds).

    Cluster members are 0-based row positions of ``df`` (``__row_id__``);
    rows absent from the result become their own singleton clusters (safety
    net -- the pipeline emits singletons, but a dropped row must not vanish
    from shape metrics).
    """
    import goldenmatch._api as _api  # lazy: full pipeline import

    t0 = time.perf_counter()
    result = _api.dedupe_df(df, config=config, source_name="splink_upgrade_measure")
    wall = time.perf_counter() - t0

    mapping: dict[str, str] = {}
    for cid, info in result.clusters.items():
        for rid in info.get("members", []):
            if 0 <= rid < len(ids):
                mapping[ids[rid]] = f"c{cid}"
    for i, uid in enumerate(ids):
        if uid not in mapping:
            mapping[uid] = f"solo{i}"
    return mapping, wall


def _cluster_sizes(mapping: dict[str, str]) -> list[int]:
    counts: dict[str, int] = defaultdict(int)
    for cid in mapping.values():
        counts[cid] += 1
    return list(counts.values())


def _run_stats(
    mapping: dict[str, str], wall: float, reference_max: int | None
) -> RunStats:
    sizes = _cluster_sizes(mapping)
    return RunStats(
        cluster_count=len(sizes),
        multi_record_clusters=sum(1 for s in sizes if s > 1),
        max_cluster_size=max(sizes) if sizes else 0,
        singleton_count=sum(1 for s in sizes if s == 1),
        wall_seconds=wall,
        snowball=snowball_flag(sizes, reference_max),
    )


def _restricted_prf(
    pred: dict[str, str], reference: dict[str, str], ctx: _LeverContext
) -> dict[str, float]:
    """Pairwise P/R/F1 of ``pred`` vs ``reference`` over their shared ids."""
    shared = pred.keys() & reference.keys()
    pred_pairs, pred_capped = pair_set({k: pred[k] for k in shared})
    ref_pairs, ref_capped = pair_set({k: reference[k] for k in shared})
    if pred_capped or ref_capped:
        ctx.report.warn(
            "upgrade:measure",
            f"{pred_capped + ref_capped} cluster(s) over {_PAIR_CAP_MEMBERS} "
            "members were excluded from pairwise-metric pair generation "
            "(quadratic guard)",
            mapped_to=None,
        )
    return pairwise_prf(pred_pairs, ref_pairs)


# ── Entry point ──────────────────────────────────────────────────────────────


def run_measurement(
    ctx: _LeverContext,
    *,
    sampled: bool,
    splink_clusters: pl.DataFrame | str | Path | None = None,
    labels: pl.DataFrame | str | Path | None = None,
    id_column: str | None = None,
) -> MeasurementResult:
    """Measure baseline vs upgraded configs on the pass's sampled data.

    Runs ``dedupe_df`` twice (baseline config as imported, upgraded config
    post-levers), injecting the respective EMResults via temp model files
    when the conversion carried a trained model. Emits per-run shape/wall
    findings; failures propagate to the orchestrator, which downgrades the
    pass to transform-only.
    """
    df = ctx.df
    ids, id_source = _resolve_ids(df, id_column)
    ctx.report.info(
        "upgrade:measure",
        "measurement ids from "
        + ("positional row index" if id_source == _POSITIONAL_ID_SOURCE
           else f"column '{id_source}'"),
        mapped_to=None,
    )

    # Config COPIES for the runs: the MigrationResult's configs must never
    # carry a temp model_path (copy-on-write invariant).
    baseline_cfg = GoldenMatchConfig(**ctx.conversion.config.model_dump())
    upgraded_cfg = GoldenMatchConfig(**ctx.upgraded_config.model_dump())

    tmpdir: str | None = None
    try:
        if ctx.conversion.em_model is not None:
            # Trained input: write BOTH models and point each run config's
            # probabilistic matchkeys at its own file (load_or_train_em loads
            # + validates instead of retraining).
            tmpdir = tempfile.mkdtemp(prefix="gm_splink_upgrade_measure_")
            baseline_path = str(Path(tmpdir) / "model.baseline.json")
            upgraded_path = str(Path(tmpdir) / "model.upgraded.json")
            ctx.conversion.em_model.save_json(baseline_path)
            upgraded_model = ctx.em_model if ctx.em_model is not None else ctx.conversion.em_model
            upgraded_model.save_json(upgraded_path)
            for cfg, path in ((baseline_cfg, baseline_path), (upgraded_cfg, upgraded_path)):
                for mk in cfg.get_matchkeys():
                    if getattr(mk, "type", None) == "probabilistic":
                        mk.model_path = path
        else:
            ctx.report.info(
                "upgrade:measure",
                "no imported model: both measurement runs train EM on the "
                "sample at run time, so the delta reflects config-level "
                "changes only (not imported-model-vs-imported-model)",
                mapped_to=None,
            )

        baseline_map, baseline_wall = _run_once(df, baseline_cfg, ids)
        upgraded_map, upgraded_wall = _run_once(df, upgraded_cfg, ids)
    finally:
        if tmpdir is not None:
            shutil.rmtree(tmpdir, ignore_errors=True)

    # References with ZERO id overlap come back None (warning emitted) so
    # their metrics blocks are absent instead of all-0.0 garbage.
    sample_id_set = set(ids)
    splink_map = (
        _checked_reference(splink_clusters, sample_id_set, "splink_clusters", id_source, ctx)
        if splink_clusters is not None
        else None
    )
    labels_map = (
        _checked_reference(labels, sample_id_set, "labels", id_source, ctx)
        if labels is not None
        else None
    )

    # Snowball reference: splink max when provided, else each run's own p99.
    reference_max = (
        max(_cluster_sizes(splink_map)) if splink_map else None
    )
    baseline_stats = _run_stats(baseline_map, baseline_wall, reference_max)
    upgraded_stats = _run_stats(upgraded_map, upgraded_wall, reference_max)

    for label, stats in (("baseline", baseline_stats), ("upgraded", upgraded_stats)):
        ctx.report.info(
            "upgrade:measure",
            f"{label} run: {stats.cluster_count} clusters "
            f"({stats.multi_record_clusters} multi-record, max size "
            f"{stats.max_cluster_size}, {stats.singleton_count} singletons) "
            f"in {stats.wall_seconds:.3f}s"
            + (" [snowball flag]" if stats.snowball else ""),
            mapped_to=None,
        )

    vs_splink: PairwiseAgreement | None = None
    if splink_map is not None:
        vs_splink = PairwiseAgreement(
            baseline=_restricted_prf(baseline_map, splink_map, ctx),
            upgraded=_restricted_prf(upgraded_map, splink_map, ctx),
        )

    vs_labels: TruthMetrics | None = None
    if labels_map is not None:
        def _truth_metrics(pred: dict[str, str]) -> dict[str, float]:
            pw = _restricted_prf(pred, labels_map, ctx)
            bc = bcubed(pred, labels_map)
            return {
                "pairwise_precision": pw["precision"],
                "pairwise_recall": pw["recall"],
                "pairwise_f1": pw["f1"],
                "bcubed_precision": bc["precision"],
                "bcubed_recall": bc["recall"],
                "bcubed_f1": bc["f1"],
            }

        vs_labels = TruthMetrics(
            baseline=_truth_metrics(baseline_map),
            upgraded=_truth_metrics(upgraded_map),
        )

    if splink_clusters is None and labels is None:
        ctx.report.info(
            "upgrade:measure",
            "shape-only comparison: no splink_clusters or labels reference "
            "was provided, so the delta above has no external ground",
            mapped_to=None,
        )

    return MeasurementResult(
        sample_rows=len(df),
        sampled=sampled,
        baseline=baseline_stats,
        upgraded=upgraded_stats,
        vs_splink=vs_splink,
        vs_labels=vs_labels,
    )
