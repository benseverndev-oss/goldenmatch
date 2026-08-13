"""#2532: the auto-config search must be able to ignore its wall-clock budgets.

The defect: the controller (`ControllerBudget.max_seconds`) and every indicator
budget cut the search short on elapsed time, so which config gets committed is a
function of host speed and load. Anything that PINS controller output is then
unstable by construction. `GOLDENMATCH_AUTOCONFIG_DETERMINISTIC=1` removes those
cuts and leaves the iteration caps as the only stopping rule.

Two properties are load-bearing and both are tested here:

* the env var is read at CALL time, so a harness can set it after import (the
  import-time-constant trap);
* a budget of ``<= 0.0`` means "this indicator is switched OFF", which is a
  semantic switch and not a time limit -- deterministic mode must not re-enable
  it. Getting that backwards would silently turn indicators back on in exactly
  the runs whose output we pin.
"""
from __future__ import annotations

import time

import pytest
from goldenmatch.core import indicators as IND
from goldenmatch.core.autoconfig_determinism import (
    ENV_VAR,
    deterministic_search_enabled,
    over_budget,
)

# ── the flag itself ───────────────────────────────────────────────────────────

def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert deterministic_search_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " 1 "])
def test_truthy_spellings_enable(monkeypatch, value):
    monkeypatch.setenv(ENV_VAR, value)
    assert deterministic_search_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "no", "off", "", "maybe"])
def test_everything_else_leaves_it_off(monkeypatch, value):
    """An unrecognized value must not silently pin a production run's search."""
    monkeypatch.setenv(ENV_VAR, value)
    assert deterministic_search_enabled() is False


def test_read_at_call_time_not_import_time(monkeypatch):
    """Set after import must still take effect -- the #957 stale-constant trap."""
    monkeypatch.delenv(ENV_VAR, raising=False)
    assert over_budget(time.time() - 10.0, 1.0) is True
    monkeypatch.setenv(ENV_VAR, "1")
    assert over_budget(time.time() - 10.0, 1.0) is False


def test_over_budget_matches_the_comparison_it_replaced(monkeypatch):
    monkeypatch.delenv(ENV_VAR, raising=False)
    now = time.time()
    assert over_budget(now, 30.0) is False        # nothing elapsed yet
    assert over_budget(now - 31.0, 30.0) is True  # comfortably past


# ── the controller's iteration loop ───────────────────────────────────────────

def test_controller_cannot_stop_on_the_clock_when_pinned(monkeypatch):
    """With an already-blown wall budget, only the flag decides BUDGET_TIME."""
    import polars as pl
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.core.autoconfig_controller import (
        _LAST_CONTROLLER_RUN,
        ControllerBudget,
    )
    from goldenmatch.core.complexity_profile import StopReason

    df = pl.DataFrame({
        "name": [f"person {i % 40}" for i in range(120)],
        "email": [f"user{i % 40}@example.com" for i in range(120)],
        "city": [["Boston", "Denver", "Austin"][i % 3] for i in range(120)],
    })

    # max_seconds=0 blows the wall budget on every iteration after the first;
    # max_iterations=2 keeps the test cheap.
    def _blown(cls, n_rows, effort="normal"):
        return ControllerBudget(max_iterations=2, max_seconds=0.0)

    monkeypatch.setattr(ControllerBudget, "for_dataset", classmethod(_blown))
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")

    monkeypatch.delenv(ENV_VAR, raising=False)
    auto_configure_df(df, allow_red_config=True)
    unpinned = _LAST_CONTROLLER_RUN.get()
    assert unpinned is not None
    assert unpinned.stop_reason is StopReason.BUDGET_TIME, (
        "precondition: without the flag a blown wall budget must stop the loop"
    )

    monkeypatch.setenv(ENV_VAR, "1")
    auto_configure_df(df, allow_red_config=True)
    pinned = _LAST_CONTROLLER_RUN.get()
    assert pinned is not None
    assert pinned.stop_reason is not StopReason.BUDGET_TIME


# ── indicator budgets ─────────────────────────────────────────────────────────

