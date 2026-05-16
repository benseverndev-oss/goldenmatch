"""Serialize AutoConfigController output (ComplexityProfile + RunHistory) into
the JSON shape consumed by ``/api/v1/controller/telemetry`` and the workbench
ControllerPanel.

The engine's controller artifacts are frozen dataclasses with deep typed
sub-profiles (DataProfile, BlockingProfile, ScoringProfile, ClusterProfile,
IndicatorsProfile, …). Rather than letting FastAPI's default
``jsonable_encoder`` walk those structures (which would surface internal
fields like ``_version`` and ``score_histogram`` raw), we project a small,
intentional subset that maps 1:1 to what the UI renders.

Spec inputs:
  - ``goldenmatch.core.complexity_profile.ComplexityProfile`` (v1.10 adds
    ``IndicatorsProfile``; ``DataProfile.column_priors`` carries ColumnPrior).
  - ``goldenmatch.core.autoconfig_history.RunHistory`` (.entries, .decisions,
    .errors, .stop_reason, .full_vs_sample_drift, .elapsed).
  - ``goldenmatch.config.schemas.GoldenMatchConfig`` for committed NE fields.
"""
from __future__ import annotations

from typing import Any


def _health_str(profile: Any) -> str | None:
    """Return the profile's overall health verdict as ``green|yellow|red``.

    Returns None when ``profile`` is None or doesn't expose ``health()`` —
    keeps the response schema stable when the controller never ran.
    """
    if profile is None:
        return None
    try:
        return profile.health().value
    except Exception:
        return None


def _scoring_summary(profile: Any) -> dict[str, Any] | None:
    """Surface the scoring sub-profile signals the workbench renders.

    Skip the full 20-bucket histogram — too noisy for a top-level panel. If
    the user wants it, ``/api/v1/runs/{name}/clusters`` already lets them
    drill down into actual pairs.
    """
    if profile is None or not hasattr(profile, "scoring"):
        return None
    s = profile.scoring
    return {
        "n_pairs_scored": int(getattr(s, "n_pairs_scored", 0)),
        "candidates_compared": int(getattr(s, "candidates_compared", 0)),
        "mass_above_threshold": float(getattr(s, "mass_above_threshold", 0.0)),
        "mass_in_borderline": float(getattr(s, "mass_in_borderline", 0.0)),
        "dip_statistic": float(getattr(s, "dip_statistic", 0.0)),
    }


def _blocking_summary(profile: Any) -> dict[str, Any] | None:
    if profile is None or not hasattr(profile, "blocking"):
        return None
    b = profile.blocking
    return {
        "n_blocks": int(getattr(b, "n_blocks", 0)),
        "reduction_ratio": float(getattr(b, "reduction_ratio", 0.0)),
        "block_sizes_p50": int(getattr(b, "block_sizes_p50", 0)),
        "block_sizes_p99": int(getattr(b, "block_sizes_p99", 0)),
        "block_sizes_max": int(getattr(b, "block_sizes_max", 0)),
        "oversized_block_count": int(getattr(b, "oversized_block_count", 0)),
        "keys_used": [list(k) for k in getattr(b, "keys_used", [])],
    }


def _cluster_summary(profile: Any) -> dict[str, Any] | None:
    if profile is None or not hasattr(profile, "cluster"):
        return None
    c = profile.cluster
    return {
        "n_clusters": int(getattr(c, "n_clusters", 0)),
        "cluster_size_p50": int(getattr(c, "cluster_size_p50", 0)),
        "cluster_size_p99": int(getattr(c, "cluster_size_p99", 0)),
        "cluster_size_max": int(getattr(c, "cluster_size_max", 0)),
        "transitivity_rate": float(getattr(c, "transitivity_rate", 0.0)),
        "oversized_cluster_count": int(getattr(c, "oversized_cluster_count", 0)),
    }


def _column_priors(profile: Any) -> list[dict[str, Any]]:
    """v1.10 ColumnPrior values, one row per column with a non-zero signal.

    Eager indicator output — always populated when the controller ran. Used by
    the workbench to explain WHY auto-config picked / refused certain columns
    as identity anchors.
    """
    if profile is None or not hasattr(profile, "data"):
        return []
    data = profile.data
    priors = getattr(data, "column_priors", None) or {}
    out: list[dict[str, Any]] = []
    for col, prior in priors.items():
        identity = float(getattr(prior, "identity_score", 0.0))
        corruption = float(getattr(prior, "corruption_score", 0.0))
        # Skip columns with no usable signal in either direction — keeps the
        # panel focused on columns where the controller actually had a view.
        if identity == 0.0 and corruption == 0.0:
            continue
        out.append({
            "column": col,
            "identity_score": identity,
            "corruption_score": corruption,
        })
    # Highest-identity columns first, then highest-corruption — matches how
    # the controller prioritises them.
    out.sort(key=lambda r: (-r["identity_score"], -r["corruption_score"]))
    return out


def _indicators(profile: Any) -> dict[str, Any] | None:
    """v1.10 lazy IndicatorsProfile, when the controller computed it.

    Returns None when ``indicators is None`` (the cheap path on YELLOW
    profiles where no expensive indicator was needed).
    """
    ind = getattr(profile, "indicators", None) if profile else None
    if ind is None:
        return None
    return {
        "full_pop_matchkey_hit_rate": (
            float(ind.full_pop_matchkey_hit_rate)
            if ind.full_pop_matchkey_hit_rate is not None
            else None
        ),
        "cross_blocking_overlap": (
            float(ind.cross_blocking_overlap)
            if ind.cross_blocking_overlap is not None
            else None
        ),
    }


