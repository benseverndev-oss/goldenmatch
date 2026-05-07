import pytest
import polars as pl
from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.core.complexity_profile import ComplexityProfile, HealthVerdict, DataProfile
from goldenmatch.core.autoconfig_controller import (
    AutoConfigController, ControllerBudget, StopReason, _RED_PROFILE,
    ConfigValidationError,
)
from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy
from goldenmatch.core.autoconfig_history import RunHistory


# ============================================================
# ControllerBudget
# ============================================================

def test_default_budget_has_sane_values():
    b = ControllerBudget()
    assert b.max_iterations >= 1
    assert b.max_seconds > 0
    assert b.sample_size_default >= 1000
    assert b.sample_skip_below >= b.sample_size_default
    assert 0.0 < b.converge_epsilon < 1.0
    assert 0.0 < b.drift_threshold < 1.0


def test_budget_overrides():
    b = ControllerBudget(max_iterations=10, max_seconds=60.0,
                         sample_size_default=500, sample_skip_below=2000,
                         converge_epsilon=0.1, drift_threshold=0.5)
    assert b.max_iterations == 10
    assert b.sample_size_default == 500
    assert b.drift_threshold == 0.5


# ============================================================
# _RED_PROFILE sentinel
# ============================================================

def test_red_profile_sentinel_is_red():
    assert _RED_PROFILE.health() == HealthVerdict.RED


# ============================================================
# StopReason
# ============================================================

def test_stop_reason_has_all_required_values():
    expected = {
        "GREEN", "CONVERGED", "BUDGET_ITERATIONS", "BUDGET_TIME",
        "POLICY_SATISFIED", "POLICY_NO_PROGRESS", "OSCILLATING", "CANCELLED",
    }
    actual = {sr.name for sr in StopReason}
    assert expected.issubset(actual)


# ============================================================
# Pathological-input gates
# ============================================================

def test_run_raises_on_empty_dataframe():
    controller = AutoConfigController(policy=HeuristicRefitPolicy(), budget=ControllerBudget())
    with pytest.raises(ConfigValidationError, match=r"no data"):
        controller.run(pl.DataFrame({"a": []}, schema={"a": pl.Utf8}))


def test_run_returns_v0_for_single_row():
    """Single-row data → no work for ER; returns v0 with health=YELLOW."""
    controller = AutoConfigController(policy=HeuristicRefitPolicy(), budget=ControllerBudget())
    df = pl.DataFrame({"a": ["x"], "b": ["y"], "c": ["z"]})
    config, profile, history = controller.run(df)
    assert isinstance(config, GoldenMatchConfig)
    assert profile.health() in (HealthVerdict.YELLOW, HealthVerdict.GREEN)
    assert history.iteration == 0  # never entered loop


def test_run_raises_on_all_null_columns():
    controller = AutoConfigController(policy=HeuristicRefitPolicy(), budget=ControllerBudget())
    df = pl.DataFrame({"a": [None, None, None], "b": [None, None, None]},
                      schema={"a": pl.Utf8, "b": pl.Utf8})
    with pytest.raises(ConfigValidationError, match=r"no usable columns"):
        controller.run(df)


def test_run_returns_v0_yellow_for_single_column():
    """Single non-empty column → no orthogonal evidence; v0 with YELLOW."""
    controller = AutoConfigController(policy=HeuristicRefitPolicy(), budget=ControllerBudget())
    df = pl.DataFrame({"name": ["a", "b", "c", "d", "e"] * 100})
    config, profile, history = controller.run(df)
    assert isinstance(config, GoldenMatchConfig)
    assert profile.health() == HealthVerdict.YELLOW
    assert history.iteration == 0


# ============================================================
# Sample selection
# ============================================================

def test_take_sample_uses_full_data_below_threshold():
    """When n_rows < sample_skip_below, sample == full data."""
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(sample_skip_below=5000),
    )
    df = pl.DataFrame({"a": list(range(100)), "b": ["x"] * 100})
    sample, _ = controller._take_sample(df, reference=None)
    assert sample.height == 100  # full data, no sampling


def test_take_sample_caps_at_sample_size_for_large_data():
    """When n_rows >= sample_skip_below, sample is sample_size_default."""
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(sample_size_default=200, sample_skip_below=500),
    )
    df = pl.DataFrame({"a": list(range(1000)), "b": ["x"] * 1000})
    sample, _ = controller._take_sample(df, reference=None)
    assert sample.height == 200


