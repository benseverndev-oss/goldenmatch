import pytest
import polars as pl
from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.core.complexity_profile import ComplexityProfile, HealthVerdict, DataProfile
from goldenmatch.core.autoconfig_controller import (
    AutoConfigController, ControllerBudget, _RED_PROFILE,
    ConfigValidationError, _LAST_CONTROLLER_RUN,
)
from goldenmatch.core.complexity_profile import StopReason
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


def _make_controller_with_mocked_runner(profiles_per_iter, policy=None, **budget_kwargs):
    """Build a controller whose `_run_pipeline_sample` returns the given
    sequence of (sub-profile dict) per iteration."""
    bk = {"max_iterations": 5, "sample_skip_below": 1}
    bk.update(budget_kwargs)
    controller = AutoConfigController(
        policy=policy if policy is not None else HeuristicRefitPolicy(),
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
    """All-green sample profile → GREEN exit, finalize runs.

    v1.9 amendment: after the loop, a virtual v0 entry (iteration=-1) is
    appended, so total entries == 2 (one real iter + one virtual v0).
    """
    green = _green_subprofiles()
    controller = _make_controller_with_mocked_runner([green])
    config, profile, history = controller.run(small_df)
    assert isinstance(config, GoldenMatchConfig)
    assert profile.health() == HealthVerdict.GREEN
    # Real iterations: 1 (iter-0 GREEN + break). Virtual v0 appended → total entries = 2.
    real_iters = [e for e in history.entries if e.iteration >= 0]
    assert len(real_iters) == 1


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
    """Profile stays RED across iterations and policy keeps proposing.

    v1.9 change: pick_committed() returns the best RED entry instead of None,
    so the controller commits a RED config (from the sample profile in _finalize
    mock) rather than falling back to v0. The returned profile is the _finalize
    mock value (GREEN from _make_controller_with_mocked_runner), which may differ
    from the RED sample profile — the important invariant is that a config was
    committed and stop_reason is set appropriately.
    """
    red = {**_green_subprofiles(), "blocking": _red_blocking_subprofile()}
    # All iters return same red profile; the rule will fire and propose a config,
    # but next iter still returns red (the mock doesn't actually use the config).
    controller = _make_controller_with_mocked_runner([red, red, red, red, red],
                                                      max_iterations=2)
    config, profile, history = controller.run(small_df)
    # v1.9: committed the best RED entry — config is real (not v0 fallback from
    # all-errored path), stop_reason is recorded.
    assert isinstance(config, GoldenMatchConfig)
    assert history.stop_reason is not None
    # At least one iteration produced a profile (entries exist)
    assert history.iteration >= 1


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
    assert history.stop_reason in (StopReason.BUDGET_TIME, StopReason.BUDGET_ITERATIONS)


def test_stop_reason_budget_time_exit(small_df):
    """Wall-clock budget exhaustion → stop_reason=BUDGET_TIME.

    Uses a slow runner (10ms sleep per iteration) and a 0.1ms wall-clock
    budget so the time guard fires before the second iteration starts.
    """
    import time

    red = _red_blocking_subprofile_dict()
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(max_iterations=10, max_seconds=0.0001,
                                sample_skip_below=1),
    )

    def slow_red_runner(sample, ref, config):
        time.sleep(0.01)
        from goldenmatch.core.profile_emitter import current_emitter
        e = current_emitter()
        if "blocking" in red: e.set_blocking(red["blocking"])
        if "scoring" in red: e.set_scoring(red["scoring"])
        if "cluster" in red: e.set_cluster(red["cluster"])
        if "matchkey" in red: e.set_matchkey(red["matchkey"])
        if "data" in red: e.set_data(red["data"])

    _green_subs = _green_subprofiles()
    controller._run_pipeline_sample = slow_red_runner  # type: ignore[method-assign]
    controller._finalize = MagicMock(return_value=ComplexityProfile(**_green_subs))
    config, profile, history = controller.run(small_df)
    assert history.stop_reason == StopReason.BUDGET_TIME


def test_stop_reason_policy_no_progress_when_policy_returns_same_config(small_df):
    """Policy proposing the same config → stop_reason=POLICY_NO_PROGRESS."""
    class _NoProgressPolicy:
        def propose(self, profile, config, history):
            return config  # same instance — equal to config_n

    red = _red_blocking_subprofile_dict()
    controller = _make_controller_with_mocked_runner(
        [red, red, red],
        max_iterations=5,
        policy=_NoProgressPolicy(),
    )
    config, profile, history = controller.run(small_df)
    assert history.stop_reason == StopReason.POLICY_NO_PROGRESS


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


