"""Unit tests for the confidence gate inside AutoConfigController.run.

Spec §Design / Confidence gate. Gate fires when:
    confidence_required=True
    AND df.height >= REFUSE_AT_N
    AND best_entry.profile.health() == RED.

These tests use a monkey-patched pick_committed to force RED entries
without needing a real 100K fixture. Phase 5 adds an end-to-end test
that exercises the real iteration loop.
"""
from __future__ import annotations

import goldenmatch as gm
import polars as pl
import pytest
from goldenmatch.core.autoconfig_controller import (
    REFUSE_AT_N,
    ControllerNotConfidentError,
)
from goldenmatch.core.complexity_profile import (
    BlockingProfile,
    ComplexityProfile,
    DataProfile,
    ProfileMeta,
    ScoringProfile,
)


@pytest.fixture(autouse=True)
def _disable_autoconfig_memory(monkeypatch):
    """Match the integration-test convention: prevent cross-run cache
    short-circuits from affecting these gate tests."""
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")


# #433: gate tests previously built REFUSE_AT_N (100K) row dataframes
# to exercise the gate. Each test ran the controller iteration loop on
# 100K rows, costing ~30s/test * 4 tests = ~120s of CI wall time.
#
# The gate's behavior depends on the COMPARISON `df.height >= REFUSE_AT_N`,
# not the absolute scale. Lowering REFUSE_AT_N to SMALL_N via monkeypatch
# and using SMALL_N-row dataframes exercises the same code path on a
# fraction of the data.
#
# The constant-guardrail test `test_refuse_at_n_constant_is_100k` opts
# OUT of this fixture (its own monkeypatch fixture below). That keeps
# the load-bearing 100K value pinned in source.
SMALL_N = 500


@pytest.fixture(autouse=True)
def _lower_refuse_at_n_for_speed(monkeypatch, request):
    """Lower REFUSE_AT_N to SMALL_N inside the autoconfig_controller
    module + tighten the iteration budget so gate-mechanism tests
    don't need 100K-row fixtures OR multiple iteration passes.

    Skipped via marker `keep_real_refuse_at_n` for the constant guardrail.

    Without the iteration-budget cap, the smaller dataframe runs each
    pipeline iteration so fast that the controller fits MORE iterations
    in the wall-budget than it did on 100K -- net slower. Capping
    max_iterations to 0 keeps the loop minimal (1 iteration via
    `range(max_iterations + 1)`). The gate fires AFTER the loop based
    on the mocked pick_committed, so iteration count doesn't change
    test outcomes -- only wall time.
    """
    if request.node.get_closest_marker("keep_real_refuse_at_n"):
        return
    from goldenmatch.core import autoconfig_controller as ctrl_mod
    monkeypatch.setattr(ctrl_mod, "REFUSE_AT_N", SMALL_N)
    # Replace ControllerBudget.for_dataset with a min-iterations stub.
    # `range(max_iterations + 1)` means max_iterations=0 still runs 1
    # iteration -- enough for the mocked pick_committed to commit.
    def _tight_for_dataset(
        cls, n_rows: int, effort: str = "normal"
    ) -> ctrl_mod.ControllerBudget:
        return cls(max_iterations=0, max_seconds=15.0)

    monkeypatch.setattr(
        ctrl_mod.ControllerBudget, "for_dataset",
        classmethod(_tight_for_dataset),
    )


def _force_red_history_entry(monkeypatch, n_rows_in_df: int):
    """Replace RunHistory.pick_committed with a callable returning a
    forced-RED HistoryEntry."""
    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.core.autoconfig_history import HistoryEntry, RunHistory
    from goldenmatch.core.complexity_profile import HealthVerdict

    red_profile = ComplexityProfile(
        data=DataProfile(n_rows=0),  # n_rows==0 forces data.health() == RED
        blocking=BlockingProfile(),
        scoring=ScoringProfile(),
        meta=ProfileMeta(
            iteration=0, is_sample=False, sample_size=n_rows_in_df,
            n_rows_full=n_rows_in_df, wall_clock_ms=0, seed=0,
        ),
    )
    assert red_profile.health() == HealthVerdict.RED  # sanity

    # A minimal config is required so the code paths after the confidence
    # gate (Task 6.1 stamp, LLM decorator, planner) can access
    # best_entry.config.blocking without AttributeError when the gate
    # doesn't fire (below-threshold or non-RED cases).
    _minimal_config = GoldenMatchConfig()

    def _picker(self, *args, **kwargs):
        return HistoryEntry(
            iteration=0,
            config=_minimal_config,
            profile=red_profile,
            decision=None,
            error=None,
            wall_clock_ms=0,
        )

    monkeypatch.setattr(RunHistory, "pick_committed", _picker)