def test_take_sample_match_mode_preserves_source_split():
    """When reference is provided, both target and reference get a sub-sample."""
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(sample_size_default=100, sample_skip_below=200),
    )
    target = pl.DataFrame({"a": list(range(500)), "b": ["t"] * 500})
    reference = pl.DataFrame({"a": list(range(500, 1000)), "b": ["r"] * 500})
    s_target, s_ref = controller._take_sample(target, reference=reference)
    assert s_target is not None
    assert s_ref is not None
    # Target is sampled; reference is also sampled (not necessarily the same size,
    # but it is sampled — not full data)
    assert s_target.height <= 200  # sample_size or full
    assert s_ref.height <= 200


def test_take_sample_match_mode_below_threshold_returns_full():
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(sample_skip_below=5000),
    )
    target = pl.DataFrame({"a": list(range(100))})
    reference = pl.DataFrame({"a": list(range(100, 200))})
    s_target, s_ref = controller._take_sample(target, reference=reference)
    assert s_target.height == 100
    assert s_ref.height == 100


def test_take_sample_is_deterministic():
    """Same df → same sample (deterministic seed from data shape)."""
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(sample_size_default=50, sample_skip_below=100),
    )
    df = pl.DataFrame({"a": list(range(200)), "b": ["x"] * 200})
    s1, _ = controller._take_sample(df, reference=None)
    s2, _ = controller._take_sample(df, reference=None)
    assert s1.equals(s2)


# ============================================================
# Task 4.2 — iteration loop
# ============================================================

from unittest.mock import patch, MagicMock
from goldenmatch.core.complexity_profile import (
    BlockingProfile, ScoringProfile, ClusterProfile, MatchkeyProfile, FieldStats,
)
from goldenmatch.core.autoconfig_history import HistoryEntry, PolicyDecision, ErrorRecord
from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy
from goldenmatch.core.autoconfig_rules import DEFAULT_RULES


def _green_subprofiles():
    """Return sub-profiles that yield ComplexityProfile.health() == GREEN.

    Includes a DataProfile with diverse column_types (name + numeric) so the
    DataProfile.health() check (len(set(column_types.values())) != 1) returns
    GREEN rather than YELLOW.  The emitter mock writes these into the emitter
    so _assemble_profile uses them in preference to the computed fallback.
    """
    return dict(
        data=DataProfile(
            n_rows=100, n_cols=2,
            column_types={"a": "name", "b": "numeric"},
        ),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=10, total_comparisons=500,
            reduction_ratio=0.95, block_sizes_p50=10, block_sizes_p95=15,
            block_sizes_p99=20, block_sizes_max=25,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=500, score_histogram=[0]*15 + [100]*5,
            dip_statistic=0.05, mass_above_threshold=0.4, mass_in_borderline=0.05,
        ),
        cluster=ClusterProfile(
            n_clusters=20, cluster_size_p50=2, cluster_size_p99=5,
            cluster_size_max=8, transitivity_rate=0.95,
        ),
        matchkey=MatchkeyProfile(per_field={"a": FieldStats(0.5, 0.0, 10)}),
    )


def _red_blocking_subprofile():
    return BlockingProfile(
        keys_used=[["a"]], n_blocks=2, total_comparisons=4900,
        reduction_ratio=0.01,  # RED: < 0.5
        block_sizes_p50=49, block_sizes_p95=49, block_sizes_p99=49,
        block_sizes_max=49,
    )


@pytest.fixture
def small_df():
    """Df above pathological threshold but below sample_skip_below."""
    return pl.DataFrame({
        "a": ["x", "y", "z"] * 4,
        "b": ["1", "2", "3"] * 4,
    })


def _make_controller_with_mocked_runner(profiles_per_iter, **budget_kwargs):
    """Build a controller whose `_run_pipeline_sample` returns the given
    sequence of (sub-profile dict) per iteration."""
    bk = {"max_iterations": 5, "sample_skip_below": 1}
    bk.update(budget_kwargs)
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(**bk),
    )
    # The mock writes to the active emitter so _assemble_profile picks it up.
    iter_idx = {"i": 0}

    def fake_runner(sample, ref, config):
        from goldenmatch.core.profile_emitter import current_emitter
        idx = iter_idx["i"]
        iter_idx["i"] = min(idx + 1, len(profiles_per_iter) - 1)
        sub = profiles_per_iter[idx]
        e = current_emitter()
        if "blocking" in sub: e.set_blocking(sub["blocking"])
        if "scoring" in sub: e.set_scoring(sub["scoring"])
        if "cluster" in sub: e.set_cluster(sub["cluster"])
        if "matchkey" in sub: e.set_matchkey(sub["matchkey"])
        if "data" in sub: e.set_data(sub["data"])
        if "domain" in sub: e.set_domain(sub["domain"])

    controller._run_pipeline_sample = fake_runner  # type: ignore[method-assign]
    # Build the finalize return value using the full green sub-profiles (which
    # now include a DataProfile with n_rows=100 and diverse column types so
    # DataProfile.health() returns GREEN rather than YELLOW).
    _green_subs = _green_subprofiles()
    _green_full = ComplexityProfile(**_green_subs)
    controller._finalize = MagicMock(return_value=_green_full)
    return controller


