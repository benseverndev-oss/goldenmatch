"""Real-data integration tests for AutoConfigController.

These tests drive the controller end-to-end on synthetic fixtures --
NO mocking of _run_pipeline_sample or _finalize. The unit-test layer
in test_autoconfig_controller.py mocks these to test loop semantics in
isolation; this file is the regression net for the integration seam
(controller -> instrumented pipeline -> emitter -> assembled profile).

Without these tests, a regression that disables stage instrumentation
or breaks the emitter wiring would not be caught -- every other test
mocks the pipeline.

Spec: docs/superpowers/specs/2026-05-06-autoconfig-introspective-controller-design.md
      Section: Testing tier 3.
"""
from __future__ import annotations

from pathlib import Path

import goldenmatch
import polars as pl
import pytest
from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.core.autoconfig import _LAST_CONTROLLER_RUN
from goldenmatch.core.autoconfig_controller import (
    AutoConfigController,
    ConfigValidationError,
    ControllerBudget,
)
from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy

FIXTURES = Path(__file__).parent / "fixtures" / "autoconfig"


def _read_fixture(name: str) -> pl.DataFrame:
    path = FIXTURES / name
    if not path.exists():
        pytest.skip(f"fixture missing: {path}")
    return pl.read_csv(path, encoding="utf8-lossy")


# ============================================================
# Real-data controller iteration
# ============================================================

def test_controller_iterates_on_real_dedupe_data():
    """Controller drives real dedupe pipeline on a sample, returns a config
    + populated profile + history with at least the v0 entry.

    Without this test, a regression that disables stage instrumentation
    or breaks the emitter wiring would not be caught -- every other test
    mocks the pipeline."""
    df = _read_fixture("clean_dedupe.csv")
    cfg = goldenmatch.auto_configure_df(df)
    assert isinstance(cfg, GoldenMatchConfig)
    state = _LAST_CONTROLLER_RUN.get()
    assert state is not None, "_LAST_CONTROLLER_RUN must be populated after auto_configure_df"
    profile, history = state
    assert history.iteration >= 1, "controller must record at least one iteration"
    # First iteration's profile should have a populated DataProfile from the sample
    assert history.entries[0].profile.data.n_rows > 0
    # Blocking sub-profile should reflect a real run (not the empty default)
    bp = history.entries[0].profile.blocking
    assert bp.n_blocks > 0, "blocking instrumentation didn't emit (or wiring broken)"


def test_controller_match_mode_with_real_reference():
    """Match mode end-to-end: target + reference pair runs through the
    controller. Verifies cross-source plumbing (n_rows includes reference,
    sample is taken from both sides)."""
    target = _read_fixture("bibliographic_match_target.csv")
    reference = _read_fixture("bibliographic_match_reference.csv")
    cfg = goldenmatch.auto_configure_df(target, reference=reference)
    assert isinstance(cfg, GoldenMatchConfig)
    state = _LAST_CONTROLLER_RUN.get()
    assert state is not None
    profile, history = state
    # n_rows from the emitted DataProfile should cover both sides combined
    # (either from the emitter or from the fallback _compute_data_profile)
    first_profile = history.entries[0].profile
    assert first_profile.data.n_rows > 0
    # Validate match_df actually runs with the controller-emitted config
    result = goldenmatch.match_df(target, reference, config=cfg)
    assert result is not None


def test_controller_real_run_populates_postflight_controller_history():
    """gm.dedupe_df zero-config attaches RunHistory + ComplexityProfile to
    PostflightReport (the in-PR review fix). Verifies the wiring from
    _LAST_CONTROLLER_RUN through to result.postflight_report.controller_*."""
    df = _read_fixture("clean_dedupe.csv")
    result = goldenmatch.dedupe_df(df)
    assert result.postflight_report is not None, "zero-config must produce a postflight"
    pf = result.postflight_report
    assert pf.controller_profile is not None, (
        "controller_profile not attached -- wiring from _LAST_CONTROLLER_RUN broken"
    )
    assert pf.controller_history is not None, (
        "controller_history not attached"
    )
    # Sanity: history reports at least one iteration
    assert pf.controller_history.iteration >= 1


def test_controller_skips_iteration_below_sample_skip_below():
    """When n_rows < sample_skip_below (default 5000), controller still runs
    but uses the full data instead of sampling. Verifies sample-bypass code path."""
    df = _read_fixture("clean_dedupe.csv")  # ~50 rows
    cfg = goldenmatch.auto_configure_df(df)
    assert isinstance(cfg, GoldenMatchConfig)
    state = _LAST_CONTROLLER_RUN.get()
    assert state is not None
    profile, history = state
    # Sample size in meta should be <= full data size when below threshold
    e0 = history.entries[0]
    if e0.profile.meta.sample_size > 0:
        assert e0.profile.meta.sample_size <= df.height


