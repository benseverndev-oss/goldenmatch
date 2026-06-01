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

import pytest
from goldenmatch.core.autoconfig_planner_rules import (
    DEFAULT_RULES,
    _has_ray,
    auto_chunk_size,
    rule_chunked,
    rule_duckdb,
    rule_fast_box,
    rule_pathological,
    rule_ray,
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

HAS_RAY = _has_ray()


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


@pytest.fixture(autouse=True)
def _native_off(monkeypatch):
    """The simple / fast-box rules pick the bucket backend when the native
    block-scorer is enabled, else polars-direct. Pin it OFF by default so the
    backend assertions below are deterministic regardless of whether the native
    ext happens to be built in the test env. The bucket-branch tests re-enable it."""
    import goldenmatch.core.autoconfig_planner_rules as pr
    monkeypatch.setattr(pr, "native_enabled", lambda component: False)


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


def test_simple_plan_uses_bucket_when_native_enabled(monkeypatch):
    """With the native block-scorer enabled, the simple plan selects the bucket
    backend (measured 1.7-3.7x faster than polars-direct, identical clusters)."""
    import goldenmatch.core.autoconfig_planner_rules as pr
    monkeypatch.setattr(pr, "native_enabled", lambda component: True)
    p = _profile(n_rows=50_000, total_comparisons=1_000_000)
    plan = rule_simple_plan.action(p, _runtime(), 50_000)
    assert plan.backend == "bucket"
    assert plan.rule_name == "plan_selected_simple"


def test_fast_box_plan_uses_bucket_when_native_enabled(monkeypatch):
    import goldenmatch.core.autoconfig_planner_rules as pr
    monkeypatch.setattr(pr, "native_enabled", lambda component: True)
    p = _profile(n_rows=200_000, total_comparisons=1_000_000)
    plan = rule_fast_box.action(p, _runtime(ram_gb=64.0), 200_000)
    assert plan.backend == "bucket"
    assert plan.rule_name == "plan_selected_fast_box"


def test_bucket_opt_out_forces_polars_direct(monkeypatch):
    """GOLDENMATCH_PLANNER_BUCKET=0 forces polars-direct even with native on."""
    import goldenmatch.core.autoconfig_planner_rules as pr
    monkeypatch.setattr(pr, "native_enabled", lambda component: True)
    monkeypatch.setenv("GOLDENMATCH_PLANNER_BUCKET", "0")
    p = _profile(n_rows=50_000, total_comparisons=1_000_000)
    assert rule_simple_plan.action(p, _runtime(), 50_000).backend == "polars-direct"


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


# ── Rule 3b: bucket-suggested ────────────────────────────────────────────────


def test_bucket_suggested_fires_sub32gb_under_750k_when_pairs_fit(monkeypatch):
    # 16GB box, 300k rows, 20M pairs (~1.3GB at 64B/pair, budget 0.5*16=8GB) -> bucket
    import goldenmatch.core.autoconfig_planner_rules as pr
    monkeypatch.setattr(pr, "native_enabled", lambda component: True)
    prof = _profile(total_comparisons=20_000_000)
    rt = _runtime(ram_gb=16.0, cpus=8)
    assert pr._is_bucket_suggested_eligible(prof, rt, n_rows_full=300_000) is True
    plan = pr._bucket_suggested_plan(prof, rt, 300_000)
    assert plan.backend == "bucket"
    assert plan.rule_name == "plan_selected_bucket_suggested"


def test_bucket_suggested_blocked_when_pairs_wont_fit(monkeypatch):
    # NOTE: the existing <50M density cap means rejection only bites on a LOW-RAM
    # box. 4GB box, 49M pairs (~3.1GB) vs budget 0.5*4=2GB -> 3.1 > 2 -> reject.
    import goldenmatch.core.autoconfig_planner_rules as pr
    monkeypatch.setattr(pr, "native_enabled", lambda component: True)
    prof = _profile(total_comparisons=49_000_000)
    rt = _runtime(ram_gb=4.0, cpus=4)
    assert pr._is_bucket_suggested_eligible(prof, rt, n_rows_full=300_000) is False


def test_bucket_suggested_blocked_over_750k():
    import goldenmatch.core.autoconfig_planner_rules as pr
    prof = _profile(total_comparisons=1_000_000)
    rt = _runtime(ram_gb=16.0, cpus=8)
    assert pr._is_bucket_suggested_eligible(prof, rt, n_rows_full=1_000_000) is False


def test_bucket_suggested_not_needed_on_fat_box():
    # >=32GB is already covered by fast_box; bucket_suggested requires sub-32GB.
    import goldenmatch.core.autoconfig_planner_rules as pr
    prof = _profile(total_comparisons=20_000_000)
    rt = _runtime(ram_gb=64.0, cpus=16)
    assert pr._is_bucket_suggested_eligible(prof, rt, n_rows_full=300_000) is False


def test_bucket_suggested_polars_when_native_absent(monkeypatch):
    import goldenmatch.core.autoconfig_planner_rules as pr
    monkeypatch.setattr(pr, "native_enabled", lambda component: False)
    prof = _profile(total_comparisons=20_000_000)
    rt = _runtime(ram_gb=16.0, cpus=8)
    plan = pr._bucket_suggested_plan(prof, rt, 300_000)
    assert plan.backend == "polars-direct"  # _scoring_backend() fallback


def test_default_rules_phase_5_order():
    """Phase 5: user_override first; pathological precedes simple; fast-box,
    chunked, then ray BEFORE duckdb so 50M+ tries ray first and falls
    through to duckdb when ray isn't installed."""
    rule_names = [r.name for r in DEFAULT_RULES]
    assert rule_names == [
        "plan_user_override",
        "plan_pathological",
        "plan_selected_simple",
        "plan_selected_fast_box",
        "plan_selected_chunked",
        "plan_selected_ray",
        "plan_selected_duckdb",
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


# ── Rule 5: DuckDB ──────────────────────────────────────────────────────────


def test_rule_duckdb_fires_when_pairs_exceed_5b():
    """Spec §Rule 5: pair_count >= 5B (regardless of RAM)."""
    p = _profile(n_rows=10_000_000, total_comparisons=6_000_000_000)
    assert rule_duckdb.predicate(p, _runtime(ram_gb=64.0, cpus=16), 10_000_000) is True
    plan = rule_duckdb.action(p, _runtime(ram_gb=64.0, cpus=16), 10_000_000)
    assert plan.backend == "duckdb"
    assert plan.pair_spill_threshold == "duckdb"
    assert plan.clustering_strategy == "partitioned_union_find"
    assert plan.max_workers == 8
    assert plan.rule_name == "plan_selected_duckdb"


def test_rule_duckdb_fires_on_low_ram_even_with_few_pairs():
    """Spec §Rule 5: OR condition -- RAM < 16GB forces DuckDB regardless."""
    p = _profile(n_rows=200_000, total_comparisons=1_000_000)
    assert rule_duckdb.predicate(p, _runtime(ram_gb=8.0), 200_000) is True


def test_rule_duckdb_does_not_fire_at_ram_threshold_with_modest_pairs():
    """16GB RAM (exactly the floor) + < 5B pairs: should not fire."""
    p = _profile(n_rows=1_000_000, total_comparisons=100_000_000)
    assert rule_duckdb.predicate(p, _runtime(ram_gb=16.0), 1_000_000) is False


def test_rule_duckdb_max_workers_caps_at_8():
    """Spec: max_workers = min(cpu_count, 8). Even 64-core box gets 8."""
    p = _profile(n_rows=10_000_000, total_comparisons=6_000_000_000)
    plan = rule_duckdb.action(p, _runtime(ram_gb=64.0, cpus=64), 10_000_000)
    assert plan.max_workers == 8


# ── Rule 6: Ray ─────────────────────────────────────────────────────────────


def test_rule_ray_does_not_fire_below_50m_rows():
    p = _profile(n_rows=10_000_000)
    assert rule_ray.predicate(p, _runtime(), 10_000_000) is False


@pytest.mark.skipif(not HAS_RAY, reason="ray optional dep not installed")
def test_rule_ray_fires_at_50m_when_ray_is_available():
    """Spec §Rule 6: n_rows >= 50M AND ray import succeeds."""
    p = _profile(n_rows=50_000_000)
    assert rule_ray.predicate(p, _runtime(cpus=32), 50_000_000) is True
    plan = rule_ray.action(p, _runtime(cpus=32), 50_000_000)
    assert plan.backend == "ray"
    assert plan.pair_spill_threshold == "disk_per_worker"
    assert plan.clustering_strategy == "streaming_cc"
    assert plan.max_workers == 32  # full cluster cpu count, no cap
    assert plan.rule_name == "plan_selected_ray"


def test_rule_ray_falls_through_to_duckdb_when_ray_unavailable(monkeypatch):
    """Spec §Rule 6 fail-closed: when ``import ray`` raises, predicate
    returns False; dispatcher falls through to rule_duckdb at 50M+."""
    from goldenmatch.core import autoconfig_planner_rules as rules_mod
    from goldenmatch.core.autoconfig_planner import apply_planner_rules

    monkeypatch.setattr(rules_mod, "_has_ray", lambda: False)

    p = _profile(n_rows=50_000_000, total_comparisons=10_000_000_000)
    plan = apply_planner_rules(
        profile=p,
        runtime=_runtime(ram_gb=32.0, cpus=16),
        n_rows_full=50_000_000,
        rules=DEFAULT_RULES,
        context={"user_backend": None},
    )
    # Falls through past rule_ray; rule_duckdb fires because pairs >= 5B.
    assert plan.rule_name == "plan_selected_duckdb"
    assert plan.backend == "duckdb"


def test_rule_ray_picks_up_full_cluster_cpu_count():
    """Spec: max_workers=cpu_count_total_cluster (no min(...) cap)."""
    p = _profile(n_rows=50_000_000)
    plan = rule_ray.action(p, _runtime(cpus=128), 50_000_000)
    assert plan.max_workers == 128


def test_has_ray_helper_returns_bool():
    """``_has_ray`` returns True or False, never raises."""
    assert isinstance(_has_ray(), bool)