def test_column_priors_are_computed_despite_a_blown_budget(monkeypatch):
    """A time-exhausted priors pass hands back all-zero priors, which changes
    what the indicator-aware rules decide. Pinned runs must not do that."""
    import polars as pl

    df = pl.DataFrame({
        "email": [f"u{i}@example.com" for i in range(50)],
        "city": [["Boston", "Denver"][i % 2] for i in range(50)],
    })
    monkeypatch.setattr(IND, "BUDGET_COLUMN_PRIORS", -1.0)  # always exhausted

    monkeypatch.delenv(ENV_VAR, raising=False)
    starved = IND.compute_column_priors(df)
    assert all(p.identity_score == 0.0 for p in starved.values())

    monkeypatch.setenv(ENV_VAR, "1")
    full = IND.compute_column_priors(df)
    assert full["email"].identity_score > 0.0, (
        "deterministic mode must let the priors pass actually run"
    )


def test_a_disabled_indicator_stays_disabled_when_pinned(monkeypatch):
    """`BUDGET_X <= 0.0` means switched OFF, not out of time.

    Deterministic mode bypasses elapsed-time comparisons only. If it also
    bypassed the disabled checks it would quietly re-enable indicators in
    precisely the runs whose output gets pinned.
    """
    import polars as pl

    df = pl.DataFrame({"blk": [f"k{i % 5}" for i in range(50)]})
    monkeypatch.setenv(ENV_VAR, "1")

    monkeypatch.setattr(IND, "BUDGET_FULL_POP_HITS", 0.0)
    assert IND.estimate_full_pop_hits(df, "blk") is None

    monkeypatch.setattr(IND, "BUDGET_CROSS_BLOCKING", 0.0)
    assert IND.compute_cross_blocking_overlap(df, "blk", "other") is None

    monkeypatch.setattr(IND, "BUDGET_CORRUPTION", 0.0)
    assert IND._compute_corruption_score_inline(df, "blk") == 0.0


def test_full_pop_hits_survives_a_blown_time_budget_when_pinned(monkeypatch):
    """The same indicator, starved on TIME rather than switched off, does run."""
    import polars as pl

    df = pl.DataFrame({"blk": [f"k{i % 5}" for i in range(50)]})
    # A positive-but-unmeetable budget, NOT a negative one: `<= 0.0` would trip
    # the disabled pre-flight instead and this test would prove nothing. 1e-9 s
    # is below the float resolution of a `time.time()` delta (~0.24 us at the
    # current epoch), so any work at all exhausts it.
    monkeypatch.setattr(IND, "BUDGET_FULL_POP_HITS", 1e-9)

    monkeypatch.delenv(ENV_VAR, raising=False)
    assert IND.estimate_full_pop_hits(df, "blk") is None

    monkeypatch.setenv(ENV_VAR, "1")
    # 5 blocks of 10 rows -> 5 * C(10,2) = 225 co-blocked pairs.
    assert IND.estimate_full_pop_hits(df, "blk") == 225


# ── the pin harness ───────────────────────────────────────────────────────────

def _pin_harness():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent / "parity"))
    import capture_autoconfig_output as cap  # type: ignore

    return cap


def test_pin_harness_restores_the_previous_env(monkeypatch):
    cap = _pin_harness()
    monkeypatch.setenv(ENV_VAR, "0")
    with cap._deterministic_search():
        assert deterministic_search_enabled() is True
    assert deterministic_search_enabled() is False

    monkeypatch.delenv(ENV_VAR, raising=False)
    with cap._deterministic_search():
        assert deterministic_search_enabled() is True
    import os
    assert ENV_VAR not in os.environ


def test_pin_harness_refuses_to_pin_a_clock_stopped_run(monkeypatch):
    """If the flag ever fails to reach the controller, pinning must fail loudly
    rather than commit a host-dependent config -- the whole point of #2532."""
    from goldenmatch.core.autoconfig_controller import _LAST_CONTROLLER_RUN
    from goldenmatch.core.autoconfig_history import RunHistory
    from goldenmatch.core.complexity_profile import StopReason

    cap = _pin_harness()

    history = RunHistory()
    history.stop_reason = StopReason.BUDGET_TIME
    token = _LAST_CONTROLLER_RUN.set(history)
    try:
        with pytest.raises(RuntimeError, match="BUDGET_TIME"):
            cap._assert_deterministic_stop("dblp_acm")
        history.stop_reason = StopReason.BUDGET_ITERATIONS
        cap._assert_deterministic_stop("dblp_acm")  # must not raise
    finally:
        _LAST_CONTROLLER_RUN.reset(token)
