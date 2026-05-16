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
    rule_pathological,
    rule_simple_plan,
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


def test_default_rules_phase_3_includes_both():
    """Phase 3's DEFAULT_RULES has rule_pathological first, then
    rule_simple_plan. Phases 4-6 append more rules."""
    rule_names = [r.name for r in DEFAULT_RULES]
    assert rule_names == ["plan_pathological", "plan_selected_simple"]