# ============================================================
# Fix 2 — KeyboardInterrupt propagates
# ============================================================

def test_keyboard_interrupt_propagates(small_df):
    """Ctrl-C inside a pipeline iteration must bubble out, not be swallowed."""
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(sample_skip_below=1),
    )

    def interrupting(sample, ref, config):
        raise KeyboardInterrupt

    controller._run_pipeline_sample = interrupting  # type: ignore[method-assign]
    with pytest.raises(KeyboardInterrupt):
        controller.run(small_df)


# ============================================================
# Fix 4 — skip_finalize skips _finalize call
# ============================================================

def test_skip_finalize_does_not_call_finalize(small_df):
    """When skip_finalize=True and a healthy iteration exists, _finalize is NOT called."""
    green = _green_subprofiles()
    controller = _make_controller_with_mocked_runner([green])
    # _finalize is already a MagicMock from _make_controller_with_mocked_runner
    config, profile, history = controller.run(small_df, skip_finalize=True)
    controller._finalize.assert_not_called()
    assert isinstance(config, GoldenMatchConfig)
    # Profile returned is the sample profile (not full-data); drift not computed.
    assert history.full_vs_sample_drift is None


def test_skip_finalize_false_still_calls_finalize(small_df):
    """Default (skip_finalize=False) still calls _finalize as before."""
    green = _green_subprofiles()
    controller = _make_controller_with_mocked_runner([green])
    controller.run(small_df, skip_finalize=False)
    controller._finalize.assert_called_once()


# ============================================================
# Fix 6 — Warning logged on all-RED fallback
# ============================================================

import logging as _logging


def test_run_returns_v0_red_when_all_iterations_crash_logs_warning(small_df, caplog):
    """Every iteration crashes → returns v0 with RED AND logs an error.

    v1.9 change: the all-errored path now logs at ERROR level (not WARNING)
    with a message about 'every iteration errored'.
    """
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(max_iterations=2, sample_skip_below=1),
    )

    def always_crashes(sample, ref, config):
        raise RuntimeError("never works")

    controller._run_pipeline_sample = always_crashes  # type: ignore[method-assign]
    controller._finalize = MagicMock()

    with caplog.at_level(_logging.ERROR, logger="goldenmatch.core.autoconfig_controller"):
        config, profile, history = controller.run(small_df)

    assert profile.health() == HealthVerdict.RED
    assert any(
        "every iteration errored" in rec.message
        for rec in caplog.records
    ), f"Expected error not found; records: {[r.message for r in caplog.records]}"


# ============================================================
# Fix A — match-mode n_rows includes reference
# ============================================================

def test_match_mode_n_rows_includes_reference():
    """In match mode, DataProfile.n_rows must reflect target+reference combined.

    Bug A: _assemble_profile previously called _compute_data_profile(df) with
    only the target sample. BlockingProfile is built over the combined frame, so
    rule_blocking_too_coarse's average block size was computed on half the actual
    record count, causing phantom fires.
    """
    from goldenmatch.core.profile_emitter import ProfileEmitter

    target = pl.DataFrame({
        "title": [f"paper {i}" for i in range(50)],
        "year": ["2020"] * 50,
    })
    reference = pl.DataFrame({
        "title": [f"paper {i}" for i in range(50, 100)],
        "year": ["2020"] * 50,
    })
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(sample_skip_below=10, max_iterations=1),
    )
    # Use an empty emitter to force the _compute_data_profile fallback
    emitter = ProfileEmitter()
    profile = controller._assemble_profile(
        emitter,
        df=target,
        reference=reference,
        iteration=0,
    )
    # n_rows must be 100 (50 target + 50 reference), not 50 (target only)
    assert profile.data.n_rows == 100


# ============================================================
# Tier 1a — recall probe tests (autoconfig-tier1-tier2)
# ============================================================


def test_recall_probe_runs_on_real_sample():
    df = pl.DataFrame({
        "name": [f"alice_{i}" for i in range(20)] + [f"bob_{i}" for i in range(20)],
        "email": [f"a{i}@x" for i in range(20)] + [f"b{i}@y" for i in range(20)],
    })
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(sample_skip_below=10, max_iterations=1),
    )
    config, profile, history = controller.run(df)
    if history.entries:
        # Probe should have run on iter 0
        rate = history.entries[0].profile.scoring.random_pair_above_threshold_rate
        assert rate is None or 0.0 <= rate <= 1.0


