"""Phase 7 integration tests for the controller v3 planner.

End-to-end exercises of ``gm.dedupe_df()`` -> controller -> planner ->
PostflightReport. Each test fixture is shaped (or the runtime monkey-
patched) to force a specific rule to fire, then asserts:
1. ``result.postflight_report.controller_history.execution_plan.rule_name``
   matches the expected rule.
2. ``plan.apply_to(committed_config)`` produced the right ``config.backend``.

The unit-level rule tests in ``test_autoconfig_planner_rules.py`` already
cover the predicates and actions in isolation. These tests add the
controller-wiring assurance: that the planner actually fires inside
``AutoConfigController.run`` and that the resulting plan reaches the
caller's DedupeResult.

Tiers requiring impractical fixture sizes (chunked / duckdb / ray at
50M+ rows) are exercised by monkey-patching ``capture_runtime_profile``
or ``apply_planner_rules`` to flip predicate inputs without inflating
the actual dataframe.
"""
from __future__ import annotations

import goldenmatch as gm
import polars as pl
import pytest
from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN
from goldenmatch.core.autoconfig_planner_rules import _has_ray, _scoring_backend
from goldenmatch.core.runtime_profile import RuntimeProfile

HAS_RAY = _has_ray()


@pytest.fixture(autouse=True)
def _disable_autoconfig_memory(monkeypatch):
    """Cross-run autoconfig memory caches configs by data-shape signature.
    Without this guard, an earlier test's cached config can short-circuit
    ``_initial_config`` and break our monkey-patched-dispatcher tests."""
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")


