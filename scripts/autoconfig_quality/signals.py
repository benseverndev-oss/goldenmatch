"""FAST tier: config-quality signals (no full dedupe).

Runs the auto-config decision path on a df and records the signals that have
historically regressed (classification, exact matchkeys, blocking fields +
cost, planner rung). Deterministic and fast (seconds): only profiling +
blocking + matchkey selection, NOT the controller's iterative sample-dedupes.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import polars as pl
from goldenmatch.core.autoconfig import build_blocking, build_matchkeys, profile_columns
from goldenmatch.core.autoconfig_planner import apply_planner_rules
from goldenmatch.core.autoconfig_planner_rules import DEFAULT_RULES
from goldenmatch.core.blocker import measure_blocking_profile
from goldenmatch.core.complexity_profile import ComplexityProfile
from goldenmatch.core.runtime_profile import capture_runtime_profile


def extract_signals(df: pl.DataFrame) -> dict[str, Any]:
    """Extract the fast config-quality signals for one dataset."""
    profiles = profile_columns(df)
    classification = {p.name: p.col_type for p in profiles}

    matchkeys = build_matchkeys(profiles, df)
    exact_matchkeys = sorted(
        {f.field for mk in matchkeys if mk.type == "exact" for f in mk.fields if f.field}
    )

    blocking = build_blocking(profiles, df, n_rows_full=df.height)
    fields: set[str] = set()
    for k in (blocking.keys or []):
        fields.update(k.fields)
    for p in (blocking.passes or []):
        fields.update(p.fields)

    # measure_blocking_profile reads config.blocking -> wrap in a namespace.
    bp = measure_blocking_profile(df, SimpleNamespace(blocking=blocking))
    if bp is not None:
        blocking_cost = {
            "candidate_pairs": bp.estimated_pair_count,
            "n_blocks": bp.n_blocks,
            "max_block": bp.block_sizes_max,
            "p99": bp.block_sizes_p99,
            "reduction_ratio": round(bp.reduction_ratio, 4),
        }
    else:
        blocking_cost = {"candidate_pairs": None, "error": "measure_returned_none"}

    cp = ComplexityProfile(blocking=bp) if bp is not None else ComplexityProfile()
    plan = apply_planner_rules(cp, capture_runtime_profile(), df.height, DEFAULT_RULES)

    return {
        "classification": classification,
        "exact_matchkeys": exact_matchkeys,
        "blocking_fields": sorted(fields),
        "blocking_cost": blocking_cost,
        "planner_rung": {"backend": plan.backend, "rule_name": plan.rule_name},
    }