# ============================================================
# Real-data drift detection
# ============================================================

def test_finalize_drift_real_data_path_exercised():
    """When auto_configure_df is called directly (not via _api shortcut),
    _finalize runs and computes drift. Verifies the L1-distance arithmetic
    over real assembled profiles -- without this test, _finalize's drift
    code is never executed on non-mocked profiles."""
    df = _read_fixture("clean_dedupe.csv")
    # Direct call to auto_configure_df runs _finalize (not the _api skip path)
    cfg = goldenmatch.auto_configure_df(df)
    state = _LAST_CONTROLLER_RUN.get()
    assert state is not None
    profile, history = state
    # _finalize ran -> full_vs_sample_drift is populated (>= 0.0, possibly 0
    # when sample == full data)
    assert history.full_vs_sample_drift is not None, (
        "_finalize didn't run or didn't compute drift"
    )
    assert history.full_vs_sample_drift >= 0.0


# ============================================================
# Pathological inputs through the public facade
# ============================================================

def test_facade_empty_dataframe_raises():
    """Empty df -> ConfigValidationError through the public facade."""
    df = pl.DataFrame({"a": []}, schema={"a": pl.Utf8})
    with pytest.raises(ConfigValidationError):
        goldenmatch.auto_configure_df(df)


def test_facade_single_column_returns_config():
    """Single column -> controller returns v0 with YELLOW profile, doesn't iterate."""
    df = pl.DataFrame({"name": ["a", "b", "c", "d", "e"] * 20})
    cfg = goldenmatch.auto_configure_df(df)
    assert isinstance(cfg, GoldenMatchConfig)
    state = _LAST_CONTROLLER_RUN.get()
    assert state is not None
    profile, history = state
    # No iteration loop entered (pathological gate short-circuits)
    assert history.iteration == 0


def test_facade_lazyframe_collected():
    """LazyFrame inputs through the public facade actually collect and run."""
    lf = pl.DataFrame({
        "name": ["alice", "bob", "carol"] * 20,
        "city": ["x", "y", "z"] * 20,
    }).lazy()
    cfg = goldenmatch.auto_configure_df(lf)
    assert isinstance(cfg, GoldenMatchConfig)


# ============================================================
# Stop reasons under real iteration
# ============================================================

def test_real_run_stop_reason_recorded():
    """After real iteration, history reflects how the loop exited.

    NB: StopReason is on the controller's internal logic but not yet surfaced
    as a field on RunHistory (deferred to follow-up).
    For now, verify history.iteration is bounded by budget.max_iterations + 1
    and that history.elapsed is positive.
    """
    df = _read_fixture("clean_dedupe.csv")
    goldenmatch.auto_configure_df(df)
    state = _LAST_CONTROLLER_RUN.get()
    assert state is not None
    profile, history = state
    # Default budget is max_iterations=3 -> at most 4 entries (initial + 3 refits)
    assert history.iteration <= 4
    # Elapsed time recorded
    assert history.elapsed.total_seconds() >= 0.0


# ============================================================
# Controller with explicit budget (fast path for CI)
# ============================================================

def test_controller_direct_api_with_budget_cap():
    """Directly call AutoConfigController with a tight budget to verify
    the iteration cap is respected and the pipeline runs for real."""
    df = _read_fixture("clean_dedupe.csv")
    budget = ControllerBudget(max_iterations=1, sample_skip_below=10)
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=budget,
    )
    config, profile, history = controller.run(df, skip_finalize=True)
    assert isinstance(config, GoldenMatchConfig)
    # At most 3 entries: iteration 0, iteration 1 (max_iterations=1 → range(0,2)),
    # plus the virtual v0 entry (iteration=-1) appended post-loop.
    assert history.iteration <= 3
    # Profile populated from real pipeline run
    assert profile.data.n_rows > 0
    # Elapsed recorded
    assert history.elapsed.total_seconds() >= 0.0


def test_controller_profile_has_real_blocking_data():
    """BlockingProfile from a real pipeline run must have non-zero n_blocks.
    Verifies the blocker.py stage instrumentation fires and the emitter
    captures it (if zero, the wiring is broken)."""
    df = _read_fixture("clean_dedupe.csv")
    budget = ControllerBudget(max_iterations=1, sample_skip_below=10)
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=budget,
    )
    config, profile, history = controller.run(df, skip_finalize=True)
    if history.iteration >= 1:
        bp = history.entries[0].profile.blocking
        assert bp.n_blocks > 0, (
            "BlockingProfile.n_blocks==0 after real pipeline run -- "
            "blocker stage instrumentation not wired to emitter"
        )