def test_recall_probe_returns_none_when_no_weighted_matchkey():
    """If config has only exact matchkeys, probe returns None."""
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )
    cfg = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="exact_email", type="exact",
            fields=[MatchkeyField(field="email", transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(strategy="static",
                                 keys=[BlockingKeyConfig(fields=["email"], transforms=["lowercase"])],
                                 max_block_size=5000, skip_oversized=False),
    )
    controller = AutoConfigController(policy=HeuristicRefitPolicy(),
                                       budget=ControllerBudget())
    df = pl.DataFrame({"email": [f"a{i}@x.com" for i in range(20)]})
    rate = controller._compute_recall_probe(df, cfg)
    assert rate is None


# ============================================================
# Tier 4 — cross-run memory integration
# ============================================================

from goldenmatch.core.autoconfig_memory import AutoConfigMemory, profile_signature
from goldenmatch.config.schemas import (
    GoldenMatchConfig as _GoldenMatchConfig,
    MatchkeyConfig as _MatchkeyConfig,
    MatchkeyField as _MatchkeyField,
    BlockingConfig as _BlockingConfig,
    BlockingKeyConfig as _BlockingKeyConfig,
)


def _cached_cfg():
    return _GoldenMatchConfig(
        matchkeys=[_MatchkeyConfig(
            name="cached",
            type="weighted",
            threshold=0.42,
            fields=[_MatchkeyField(
                field="name",
                scorer="ensemble",
                weight=1.0,
                transforms=["lowercase"],
            )],
        )],
        blocking=_BlockingConfig(
            strategy="static",
            keys=[_BlockingKeyConfig(fields=["city"], transforms=["lowercase"])],
            max_block_size=5000,
            skip_oversized=False,
        ),
    )


def test_controller_uses_memory_when_signature_matches():
    """When AutoConfigMemory has a successful entry for this data shape,
    _initial_config returns the cached config instead of running the legacy heuristic."""
    mem = AutoConfigMemory(db_path=":memory:")
    df = pl.DataFrame({
        "name": ["alice", "bob", "carol"] * 10,
        "city": ["x", "y", "z"] * 10,
    })
    sig = profile_signature(df)
    mem.remember(sig, _cached_cfg(), succeeded=True, n_iterations=2)

    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(sample_skip_below=10, max_iterations=1),
        memory=mem,
    )
    initial = controller._initial_config(df, reference=None)
    assert initial.matchkeys[0].threshold == 0.42
    assert initial.matchkeys[0].name == "cached"


def test_controller_records_committed_run_to_memory(small_df):
    """After a healthy run, the committed config is stored in memory.

    Uses a mocked pipeline runner that returns a GREEN profile, guaranteeing
    a best_entry so memory.remember is called.
    """
    mem = AutoConfigMemory(db_path=":memory:")
    green = _green_subprofiles()
    controller = _make_controller_with_mocked_runner([green])
    controller._memory = mem
    controller._finalize = MagicMock(return_value=ComplexityProfile(**green))

    controller.run(small_df)
    sig = profile_signature(small_df)
    cached = mem.lookup_best(sig)
    # A healthy (GREEN) run must be stored in memory.
    assert cached is not None


def test_controller_memory_not_required():
    """Default (memory=None) runs without error — no memory recording."""
    df = pl.DataFrame({
        "name": ["alice", "bob", "carol"] * 5,
        "city": ["x", "y", "z"] * 5,
    })
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(sample_skip_below=5, max_iterations=1),
        memory=None,
    )
    config, profile, history = controller.run(df)
    assert isinstance(config, _GoldenMatchConfig)


def test_env_var_disables_default_memory(monkeypatch):
    """When GOLDENMATCH_AUTOCONFIG_MEMORY=0, auto_configure_df doesn't crash."""
    import goldenmatch
    import goldenmatch.core.autoconfig as _ac

    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")
    # Reset cached state so the monkeypatched env var takes effect
    monkeypatch.setattr(_ac, "_DEFAULT_MEMORY", None)
    monkeypatch.setattr(_ac, "_AUTOCONFIG_MEMORY_DISABLED", True)

    df = pl.DataFrame({"name": ["a", "b", "c"] * 4, "city": ["x", "y", "z"] * 4})
    cfg = goldenmatch.auto_configure_df(df)
    assert isinstance(cfg, _GoldenMatchConfig)


# ============================================================
# _maybe_decorate_with_llm_scorer (Change A + B: post-iteration LLM decoration)
# ============================================================

