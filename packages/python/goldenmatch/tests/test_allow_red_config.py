"""End-to-end API tests for the allow_red_config kwarg (#715 reopened).

A committed RED config raises ControllerNotConfidentError when
``n_rows >= REFUSE_AT_N and not allow_red_config``. Below REFUSE_AT_N a RED
commit warn-and-runs (cheap; the existing deliberate design).
allow_red_config=True restores warn-and-run at scale. confidence_required is
NOT part of the RED gate -- confidence_required=False no longer bypasses the
RED-refuse (that was the reporter's bug). See #715 reopened, Task 5.

Mirrors tests/test_api_confidence_required_kwarg.py's monkeypatch-forced-RED
helper."""
from __future__ import annotations

import goldenmatch as gm
import polars as pl
import pytest
from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.core import autoconfig_controller
from goldenmatch.core.autoconfig_controller import (
    REFUSE_AT_N,
    ControllerNotConfidentError,
)

# "At scale" for the gate means one thing only: n_rows >= REFUSE_AT_N. The
# controller still runs its full iteration loop before reaching that
# comparison, so building a real 100_000-row frame per test cost ~73s EACH --
# eight of them, and the sibling file's five, dominated CI shard 1 (12m41s vs
# ~4m30s for the other two shards).
#
# REFUSE_AT_N has exactly ONE behavioural use: the gate comparison. The other
# two mentions in autoconfig_controller.py are comments explicitly saying the
# threshold is NOT applied at those call sites. So lowering it exercises a
# byte-identical code path at 1/500th the size -- measured: same
# ControllerNotConfidentError, same message, 0.79s instead of 73s.
#
# The real constant is pinned by test_refuse_at_n_is_100k below, and
# test_dedupe_df_raises_on_red_at_scale_at_real_threshold still drives the
# whole path at the true 100_000 so the lowered threshold cannot hide a
# controller that stops reaching the gate at real scale.
_TEST_REFUSE_AT_N = 200