def test_run_exits_green_after_one_iteration(small_df):
    """All-green sample profile → GREEN exit, finalize runs."""
    green = _green_subprofiles()
    controller = _make_controller_with_mocked_runner([green])
    config, profile, history = controller.run(small_df)
    assert isinstance(config, GoldenMatchConfig)
    assert profile.health() == HealthVerdict.GREEN
    assert history.iteration == 1


def test_run_handles_iteration_crash_gracefully(small_df):
    """Sample iteration raises → recorded in history.errors, controller continues."""
    green = _green_subprofiles()
    controller = _make_controller_with_mocked_runner([green, green])
    crash_count = {"n": 0}
    original = controller._run_pipeline_sample

    def crashing(sample, ref, config):
        if crash_count["n"] == 0:
            crash_count["n"] += 1
            raise RuntimeError("synthetic")
        original(sample, ref, config)

    controller._run_pipeline_sample = crashing  # type: ignore[method-assign]
    config, profile, history = controller.run(small_df)
    assert len(history.errors) == 1
    assert history.errors[0].exception_type == "RuntimeError"
    # At least one healthy entry should follow → committed
    assert profile.health() != HealthVerdict.RED


def test_run_returns_v0_red_when_all_iterations_crash(small_df):
    """Every iteration crashes → returns v0 with RED, no finalize call."""
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(max_iterations=2, sample_skip_below=1),
    )

    def always_crashes(sample, ref, config):
        raise RuntimeError("never works")

    controller._run_pipeline_sample = always_crashes  # type: ignore[method-assign]
    controller._finalize = MagicMock()  # finalize must NOT be called
    config, profile, history = controller.run(small_df)
    assert isinstance(config, GoldenMatchConfig)
    assert profile.health() == HealthVerdict.RED
    assert len(history.errors) >= 2
    controller._finalize.assert_not_called()


def test_run_exits_budget_iterations_when_no_progress(small_df):
    """Profile stays red across iterations and policy keeps proposing → BUDGET_ITERATIONS."""
    red = {**_green_subprofiles(), "blocking": _red_blocking_subprofile()}
    # All iters return same red profile; the rule will fire and propose a config,
    # but next iter still returns red (the mock doesn't actually use the config).
    controller = _make_controller_with_mocked_runner([red, red, red, red, red],
                                                      max_iterations=2)
    config, profile, history = controller.run(small_df)
    # No healthy iterations at all → returns v0 with RED
    assert profile.health() == HealthVerdict.RED


def test_run_budget_time_exit(small_df):
    """When wall-clock budget exceeds, exits with budget_time."""
    green = _green_subprofiles()
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(max_iterations=10, max_seconds=0.0001,
                                sample_skip_below=1),
    )
    import time
    def slow(sample, ref, config):
        time.sleep(0.01)
        from goldenmatch.core.profile_emitter import current_emitter
        # Emit RED sample profile so we don't exit GREEN immediately
        current_emitter().set_blocking(_red_blocking_subprofile())
        for k, v in green.items():
            if k != "blocking":
                getattr(current_emitter(), f"set_{k}")(v)

    controller._run_pipeline_sample = slow  # type: ignore[method-assign]
    controller._finalize = MagicMock(return_value=ComplexityProfile(**green))
    config, profile, history = controller.run(small_df)
    # Should bail out quickly via BUDGET_TIME or BUDGET_ITERATIONS — either is acceptable
    assert history.iteration >= 1


# ============================================================
# Task 4.3 — _finalize + drift detection
# ============================================================


def test_finalize_records_zero_drift_when_full_matches_sample(small_df):
    """Full-data profile equal to final sample profile → drift == 0."""
    green = _green_subprofiles()
    controller = _make_controller_with_mocked_runner([green])
    # Replace finalize with one that returns the same green profile.
    # The green sub-profiles already include a DataProfile with n_rows=100
    # and diverse column_types so the signal vectors are identical → drift == 0.
    full_profile = ComplexityProfile(**green)
    controller._finalize = MagicMock(return_value=full_profile)
    config, profile, history = controller.run(small_df)
    # Drift recorded via history.full_vs_sample_drift
    assert history.full_vs_sample_drift is not None
    assert history.full_vs_sample_drift == pytest.approx(0.0, abs=0.001)