def _df(n_rows: int) -> pl.DataFrame:
    """Minimum-shape df where only df.height matters for the gate."""
    return pl.DataFrame({
        "name": ["alice"] * n_rows,
        "email": [f"u{i}@x.com" for i in range(n_rows)],
    })


@pytest.mark.keep_real_refuse_at_n
def test_refuse_at_n_constant_is_100k():
    # Source-of-truth guardrail: REFUSE_AT_N is the documented 100K
    # threshold. Opts out of the autouse monkeypatch via marker so the
    # real constant is asserted.
    assert REFUSE_AT_N == 100_000


def test_gate_fires_on_red_at_or_above_threshold(monkeypatch):
    """df.height = REFUSE_AT_N exactly + RED -> raise."""
    _force_red_history_entry(monkeypatch, n_rows_in_df=SMALL_N)
    df = _df(SMALL_N)
    with pytest.raises(ControllerNotConfidentError) as exc_info:
        gm.dedupe_df(df)
    assert exc_info.value.n_rows == SMALL_N
    assert exc_info.value.failing_sub_profile == "data"


def test_gate_does_not_fire_below_threshold(monkeypatch):
    """df.height < REFUSE_AT_N + RED -> warn-and-run (current behavior)."""
    n = SMALL_N - 1
    _force_red_history_entry(monkeypatch, n_rows_in_df=n)
    df = _df(n)
    result = gm.dedupe_df(df)
    assert result is not None


def test_gate_does_not_fire_on_yellow_or_green(monkeypatch):
    """Only RED triggers the gate. YELLOW/GREEN at large N: proceed."""
    from goldenmatch.core.autoconfig_history import HistoryEntry, RunHistory

    green_profile = ComplexityProfile(
        data=DataProfile(n_rows=SMALL_N, n_cols=3, column_types={
            "a": "text", "b": "text", "c": "text",
        }),
        blocking=BlockingProfile(
            keys_used=[["a"]], n_blocks=10, total_comparisons=100,
            reduction_ratio=0.9, block_sizes_p50=10, block_sizes_p95=15,
            block_sizes_p99=20, block_sizes_max=25,
            singleton_block_count=0, oversized_block_count=0,
        ),
        scoring=ScoringProfile(
            n_pairs_scored=100, candidates_compared=100,
            mass_above_threshold=0.5, mass_in_borderline=0.1,
            dip_statistic=0.05,  # avoid RED: health() returns RED when dip<0.005
        ),
        meta=ProfileMeta(
            iteration=0, is_sample=False, sample_size=SMALL_N,
            n_rows_full=SMALL_N, wall_clock_ms=0, seed=0,
        ),
    )

    from goldenmatch.config.schemas import GoldenMatchConfig
    _minimal_config = GoldenMatchConfig()

    def _picker(self, *args, **kwargs):
        return HistoryEntry(
            iteration=0, config=_minimal_config, profile=green_profile,
            decision=None, error=None, wall_clock_ms=0,
        )

    monkeypatch.setattr(RunHistory, "pick_committed", _picker)
    df = _df(SMALL_N)
    result = gm.dedupe_df(df)  # should NOT raise
    assert result is not None


def test_confidence_required_false_no_longer_bypasses_red_refuse(monkeypatch):
    """#715 reopened: confidence_required=False NO LONGER bypasses the
    RED-refuse (that was the reporter's bug). A committed-RED entry at
    >= REFUSE_AT_N raises regardless of confidence_required."""
    _force_red_history_entry(monkeypatch, n_rows_in_df=SMALL_N)
    df = _df(SMALL_N)
    with pytest.raises(ControllerNotConfidentError):
        gm.dedupe_df(df, confidence_required=False)


def test_allow_red_config_is_the_red_refuse_escape_hatch(monkeypatch):
    """#715 reopened: allow_red_config=True is now the SINGLE escape hatch
    that restores warn-and-run on a committed-RED entry at >= REFUSE_AT_N."""
    _force_red_history_entry(monkeypatch, n_rows_in_df=SMALL_N)
    df = _df(SMALL_N)
    result = gm.dedupe_df(df, allow_red_config=True)
    assert result is not None