def _borderline_profile_for_llm():
    """A ComplexityProfile with meaningful borderline mass (triggers LLM decoration)."""
    from goldenmatch.core.complexity_profile import (
        BlockingProfile, ScoringProfile, ClusterProfile,
    )
    return ComplexityProfile(
        data=DataProfile(n_rows=100, n_cols=2),
        blocking=BlockingProfile(reduction_ratio=0.95, n_blocks=10,
                                  block_sizes_p99=20),
        scoring=ScoringProfile(
            n_pairs_scored=100, candidates_compared=300,
            mass_above_threshold=0.4, mass_in_borderline=0.25,
            dip_statistic=0.05,
        ),
        cluster=ClusterProfile(transitivity_rate=0.95),
    )


def _cfg_with_threshold(threshold: float) -> _GoldenMatchConfig:
    from goldenmatch.config.schemas import (
        MatchkeyConfig, MatchkeyField, BlockingConfig, BlockingKeyConfig,
    )
    return _GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="m", type="weighted", threshold=threshold,
            fields=[MatchkeyField(field="name", scorer="jaro_winkler",
                                   weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["city"], transforms=["lowercase"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )


def test_decorate_with_llm_scorer_fires_with_borderline_and_key(monkeypatch):
    """When the committed profile is borderline-heavy and an API key is
    available, _maybe_decorate_with_llm_scorer enables LLMScorerConfig on
    the returned config."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = _cfg_with_threshold(0.7)
    profile = _borderline_profile_for_llm()
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(),
    )
    out = controller._maybe_decorate_with_llm_scorer(cfg, profile)
    assert out.llm_scorer is not None
    assert out.llm_scorer.enabled is True
    # Dynamic bounds: threshold 0.7 → lo=0.6, hi=0.9
    assert out.llm_scorer.candidate_lo == pytest.approx(0.60)
    assert out.llm_scorer.candidate_hi == pytest.approx(0.90)
    assert out.llm_scorer.auto_threshold == pytest.approx(0.90)


def test_decorate_with_llm_scorer_uses_lowered_threshold(monkeypatch):
    """With threshold=0.5 (controller-lowered), bounds should track:
    lo=0.4, hi=0.7, auto=0.7."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cfg = _cfg_with_threshold(0.5)
    profile = _borderline_profile_for_llm()
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(),
    )
    out = controller._maybe_decorate_with_llm_scorer(cfg, profile)
    assert out.llm_scorer is not None
    assert out.llm_scorer.candidate_lo == pytest.approx(0.40)
    assert out.llm_scorer.candidate_hi == pytest.approx(0.70)


def test_decorate_with_llm_scorer_silent_no_key(monkeypatch):
    """Without an API key, _maybe_decorate_with_llm_scorer returns config unchanged."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    cfg = _cfg_with_threshold(0.7)
    from goldenmatch.core.complexity_profile import ScoringProfile
    profile = ComplexityProfile(
        scoring=ScoringProfile(candidates_compared=100, mass_in_borderline=0.25),
    )
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(), budget=ControllerBudget(),
    )
    out = controller._maybe_decorate_with_llm_scorer(cfg, profile)
    assert out is cfg or out.llm_scorer is None


# ============================================================
# Change 2 (2026-05-07): adaptive auto_threshold
# ============================================================

def test_decorate_uses_wide_mode_when_borderline_dominant(monkeypatch):
    """When mass_in_borderline > 0.5, LLM scorer's auto_threshold drops
    near 1.0 so the LLM inspects high-scoring pairs (the DQbench T2/T3
    pathology where mass_above=1.0 AND mass_borderline=0.95)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from goldenmatch.core.complexity_profile import (
        BlockingProfile, ScoringProfile, ClusterProfile,
    )
    cfg = _cfg_with_threshold(0.7)
    profile = ComplexityProfile(
        data=DataProfile(n_rows=2000, n_cols=2),
        blocking=BlockingProfile(reduction_ratio=0.95, n_blocks=25,
                                  block_sizes_p99=98),
        scoring=ScoringProfile(
            n_pairs_scored=600, candidates_compared=80000,
            mass_above_threshold=1.0,
            mass_in_borderline=0.95,    # WIDE MODE TRIGGER
            dip_statistic=0.05,
        ),
        cluster=ClusterProfile(transitivity_rate=0.02),
    )
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(), budget=ControllerBudget(),
    )
    out = controller._maybe_decorate_with_llm_scorer(cfg, profile)
    assert out.llm_scorer is not None
    # Wide mode: auto_threshold close to 1.0
    assert out.llm_scorer.auto_threshold >= 0.95
    # candidate_lo ≈ threshold - 0.05 (tighter than standard mode's -0.10)
    assert out.llm_scorer.candidate_lo == pytest.approx(0.65, abs=0.01)