@pytest.fixture(autouse=True)
def _disable_autoconfig_memory(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")


@pytest.fixture
def at_scale(monkeypatch) -> int:
    """Lower the RED-refuse threshold; return the row count that now crosses it.

    Not autouse: the small-N tests need the gate NOT to fire, and one test
    deliberately keeps the real 100_000.
    """
    monkeypatch.setattr(autoconfig_controller, "REFUSE_AT_N", _TEST_REFUSE_AT_N)
    return _TEST_REFUSE_AT_N


def test_refuse_at_n_is_100k():
    """Pin the real threshold -- every other at-scale test here lowers it."""
    assert REFUSE_AT_N == 100_000


def _force_red_history(monkeypatch, n_rows_in_df: int):
    """Force pick_committed to return a RED HistoryEntry."""
    from goldenmatch.core.autoconfig_history import HistoryEntry, RunHistory
    from goldenmatch.core.complexity_profile import (
        BlockingProfile,
        ComplexityProfile,
        DataProfile,
        ProfileMeta,
        ScoringProfile,
    )

    red_profile = ComplexityProfile(
        data=DataProfile(n_rows=0),
        blocking=BlockingProfile(),
        scoring=ScoringProfile(),
        meta=ProfileMeta(
            iteration=0, is_sample=False, sample_size=n_rows_in_df,
            n_rows_full=n_rows_in_df, wall_clock_ms=0, seed=0,
        ),
    )

    def _picker(self, *args, **kwargs):
        return HistoryEntry(
            iteration=0, config=GoldenMatchConfig(), profile=red_profile,
            decision=None, error=None, wall_clock_ms=0,
        )

    monkeypatch.setattr(RunHistory, "pick_committed", _picker)


def _df(n_rows: int) -> pl.DataFrame:
    return pl.DataFrame({
        "name": ["alice"] * n_rows,
        "email": [f"u{i}@x.com" for i in range(n_rows)],
    })


# --- small-N (< REFUSE_AT_N): RED commit does NOT raise (warn-and-run
#     preserved -- the deliberate design) ----------------------------------

def test_dedupe_df_small_n_red_does_not_raise(monkeypatch):
    # Below REFUSE_AT_N a RED commit is cheap -- warn-and-run, no raise.
    _force_red_history(monkeypatch, n_rows_in_df=50)
    result = gm.dedupe_df(_df(50))  # allow_red_config defaults False
    assert result is not None


def test_auto_configure_df_small_n_red_does_not_raise(monkeypatch):
    from goldenmatch.core.autoconfig import auto_configure_df

    _force_red_history(monkeypatch, n_rows_in_df=50)
    cfg = auto_configure_df(_df(50))
    assert cfg is not None


def test_match_df_small_n_red_does_not_raise(monkeypatch):
    _force_red_history(monkeypatch, n_rows_in_df=50)
    result = gm.match_df(_df(50), _df(20))
    assert result is not None


# --- at-scale RED (>= REFUSE_AT_N) raises by default; allow_red_config=True
#     restores warn-and-run -------------------------------------------------

def test_dedupe_df_raises_on_red_at_scale_at_real_threshold(monkeypatch):
    """The one test that pays for a real 100_000-row frame.

    Deliberately does NOT take the `at_scale` fixture. Every other at-scale
    test here lowers REFUSE_AT_N, which proves the comparison but not that the
    controller still REACHES it on a genuinely large frame. This one does.
    """
    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    with pytest.raises(ControllerNotConfidentError):
        gm.dedupe_df(_df(REFUSE_AT_N))


def test_dedupe_df_allow_red_config_true_runs_at_scale(monkeypatch, at_scale):
    _force_red_history(monkeypatch, n_rows_in_df=at_scale)
    result = gm.dedupe_df(_df(at_scale), allow_red_config=True)
    assert result is not None


def test_auto_configure_df_raises_on_red_at_scale_by_default(monkeypatch, at_scale):
    from goldenmatch.core.autoconfig import auto_configure_df

    _force_red_history(monkeypatch, n_rows_in_df=at_scale)
    with pytest.raises(ControllerNotConfidentError):
        auto_configure_df(_df(at_scale))


def test_auto_configure_df_allow_red_config_true_returns_config(monkeypatch, at_scale):
    from goldenmatch.core.autoconfig import auto_configure_df

    _force_red_history(monkeypatch, n_rows_in_df=at_scale)
    cfg = auto_configure_df(_df(at_scale), allow_red_config=True)
    assert cfg is not None


def test_match_df_raises_on_red_at_scale_by_default(monkeypatch, at_scale):
    _force_red_history(monkeypatch, n_rows_in_df=at_scale)
    with pytest.raises(ControllerNotConfidentError):
        gm.match_df(_df(at_scale), _df(20))


def test_match_df_allow_red_config_true_runs_at_scale(monkeypatch, at_scale):
    _force_red_history(monkeypatch, n_rows_in_df=at_scale)
    result = gm.match_df(_df(at_scale), _df(20), allow_red_config=True)
    assert result is not None


# --- confidence_required=False NO LONGER bypasses RED-refuse at scale (the
#     reporter's bug -- behavior change) -------------------------------------

def test_confidence_required_false_still_raises_on_red_at_scale(monkeypatch, at_scale):
    # Pre-#715-reopened, confidence_required=False kept warn-and-run on a RED
    # commit. Now allow_red_config is the only escape; confidence_required is
    # out of the RED gate, so this still raises.
    _force_red_history(monkeypatch, n_rows_in_df=at_scale)
    with pytest.raises(ControllerNotConfidentError):
        gm.dedupe_df(_df(at_scale), confidence_required=False)


# --- error message surfaces the escape hatch -------------------------------

def test_error_message_mentions_allow_red_config(monkeypatch, at_scale):
    from goldenmatch.core.autoconfig import auto_configure_df

    _force_red_history(monkeypatch, n_rows_in_df=at_scale)
    try:
        auto_configure_df(_df(at_scale))
    except ControllerNotConfidentError as e:
        assert "allow_red_config" in str(e)
    else:  # pragma: no cover
        pytest.fail("expected ControllerNotConfidentError")
