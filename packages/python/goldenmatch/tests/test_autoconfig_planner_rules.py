"""Unit tests for individual planner rules.

Spec §Decision rules: each rule is a (predicate, action) pair. Tests
construct synthetic profiles + runtimes that should trigger each rule
and assert the resulting ExecutionPlan's backend + rule_name.

NB: This module covers ``autoconfig_planner_rules`` (the controller v3
planner's rule table). The legacy ``autoconfig_rules`` module +
``test_autoconfig_rules`` cover the v1.10/v1.11 HeuristicRefitPolicy
rules -- a different layer.
"""
from __future__ import annotations

from goldenmatch.core.autoconfig_planner_rules import (
    DEFAULT_RULES,
    auto_chunk_size,
    rule_chunked,
    rule_fast_box,
    rule_pathological,
    rule_simple_plan,
    rule_user_override,
)
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ComplexityProfile,
    DataProfile,
    ProfileMeta,
)
from goldenmatch.core.runtime_profile import RuntimeProfile


def _profile(n_rows: int = 1000, total_comparisons: int = 100) -> ComplexityProfile:
    return ComplexityProfile(
        data=DataProfile(n_rows=n_rows, n_cols=3),
        blocking=BlockingProfile(
            keys_used=[["name"]],
            n_blocks=10,
            total_comparisons=total_comparisons,
            reduction_ratio=0.9,
            block_sizes_p50=10,
            block_sizes_p95=15,
            block_sizes_p99=20,
            block_sizes_max=25,
            singleton_block_count=0,
            oversized_block_count=0,
        ),
        meta=ProfileMeta(
            iteration=0,
            is_sample=False,
            sample_size=n_rows,
            n_rows_full=n_rows,
            wall_clock_ms=0,
            seed=0,
        ),
    )


def _runtime(ram_gb: float = 16.0, cpus: int = 4) -> RuntimeProfile:
    return RuntimeProfile(available_ram_gb=ram_gb, cpu_count=cpus, disk_free_gb=100.0)


def test_rule_pathological_fires_at_one_row():
    """Single-row inputs trigger the pathological rule. Defensive -- the
    controller already short-circuits these earlier; the rule exists so
    the planner's decision table reads completely."""
    p = _profile(n_rows=1)
    assert rule_pathological.predicate(p, _runtime(), 1) is True
    plan = rule_pathological.action(p, _runtime(), 1)
    assert plan.backend == "polars-direct"
    assert plan.rule_name == "plan_pathological"


def test_rule_pathological_does_not_fire_on_normal_inputs():
    p = _profile(n_rows=1000)
    assert rule_pathological.predicate(p, _runtime(), 1000) is False


def test_rule_simple_plan_fires_under_100k_rows():
    p = _profile(n_rows=50_000, total_comparisons=1_000_000)
    assert rule_simple_plan.predicate(p, _runtime(), 50_000) is True
    plan = rule_simple_plan.action(p, _runtime(), 50_000)
    assert plan.backend == "polars-direct"
    assert plan.max_workers == 4
    assert plan.clustering_strategy == "in_memory"
    assert plan.rule_name == "plan_selected_simple"


def test_rule_simple_plan_fires_under_50m_pairs_at_99k_rows():
    """Even near the upper-bound row count, low pair count keeps simple plan eligible."""
    p = _profile(n_rows=99_000, total_comparisons=49_000_000)
    assert rule_simple_plan.predicate(p, _runtime(), 99_000) is True


def test_rule_simple_plan_does_not_fire_over_100k_rows():
    p = _profile(n_rows=200_000, total_comparisons=1_000)
    assert rule_simple_plan.predicate(p, _runtime(), 200_000) is False


def test_rule_simple_plan_does_not_fire_over_50m_pairs():
    p = _profile(n_rows=50_000, total_comparisons=51_000_000)
    assert rule_simple_plan.predicate(p, _runtime(), 50_000) is False


def test_default_rules_phase_4_order():
    """Phase 4: user_override MUST be first; pathological precedes simple;
    fast-box / chunked come after. Phase 5 will append duckdb + ray."""
    rule_names = [r.name for r in DEFAULT_RULES]
    assert rule_names == [
        "plan_user_override",
        "plan_pathological",
        "plan_selected_simple",
        "plan_selected_fast_box",
        "plan_selected_chunked",
    ]


# ── Rule 3: fast-box ────────────────────────────────────────────────────────


def test_rule_fast_box_fires_at_500k_with_64gb_and_sparse_pairs():
    """Spec §Rule 3: n_rows >= 100K AND pair_count < 50M AND ram >= 32GB."""
    p = _profile(n_rows=500_000, total_comparisons=40_000_000)
    assert rule_fast_box.predicate(p, _runtime(ram_gb=64.0, cpus=16), 500_000) is True
    plan = rule_fast_box.action(p, _runtime(ram_gb=64.0, cpus=16), 500_000)
    assert plan.backend == "polars-direct"
    assert plan.max_workers == 16
    assert plan.rule_name == "plan_selected_fast_box"


