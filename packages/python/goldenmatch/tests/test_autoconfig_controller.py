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