def _decisions(history: Any) -> list[dict[str, Any]]:
    """RunHistory.decisions projected into a flat ordered list for rendering.

    Includes iteration index from the parent HistoryEntry so the UI can show
    "iter 2: rule_blocking_key_swap fired because …".
    """
    if history is None:
        return []
    out: list[dict[str, Any]] = []
    for entry in getattr(history, "entries", []):
        if entry.decision is None:
            continue
        diff = entry.decision.config_diff or {}
        # Truncate config_diff to keys+brief repr — the full diff can carry
        # whole MatchkeyConfig objects which are too noisy for a side panel.
        diff_summary = {k: _truncate_repr(v) for k, v in diff.items()}
        out.append({
            "iteration": int(entry.iteration),
            "rule_name": entry.decision.rule_name,
            "rationale": entry.decision.rationale,
            "config_diff": diff_summary,
            "wall_clock_ms": int(getattr(entry, "wall_clock_ms", 0) or 0),
        })
    return out


def _truncate_repr(value: Any, max_len: int = 140) -> str:
    """One-line ``repr`` truncated for compact UI rendering."""
    r = repr(value)
    return r if len(r) <= max_len else r[: max_len - 1] + "…"


def _errors(history: Any) -> list[dict[str, Any]]:
    if history is None:
        return []
    out: list[dict[str, Any]] = []
    for entry in getattr(history, "entries", []):
        if entry.error is None:
            continue
        out.append({
            "iteration": int(entry.iteration),
            "exception_type": entry.error.exception_type,
            "traceback_summary": entry.error.traceback_summary,
        })
    return out


def _negative_evidence(config: Any) -> list[dict[str, Any]]:
    """v1.11+ negative-evidence fields the controller committed, per matchkey.

    Exposed so the workbench's controller panel can flag "Path Y is active on
    matchkey X via field Y (penalty Z)" — the v1.12 unlock that drove the
    DQbench T3 improvement. Empty list when no NE fields exist on any
    matchkey (legacy configs, or zero-config decided no anchor was strong
    enough to back NE).
    """
    if config is None:
        return []
    out: list[dict[str, Any]] = []
    try:
        matchkeys = config.get_matchkeys()
    except Exception:
        return []
    for mk in matchkeys:
        ne = getattr(mk, "negative_evidence", None)
        if not ne:
            continue
        for field in ne:
            out.append({
                "matchkey_name": mk.name,
                "matchkey_type": mk.type,
                "field": field.field,
                "scorer": field.scorer,
                "threshold": float(field.threshold),
                "penalty": float(field.penalty),
                "transforms": list(field.transforms or []),
            })
    return out


def _committed_matchkeys_summary(config: Any) -> list[dict[str, Any]]:
    """One-line-per-matchkey projection of the controller-committed config.

    Workbench renders this so the user can see what auto-config picked
    without having to dig into the saved YAML. Mirrors the format that
    ``/api/v1/rules`` returns but reflects the committed (engine-side) view
    rather than the workbench's flattened RulesPayload.
    """
    if config is None:
        return []
    out: list[dict[str, Any]] = []
    try:
        matchkeys = config.get_matchkeys()
    except Exception:
        return []
    for mk in matchkeys:
        out.append({
            "name": mk.name,
            "type": mk.type,
            "threshold": float(mk.threshold) if mk.threshold is not None else None,
            "fields": [
                {
                    "column": f.column or f.field,
                    "scorer": f.scorer,
                    "weight": float(f.weight) if f.weight is not None else None,
                }
                for f in mk.fields
            ],
            "has_negative_evidence": bool(mk.negative_evidence),
        })
    return out


def serialize_telemetry(
    *,
    profile: Any,
    history: Any,
    committed_config: Any,
    source: str | None,
    run_name: str | None,
    recorded_at: str | None,
) -> dict[str, Any]:
    """Build the JSON body returned by ``GET /api/v1/controller/telemetry``."""
    elapsed_ms: float | None = None
    drift: float | None = None
    stop_reason: str | None = None
    if history is not None:
        elapsed = getattr(history, "elapsed", None)
        if elapsed is not None:
            elapsed_ms = elapsed.total_seconds() * 1000.0
        drift_val = getattr(history, "full_vs_sample_drift", None)
        drift = float(drift_val) if drift_val is not None else None
        sr = getattr(history, "stop_reason", None)
        stop_reason = sr.value if sr is not None else None

    return {
        "available": profile is not None or history is not None,
        "source": source,
        "run_name": run_name,
        "recorded_at": recorded_at,
        "stop_reason": stop_reason,
        "elapsed_ms": elapsed_ms,
        "full_vs_sample_drift": drift,
        "health": _health_str(profile),
        "scoring": _scoring_summary(profile),
        "blocking": _blocking_summary(profile),
        "cluster": _cluster_summary(profile),
        "indicators": _indicators(profile),
        "column_priors": _column_priors(profile),
        "decisions": _decisions(history),
        "errors": _errors(history),
        "execution_plan": _execution_plan(history),
        "committed_matchkeys": _committed_matchkeys_summary(committed_config),
        "negative_evidence": _negative_evidence(committed_config),
    }


def _execution_plan(history: Any) -> dict[str, Any] | None:
    """Serialize the controller v3 ExecutionPlan, or None when absent.

    Pulls from ``history.execution_plan`` (populated by
    ``AutoConfigController.run``). Returns None for legacy histories or
    hand-written configs that bypass the controller.
    """
    if history is None:
        return None
    plan = getattr(history, "execution_plan", None)
    if plan is None:
        return None
    return {
        "rule_name": plan.rule_name,
        "backend": plan.backend,
        "chunk_size": plan.chunk_size,
        "max_workers": plan.max_workers,
        "pair_spill_threshold": plan.pair_spill_threshold,
        "clustering_strategy": plan.clustering_strategy,
    }