def test_decorate_uses_standard_mode_when_borderline_modest(monkeypatch):
    """When mass_in_borderline is between 0.10 and 0.50, dynamic bounds
    centered on threshold remain (existing behavior)."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    from goldenmatch.core.complexity_profile import (
        BlockingProfile, ScoringProfile, ClusterProfile,
    )
    cfg = _cfg_with_threshold(0.7)
    profile = ComplexityProfile(
        data=DataProfile(n_rows=200, n_cols=2),
        blocking=BlockingProfile(reduction_ratio=0.95, n_blocks=10,
                                  block_sizes_p99=20),
        scoring=ScoringProfile(
            n_pairs_scored=100, candidates_compared=300,
            mass_above_threshold=0.4,
            mass_in_borderline=0.25,    # STANDARD MODE
            dip_statistic=0.05,
        ),
        cluster=ClusterProfile(transitivity_rate=0.95),
    )
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(), budget=ControllerBudget(),
    )
    out = controller._maybe_decorate_with_llm_scorer(cfg, profile)
    # Standard bounds: lo=0.6, hi=0.9, auto=0.9
    assert out.llm_scorer.candidate_lo == pytest.approx(0.60)
    assert out.llm_scorer.candidate_hi == pytest.approx(0.90)
    assert out.llm_scorer.auto_threshold == pytest.approx(0.90)


# ============================================================
# Helpers for stop_reason + commit-RED tests (v1.9, 2026-05-08)
# ============================================================

def _red_blocking_subprofile_dict():
    """Return a sub-profile dict whose BlockingProfile produces RED health."""
    return {**_green_subprofiles(), "blocking": _red_blocking_subprofile()}


def _yellow_subprofiles():
    """Return sub-profile dict that yields ComplexityProfile.health() == YELLOW.

    Uses a DataProfile with only one column type (all 'text') so DataProfile.health()
    returns YELLOW. Other sub-profiles are GREEN-safe so YELLOW rolls up.
    """
    return dict(
        data=DataProfile(
            n_rows=100, n_cols=2,
            column_types={"a": "text", "b": "text"},
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


# ============================================================
# stop_reason recording (added 2026-05-08, v1.9)
# ============================================================

def test_stop_reason_green_when_iteration_reaches_green_health(small_df):
    """When an iteration produces a GREEN profile, controller breaks with
    stop_reason=GREEN."""
    green = _green_subprofiles()
    controller = _make_controller_with_mocked_runner([green])
    config, profile, history = controller.run(small_df)
    assert history.stop_reason == StopReason.GREEN


def test_stop_reason_budget_iterations_when_max_iter_reached(small_df):
    """All iterations RED, budget exhausted → BUDGET_ITERATIONS.

    Provides alternating RED profiles (different normalized_signal_vectors so
    convergence doesn't fire) with a policy that always proposes a new config,
    letting the loop run to completion and triggering the natural stop.
    """
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )

    # Two RED profiles with distinct signal vectors (different reduction_ratios)
    red_a = dict(
        data=DataProfile(n_rows=100, n_cols=2, column_types={"a": "name", "b": "numeric"}),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=2, total_comparisons=4900,
            reduction_ratio=0.01, block_sizes_p50=49, block_sizes_p95=49,
            block_sizes_p99=49, block_sizes_max=49,
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
    red_b = dict(red_a, blocking=BlockingProfile(
        keys_used=[["b"]], n_blocks=3, total_comparisons=3000,
        reduction_ratio=0.02, block_sizes_p50=30, block_sizes_p95=30,
        block_sizes_p99=30, block_sizes_max=30,
    ))

    # Config sequence: alternates between two distinct configs so the convergence
    # guard (profile_distance_to_prev) sees non-zero distance AND the policy
    # always returns something different
    configs_cycle = [
        GoldenMatchConfig(
            matchkeys=[MatchkeyConfig(
                name=f"mk_{i}", type="weighted", threshold=0.6 + i * 0.02,
                fields=[MatchkeyField(field="a", scorer="jaro_winkler",
                                       weight=1.0, transforms=["lowercase"])],
            )],
            blocking=BlockingConfig(
                strategy="static",
                keys=[BlockingKeyConfig(fields=["a"], transforms=["lowercase"])],
                max_block_size=5000, skip_oversized=False,
            ),
        )
        for i in range(10)
    ]

    class _CyclingPolicy:
        def __init__(self):
            self._i = 0
        def propose(self, profile, config, history):
            self._i = (self._i + 1) % len(configs_cycle)
            return configs_cycle[self._i]

    controller = _make_controller_with_mocked_runner(
        [red_a, red_b, red_a, red_b, red_a, red_b, red_a],
        max_iterations=3,
        policy=_CyclingPolicy(),
    )
    config, profile, history = controller.run(small_df)
    assert history.stop_reason == StopReason.BUDGET_ITERATIONS


def test_stop_reason_oscillating_when_policy_loops(small_df):
    """When policy alternates between two configs AND profiles oscillate,
    history.is_oscillating() fires and the controller exits with stop_reason=OSCILLATING.

    is_oscillating() requires the SAME (config_hash, decision_hash) pair to
    appear ≥2× in the last 4 entries.  Because history entries are appended
    before the policy call, decision is always None and decision_hash is 0.
    So we need config_hash to repeat ≥2× in last 4 iterations — which means
    the controller must see the same config object at least twice in the last 4.
    """
    from goldenmatch.config.schemas import (
        GoldenMatchConfig, MatchkeyConfig, MatchkeyField,
        BlockingConfig, BlockingKeyConfig,
    )

    # Two configs that are not equal so POLICY_NO_PROGRESS won't fire
    cfg_a = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="mk_a", type="weighted", threshold=0.7,
            fields=[MatchkeyField(field="a", scorer="jaro_winkler",
                                   weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["a"], transforms=["lowercase"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    cfg_b = GoldenMatchConfig(
        matchkeys=[MatchkeyConfig(
            name="mk_b", type="weighted", threshold=0.8,
            fields=[MatchkeyField(field="b", scorer="jaro_winkler",
                                   weight=1.0, transforms=["lowercase"])],
        )],
        blocking=BlockingConfig(
            strategy="static",
            keys=[BlockingKeyConfig(fields=["b"], transforms=["lowercase"])],
            max_block_size=5000, skip_oversized=False,
        ),
    )
    assert cfg_a != cfg_b

    # Two different RED profiles (different reduction_ratios → different signal vectors,
    # so convergence doesn't fire before oscillation detection)
    red_a = dict(
        data=DataProfile(n_rows=100, n_cols=2, column_types={"a": "name", "b": "numeric"}),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=2, total_comparisons=4900,
            reduction_ratio=0.01, block_sizes_p50=49, block_sizes_p95=49,
            block_sizes_p99=49, block_sizes_max=49,
        ),
        scoring=ScoringProfile(n_pairs_scored=500, dip_statistic=0.05,
                                mass_above_threshold=0.4, mass_in_borderline=0.05),
        cluster=ClusterProfile(n_clusters=20, cluster_size_p50=2, cluster_size_p99=5,
                                cluster_size_max=8, transitivity_rate=0.95),
        matchkey=MatchkeyProfile(per_field={"a": FieldStats(0.5, 0.0, 10)}),
    )
    red_b = dict(red_a, blocking=BlockingProfile(
        keys_used=[["b"]], n_blocks=3, total_comparisons=3000,
        reduction_ratio=0.02, block_sizes_p50=30, block_sizes_p95=30,
        block_sizes_p99=30, block_sizes_max=30,
    ))

    class _AlternatingPolicy:
        def __init__(self):
            self._calls = 0
        def propose(self, profile, config, history):
            self._calls += 1
            return cfg_a if self._calls % 2 else cfg_b

    # Alternating profiles with alternating configs → (cfg_a, 0), (cfg_b, 0) repeat
    # After 4 iterations the last-4 window has the same pair ≥2×
    controller = _make_controller_with_mocked_runner(
        [red_a, red_b, red_a, red_b, red_a, red_b, red_a, red_b],
        max_iterations=7,
        policy=_AlternatingPolicy(),
    )
    config, profile, history = controller.run(small_df)
    assert history.stop_reason == StopReason.OSCILLATING


def test_stop_reason_policy_satisfied_on_yellow_with_no_proposal(small_df):
    """When profile is YELLOW and no rule proposes a refit, exit with
    stop_reason=POLICY_SATISFIED."""
    yellow = _yellow_subprofiles()
    controller = _make_controller_with_mocked_runner([yellow])
    config, profile, history = controller.run(small_df)
    assert history.stop_reason == StopReason.POLICY_SATISFIED


def test_stop_reason_cancelled_on_keyboard_interrupt(small_df):
    """KeyboardInterrupt mid-iteration → stop_reason=CANCELLED set BEFORE
    the re-raise. Captured via the _LAST_CONTROLLER_RUN ContextVar."""
    from goldenmatch.core.autoconfig_controller import _LAST_CONTROLLER_RUN

    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(sample_skip_below=1),
    )
    def interrupting(*_args, **_kw):
        raise KeyboardInterrupt
    controller._run_pipeline_sample = interrupting  # type: ignore[method-assign]

    with pytest.raises(KeyboardInterrupt):
        controller.run(small_df)

    history = _LAST_CONTROLLER_RUN.get()
    assert history is not None
    assert history.stop_reason == StopReason.CANCELLED


# ============================================================
# Task 3.2 — commit via pick_committed() + health-aware logging
# ============================================================

def test_controller_commits_red_when_data_provokes_red():
    """End-to-end: real pipeline on a fixture where every iteration produces
    a RED profile. Controller commits the best RED entry, surfaces stop_reason,
    runs _finalize on the committed RED config (output exists, just imperfect)."""
    from pathlib import Path

    fixture = Path(__file__).parent / "fixtures" / "autoconfig" / "red_provoking.csv"
    if not fixture.exists():
        pytest.skip(f"missing fixture: {fixture}")
    import polars as pl
    df = pl.read_csv(fixture)

    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(
            max_iterations=3,
            sample_skip_below=10,
        ),
    )
    config, profile, history = controller.run(df)
    from goldenmatch.config.schemas import GoldenMatchConfig
    assert isinstance(config, GoldenMatchConfig)
    assert history.stop_reason is not None
    # At least one iteration produced a profile (not all errored)
    assert any(e.error is None for e in history.entries)


def test_controller_warns_on_red_commit(small_df, caplog):
    """Committing a RED entry triggers a WARNING log naming the failing
    sub-profile + stop_reason."""
    import logging
    red = _red_blocking_subprofile_dict()
    controller = _make_controller_with_mocked_runner(
        [red, red, red], max_iterations=2,
    )
    with caplog.at_level(logging.WARNING,
                          logger="goldenmatch.core.autoconfig_controller"):
        controller.run(small_df)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert any(
        "best-effort RED" in r.message
        and "stop_reason=" in r.message
        and "failing_subprofile=" in r.message
        for r in warnings
    ), f"expected RED-commit warning; got: {[r.message for r in warnings]}"


def test_controller_info_log_on_yellow_commit(small_df, caplog):
    """YELLOW commit logs at INFO."""
    import logging
    yellow = _yellow_subprofiles()
    controller = _make_controller_with_mocked_runner([yellow])
    with caplog.at_level(logging.INFO,
                          logger="goldenmatch.core.autoconfig_controller"):
        controller.run(small_df)
    infos = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("YELLOW" in r.message for r in infos), (
        f"expected YELLOW-commit info; got: {[r.message for r in infos]}"
    )


# ============================================================
# Task 3.5.2 — virtual v0 HistoryEntry (v1.9 amendment, 2026-05-08)
# ============================================================

def test_controller_appends_v0_virtual_entry_before_pick_committed(small_df):
    """After the iteration loop, the controller appends config_v0's profile
    as a synthetic HistoryEntry with iteration=-1."""
    red = _red_blocking_subprofile_dict()
    controller = _make_controller_with_mocked_runner(
        [red, red, red], max_iterations=2,
    )
    config, profile, history = controller.run(small_df)
    v0_entries = [e for e in history.entries if e.iteration == -1]
    assert len(v0_entries) == 1
    assert v0_entries[0].decision is None


# ============================================================
# Task 3.1 — IndicatorContext
# ============================================================

def test_indicator_context_memoizes_calls():
    """ctx.full_pop_matchkey_hits memoizes by (col)."""
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    import polars as pl
    df = pl.DataFrame({"email": ["a@x.com", "a@x.com", "b@x.com"]})
    ctx = IndicatorContext(
        df=df, column_priors={},
        sparsity_verdict=SparsityVerdict(is_sparse=False, estimated_n_true_pairs=1),
    )
    h1 = ctx.full_pop_matchkey_hits("email")
    h2 = ctx.full_pop_matchkey_hits("email")
    assert h1 == h2
    assert ("full_pop_matchkey_hits", "email") in ctx._memo


def test_indicator_context_has_fired_one_shot_guard():
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    import polars as pl
    ctx = IndicatorContext(
        df=pl.DataFrame(), column_priors={},
        sparsity_verdict=SparsityVerdict(is_sparse=True, estimated_n_true_pairs=0),
    )
    assert ctx.has_fired("rule_x") is False
    ctx.mark_fired("rule_x")
    assert ctx.has_fired("rule_x") is True


def test_indicator_context_cross_blocking_overlap_canonicalizes_keys():
    """Same key pair in different orders gets same memoized result."""
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    import polars as pl
    df = pl.DataFrame({"city": ["nyc"] * 10, "state": ["NY"] * 10})
    ctx = IndicatorContext(
        df=df, column_priors={},
        sparsity_verdict=SparsityVerdict(False, 0),
    )
    o1 = ctx.cross_blocking_overlap("city", "state")
    o2 = ctx.cross_blocking_overlap("state", "city")
    assert o1 == o2
    # Memo key uses sorted ordering
    assert ("cross_blocking_overlap", "city", "state") in ctx._memo


# ============================================================
# Task 6.1: eager indicator compute + ctx threading
# ============================================================

def test_controller_attaches_indicators_profile_after_run():
    """After run(), committed profile has column_priors + indicators populated."""
    import os
    import polars as pl
    from goldenmatch.core.autoconfig_controller import (
        AutoConfigController, ControllerBudget,
    )
    from goldenmatch.core.autoconfig_policy import HeuristicRefitPolicy
    os.environ["GOLDENMATCH_AUTOCONFIG_MEMORY"] = "0"
    df = pl.DataFrame({
        "email": [f"u{i}@x.com" for i in range(20)],
        "name": ["Brian"] * 20,
    })
    controller = AutoConfigController(
        policy=HeuristicRefitPolicy(),
        budget=ControllerBudget(sample_skip_below=1, max_iterations=2),
    )
    config, profile, history = controller.run(df)
    # column_priors populated by eager compute
    assert profile.data.column_priors is not None
    assert "email" in profile.data.column_priors
    # indicators object exists (may have None inner fields if no lazy call fired)
    assert profile.indicators is not None


# ============================================================
# Task 6.1.5: GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET=fast
# ============================================================

def test_indicator_context_fast_mode_skips_expensive(monkeypatch):
    """When GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET=fast, expensive lazy
    indicators return None instead of computing."""
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_INDICATOR_BUDGET", "fast")
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    import polars as pl
    df = pl.DataFrame({"email": ["a@x.com"] * 100, "name": ["x"] * 100})
    ctx = IndicatorContext(
        df=df, column_priors={},
        sparsity_verdict=SparsityVerdict(False, 0),
    )
    assert ctx.full_pop_matchkey_hits("email") is None
    assert ctx.cross_blocking_overlap("email", "name") is None


# ============================================================
# Task 4.2: IndicatorContext.identity_collision_signal
# ============================================================

def test_indicator_context_identity_collision_signal_memoizes():
    """Same call twice → only one underlying compute (memoized via _memo)."""
    import polars as pl
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    df = pl.DataFrame({
        "email": ["a@x.com"] * 4 + ["b@x.com"] * 4,
        "address": [f"{i}" for i in range(8)],
    })
    ctx = IndicatorContext(
        df=df, column_priors={},
        sparsity_verdict=SparsityVerdict(False, 0),
    )
    s1 = ctx.identity_collision_signal("email", ["address"])
    s2 = ctx.identity_collision_signal("email", ["address"])
    assert s1.rate == s2.rate
    # Memo key uses sorted witnesses
    assert ("identity_collision_signal", "email", ("address",)) in ctx._memo


def test_indicator_context_identity_collision_signal_canonicalizes_witnesses():
    """Different witness orderings hit same memo entry."""
    import polars as pl
    from goldenmatch.core.autoconfig_controller import IndicatorContext
    from goldenmatch.core.complexity_profile import SparsityVerdict
    df = pl.DataFrame({
        "email": ["a@x.com"] * 4,
        "address": ["1", "2", "3", "4"],
        "phone": ["a", "b", "c", "d"],
    })
    ctx = IndicatorContext(
        df=df, column_priors={},
        sparsity_verdict=SparsityVerdict(False, 0),
    )
    s1 = ctx.identity_collision_signal("email", ["address", "phone"])
    s2 = ctx.identity_collision_signal("email", ["phone", "address"])
    assert s1.rate == s2.rate
    # Both should hit the same canonical key
    canonical_key = ("identity_collision_signal", "email", ("address", "phone"))
    assert canonical_key in ctx._memo