def test_rule_fast_box_does_not_fire_below_32gb_ram():
    p = _profile(n_rows=500_000, total_comparisons=40_000_000)
    assert rule_fast_box.predicate(p, _runtime(ram_gb=16.0), 500_000) is False


def test_rule_fast_box_does_not_fire_when_pairs_too_high():
    """50M+ pairs falls through to rule_chunked, not fast-box."""
    p = _profile(n_rows=500_000, total_comparisons=80_000_000)
    assert rule_fast_box.predicate(p, _runtime(ram_gb=64.0), 500_000) is False


def test_rule_fast_box_max_workers_caps_at_16():
    p = _profile(n_rows=500_000, total_comparisons=40_000_000)
    plan = rule_fast_box.action(p, _runtime(ram_gb=64.0, cpus=64), 500_000)
    assert plan.max_workers == 16


# ── Rule 4: chunked ─────────────────────────────────────────────────────────


def test_rule_chunked_fires_at_2m_with_32gb():
    """Spec §Rule 4: 50M <= pair_count < 5B, ram >= 16GB."""
    p = _profile(n_rows=2_000_000, total_comparisons=200_000_000)
    assert rule_chunked.predicate(p, _runtime(ram_gb=32.0, cpus=16), 2_000_000) is True
    plan = rule_chunked.action(p, _runtime(ram_gb=32.0, cpus=16), 2_000_000)
    assert plan.backend == "chunked"
    assert plan.chunk_size is not None and plan.chunk_size > 0
    assert plan.pair_spill_threshold == "ram"
    assert plan.rule_name == "plan_selected_chunked"


def test_rule_chunked_picks_chunk_size_in_spec_range():
    """auto_chunk_size targets ~60% of available RAM; clamped to [10K, 1M]."""
    p = _profile(n_rows=2_000_000, total_comparisons=200_000_000)
    plan = rule_chunked.action(p, _runtime(ram_gb=32.0, cpus=16), 2_000_000)
    assert plan.chunk_size is not None
    assert 10_000 <= plan.chunk_size <= 1_000_000


def test_rule_chunked_does_not_fire_below_16gb_ram():
    p = _profile(n_rows=2_000_000, total_comparisons=200_000_000)
    assert rule_chunked.predicate(p, _runtime(ram_gb=8.0), 2_000_000) is False


def test_rule_chunked_does_not_fire_when_pairs_exceed_5b():
    """5B+ pairs falls through to rule_duckdb (phase 5)."""
    p = _profile(n_rows=10_000_000, total_comparisons=6_000_000_000)
    assert rule_chunked.predicate(p, _runtime(ram_gb=32.0), 10_000_000) is False


def test_auto_chunk_size_clamps_at_10k_floor():
    """Tiny RAM (1GB) on 100K rows would push chunks below 10K; clamped."""
    assert auto_chunk_size(100_000, available_ram_gb=1.0) >= 10_000


def test_auto_chunk_size_clamps_at_1m_ceiling():
    """Huge RAM on small rows would push chunks above 1M; clamped."""
    assert auto_chunk_size(500_000_000, available_ram_gb=1024.0) <= 1_000_000


# ── Rule 7: user override ───────────────────────────────────────────────────


def test_rule_user_override_fires_when_context_has_backend():
    """Spec §Rule 7: explicit user override beats every other rule. Must
    be FIRST in the registry."""
    p = _profile()
    assert (
        rule_user_override.predicate(
            p, _runtime(), 1000, context={"user_backend": "ray"}
        )
        is True
    )
    plan = rule_user_override.action(p, _runtime(), 1000, context={"user_backend": "ray"})
    assert plan.backend == "ray"
    assert plan.rule_name == "plan_user_override"


def test_rule_user_override_does_not_fire_when_context_is_none():
    p = _profile()
    assert rule_user_override.predicate(p, _runtime(), 1000, context=None) is False


def test_rule_user_override_does_not_fire_when_user_backend_is_none():
    p = _profile()
    assert (
        rule_user_override.predicate(p, _runtime(), 1000, context={"user_backend": None})
        is False
    )


def test_rule_user_override_fills_chunk_size_for_chunked_backend():
    """User says chunked -- planner fills chunk_size + other knobs."""
    p = _profile(n_rows=2_000_000)
    plan = rule_user_override.action(
        p, _runtime(ram_gb=32.0, cpus=16), 2_000_000,
        context={"user_backend": "chunked"},
    )
    assert plan.backend == "chunked"
    assert plan.chunk_size is not None and plan.chunk_size > 0


def test_rule_user_override_fires_first_in_dispatcher():
    """End-to-end: even when conditions favor rule_simple_plan, the
    override still wins because it sits at position [0] of DEFAULT_RULES."""
    from goldenmatch.core.autoconfig_planner import apply_planner_rules

    p = _profile(n_rows=1000, total_comparisons=100)
    plan = apply_planner_rules(
        profile=p,
        runtime=_runtime(),
        n_rows_full=1000,
        rules=DEFAULT_RULES,
        context={"user_backend": "duckdb"},
    )
    assert plan.rule_name == "plan_user_override"
    assert plan.backend == "duckdb"
