"""Phase 1 routing regression: measuring (not extrapolating) flips the backend
on skewed blocking. Spec 2026-06-06 §Phase 1.

This is the durable proof that measure-don't-extrapolate changes the planner's
*decision* — the thing that makes the higher tiers worth their cost. It tests at
the planner-decision level (no full pipeline) so it's fast + deterministic.
"""
from __future__ import annotations

import dataclasses

import polars as pl
from goldenmatch.config.schemas import (
    BlockingConfig,
    BlockingKeyConfig,
    GoldenMatchConfig,
    MatchkeyConfig,
    MatchkeyField,
)
from goldenmatch.core.autoconfig_planner import apply_planner_rules
from goldenmatch.core.autoconfig_planner_rules import DEFAULT_RULES, SIMPLE_PLAN_MAX_PAIRS
from goldenmatch.core.blocker import measure_blocking_profile
from goldenmatch.core.complexity_profile import ComplexityProfile
from goldenmatch.core.runtime_profile import RuntimeProfile

# 40k rows with a 12k dominant block -> ~72M true candidate pairs, while a
# uniform 2k sample extrapolates to ~3.5M. The 50M threshold sits between them.
_N_ROWS = 40_000
_DOMINANT = 12_000
_SAMPLE = 2_000


def _skewed_df() -> pl.DataFrame:
    tail = _N_ROWS - _DOMINANT
    last = ["SMITH"] * _DOMINANT + [f"name{i % (tail // 6)}" for i in range(tail)]
    return pl.DataFrame({"last": last, "email": [f"r{i}@x.com" for i in range(_N_ROWS)]})


def _cfg() -> GoldenMatchConfig:
    return GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(name="m", type="exact", fields=[MatchkeyField(field="email")])],
        blocking=BlockingConfig(keys=[BlockingKeyConfig(fields=["last"])]),
    )


def _plan_rule(blocking_profile) -> str:
    runtime = RuntimeProfile(available_ram_gb=64.0, cpu_count=8, disk_free_gb=500.0)
    profile = dataclasses.replace(ComplexityProfile(), blocking=blocking_profile)
    plan = apply_planner_rules(
        profile=profile, runtime=runtime, n_rows_full=_N_ROWS,
        rules=DEFAULT_RULES, context={"user_backend": None},
    )
    return plan.rule_name


def test_corrected_extrapolation_no_longer_undercounts_below_threshold():
    """S1 (spec 2026-06-22): the corrected ratio**2 extrapolation no longer
    systematically under-counts. On this skewed chunked-rung fixture the full
    measurement is >= 50M, and the corrected sample extrapolation now ALSO lands
    >= 50M -- where the old linear scaling produced an order-of-magnitude
    under-count below the threshold (the bug S1 fixes)."""
    df = _skewed_df()
    cfg = _cfg()
    extrap = measure_blocking_profile(df.sample(_SAMPLE, seed=0), cfg).extrapolate_to(
        n_rows_sample=_SAMPLE, n_rows_full=_N_ROWS
    )
    measured = measure_blocking_profile(df, cfg)
    assert measured.estimated_pair_count >= SIMPLE_PLAN_MAX_PAIRS
    # Corrected extrapolation tracks the measured truth instead of under-counting
    # below it; the old linear path asserted `extrap < 50M <= measured` here.
    assert extrap.estimated_pair_count >= SIMPLE_PLAN_MAX_PAIRS


def test_corrected_extrapolation_routes_to_same_rung_as_measurement():
    """S1 regression (spec 2026-06-22): the corrected fallback extrapolation
    routes a chunked-rung dataset to `chunked`, MATCHING the full-frame
    measurement -- the under-provisioning is fixed at the extrapolation source.

    Pre-S1 the linear under-count routed the same extrapolated signal to
    `simple` (under-provisioned), and only the F2 measurement gate overrode it;
    this pins that the extrapolation path itself no longer mis-routes. (The F2
    gate's tier logic is covered separately below.)
    """
    df = _skewed_df()
    cfg = _cfg()
    extrap = measure_blocking_profile(df.sample(_SAMPLE, seed=0), cfg).extrapolate_to(
        n_rows_sample=_SAMPLE, n_rows_full=_N_ROWS
    )
    measured = measure_blocking_profile(df, cfg)

    assert _plan_rule(extrap) == "plan_selected_chunked"
    assert _plan_rule(measured) == "plan_selected_chunked"


# --------------------------------------------------------------------------- #
# F2 gate (spec 2026-06-22): which tiers measure full-frame blocking.
# --------------------------------------------------------------------------- #
from goldenmatch.core.autoconfig_controller import (  # noqa: E402
    _MEASURE_BLOCKING_MAX_ROWS_LOWER_TIER as _CEIL,
)
from goldenmatch.core.autoconfig_controller import (
    _should_measure_blocking as _gate,
)


def test_f2_normal_tier_now_measures_static_under_ceiling():
    # The common default tier measures (pre-F2 it extrapolated -> under-counted).
    assert _gate(planning_effort="normal", distributed=False, is_static=True, n_rows=1_000_000)
    assert _gate(planning_effort="fast", distributed=False, is_static=True, n_rows=_CEIL)


def test_f2_thinking_einstein_always_measure_even_non_static():
    for eff in ("thinking", "einstein"):
        assert _gate(planning_effort=eff, distributed=False, is_static=False, n_rows=10**9)


def test_f2_lower_tiers_extrapolate_when_fast_path_wont_apply():
    # Non-static at a lower tier would hit the slow build_blocks fallback -> skip.
    assert not _gate(planning_effort="normal", distributed=False, is_static=False, n_rows=1_000)
    # Above the ceiling, even static extrapolates on lower tiers (budget backstop).
    assert not _gate(planning_effort="normal", distributed=False, is_static=True, n_rows=_CEIL + 1)


def test_f2_distributed_never_measures():
    assert not _gate(planning_effort="einstein", distributed=True, is_static=True, n_rows=10)