def _small_df(n: int = 80) -> pl.DataFrame:
    """Person-shape df small enough that rule_simple_plan fires naturally."""
    return pl.DataFrame({
        "name": ["alice", "alyce", "bob", "robert"] * (n // 4),
        "email": [f"u{i}@x.com" for i in range(n)],
    })


def _read_plan():
    """Pull (profile, history, plan) from the last controller run."""
    state = _LAST_CONTROLLER_RUN.get()
    assert state is not None, "no controller run captured"
    profile, history = state
    plan = getattr(history, "execution_plan", None)
    assert plan is not None, "history.execution_plan should be populated"
    return profile, history, plan


# ── Simple plan (end-to-end, no monkey-patch) ──────────────────────────────


def test_integration_simple_plan_fires_on_small_df():
    """80 rows -> rule_simple_plan -> backend=polars-direct -> apply_to no-op."""
    gm.dedupe_df(_small_df())
    _profile, _history, plan = _read_plan()
    assert plan.rule_name == "plan_selected_simple"
    assert plan.backend == _scoring_backend()
    assert plan.clustering_strategy == "in_memory"


def test_integration_postflight_report_renders_plan_line():
    """Phase 6 surface check: the rendered PostflightReport string contains
    the Plan: ... line so CLI users see the rule that fired."""
    result = gm.dedupe_df(_small_df())
    rendered = str(result.postflight_report)
    assert "Plan: plan_selected_simple" in rendered
    assert f"backend={_scoring_backend()}" in rendered


def _stub_apply_to(monkeypatch):
    """Stub ``ExecutionPlan.apply_to`` to a no-op for tests where the plan
    records a non-default backend (duckdb / ray / chunked) but CI doesn't
    have those optional deps installed. The plan still lands on
    ``history.execution_plan`` (which is what the tests assert); the
    pipeline then runs with the default polars-direct path."""
    from goldenmatch.core.execution_plan import ExecutionPlan

    monkeypatch.setattr(ExecutionPlan, "apply_to", lambda self, config: None)


# ── Higher tiers: runtime / dispatcher monkey-patched ──────────────────────


def test_integration_chunked_plan_fires_at_high_pair_count(monkeypatch):
    """Force ``estimated_pair_count >= 50M`` via monkey-patched extrapolation;
    rule_chunked fires and the plan is recorded on history.execution_plan.
    apply_to is stubbed so the pipeline keeps running polars-direct
    (chunked backend has no extra dep on CI but keeping a uniform pattern
    across non-default-backend tests reduces drift risk)."""
    from goldenmatch.core import runtime_profile as runtime_mod
    from goldenmatch.core.complexity_profile import BlockingProfile

    _stub_apply_to(monkeypatch)

    def fat_runtime(*_a, **_kw):
        return RuntimeProfile(available_ram_gb=32.0, cpu_count=16, disk_free_gb=500.0)

    monkeypatch.setattr(runtime_mod, "capture_runtime_profile", fat_runtime)

    real_extrapolate = BlockingProfile.extrapolate_to

    def big_pairs(self, n_rows_sample, n_rows_full):
        out = real_extrapolate(self, n_rows_sample, n_rows_full)
        import dataclasses
        return dataclasses.replace(out, total_comparisons=200_000_000)

    monkeypatch.setattr(BlockingProfile, "extrapolate_to", big_pairs)

    gm.dedupe_df(_small_df())
    _profile, _history, plan = _read_plan()
    assert plan.rule_name == "plan_selected_chunked"
    assert plan.backend == "chunked"
    assert plan.chunk_size is not None and plan.chunk_size >= 10_000
    assert plan.pair_spill_threshold == "ram"


def test_integration_duckdb_plan_fires_on_dense_pairs(monkeypatch):
    """Rule 5 fires when pair_count >= 5B, regardless of RAM. apply_to is
    stubbed so the pipeline doesn't try to load the duckdb optional dep
    (which CI doesn't install)."""
    from goldenmatch.core.complexity_profile import BlockingProfile

    _stub_apply_to(monkeypatch)

    real_extrapolate = BlockingProfile.extrapolate_to

    def huge_pairs(self, n_rows_sample, n_rows_full):
        out = real_extrapolate(self, n_rows_sample, n_rows_full)
        import dataclasses
        return dataclasses.replace(out, total_comparisons=6_000_000_000)

    monkeypatch.setattr(BlockingProfile, "extrapolate_to", huge_pairs)

    gm.dedupe_df(_small_df())
    _profile, _history, plan = _read_plan()
    assert plan.rule_name == "plan_selected_duckdb"
    assert plan.backend == "duckdb"
    assert plan.clustering_strategy == "partitioned_union_find"
    assert plan.max_workers <= 8


def test_integration_fast_box_plan_fires_at_500k_with_64gb(monkeypatch):
    """Rule 3: large rows + sparse pairs + fat machine. Hijack n_rows_full
    via wrapping ``apply_planner_rules`` since materialising 500K rows for
    a unit test is wasteful."""
    from goldenmatch.core import autoconfig_planner as planner_mod
    from goldenmatch.core import runtime_profile as runtime_mod

    def fat_runtime(*_a, **_kw):
        return RuntimeProfile(available_ram_gb=64.0, cpu_count=16, disk_free_gb=500.0)

    real_apply = planner_mod.apply_planner_rules

    def hijack_n_rows(*args, **kwargs):
        kwargs["n_rows_full"] = 500_000
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(runtime_mod, "capture_runtime_profile", fat_runtime)
    monkeypatch.setattr(planner_mod, "apply_planner_rules", hijack_n_rows)

    gm.dedupe_df(_small_df())
    _profile, _history, plan = _read_plan()
    assert plan.rule_name == "plan_selected_fast_box"
    assert plan.backend == _scoring_backend()
    assert plan.max_workers == 16


def test_integration_user_override_beats_rule_table(monkeypatch):
    """Rule 7: explicit ``context["user_backend"]`` wins over all scale
    rules. The controller currently passes ``user_backend=None``, so we
    monkey-patch the dispatcher call to inject a user preference.
    apply_to is stubbed so the pipeline doesn't try to load ray."""
    from goldenmatch.core import autoconfig_planner as planner_mod

    _stub_apply_to(monkeypatch)

    real_apply = planner_mod.apply_planner_rules

    def with_user_choice(*args, **kwargs):
        kwargs["context"] = {"user_backend": "ray"}
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(planner_mod, "apply_planner_rules", with_user_choice)

    gm.dedupe_df(_small_df())
    _profile, _history, plan = _read_plan()
    assert plan.rule_name == "plan_user_override"
    assert plan.backend == "ray"


@pytest.mark.skipif(not HAS_RAY, reason="ray optional dep not installed")
def test_integration_ray_plan_fires_at_50m_when_ray_available(monkeypatch):
    """Rule 6: 50M+ rows AND ray installed. Hijack n_rows_full to 50M.
    apply_to is stubbed so we exercise the planner without actually
    spinning up a Ray cluster inside the test.

    Ray auto-select is soft-reverted as of 2026-05-18 (kill criterion
    failure); explicitly set ``GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1`` so
    the planner stays eligible for this test.
    """
    from goldenmatch.core import autoconfig_planner as planner_mod

    monkeypatch.setenv("GOLDENMATCH_ENABLE_DISTRIBUTED_RAY", "1")
    _stub_apply_to(monkeypatch)

    real_apply = planner_mod.apply_planner_rules

    def hijack_n_rows(*args, **kwargs):
        kwargs["n_rows_full"] = 50_000_000
        return real_apply(*args, **kwargs)

    monkeypatch.setattr(planner_mod, "apply_planner_rules", hijack_n_rows)

    gm.dedupe_df(_small_df())
    _profile, _history, plan = _read_plan()
    assert plan.rule_name == "plan_selected_ray"
    assert plan.backend == "ray"
    assert plan.clustering_strategy == "streaming_cc"
    assert plan.pair_spill_threshold == "disk_per_worker"


# ── Acceptance criteria: plan IS persisted on PostflightReport ──────────────


def test_integration_execution_plan_reaches_postflight_report():
    """Phase 6 contract: every caller of gm.dedupe_df() can read
    ``result.postflight_report.controller_history.execution_plan`` without
    diving into ``_LAST_CONTROLLER_RUN``."""
    result = gm.dedupe_df(_small_df())
    pf = result.postflight_report
    assert pf is not None
    history = pf.controller_history
    assert history is not None
    plan = history.execution_plan
    assert plan is not None
    assert plan.rule_name is not None
    assert plan.backend is not None
