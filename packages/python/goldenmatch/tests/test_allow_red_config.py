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
from goldenmatch.core.autoconfig_controller import (
    REFUSE_AT_N,
    ControllerNotConfidentError,
)


@pytest.fixture(autouse=True)
def _disable_autoconfig_memory(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")


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

def test_dedupe_df_raises_on_red_at_scale_by_default(monkeypatch):
    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    with pytest.raises(ControllerNotConfidentError):
        gm.dedupe_df(_df(REFUSE_AT_N))


def test_dedupe_df_allow_red_config_true_runs_at_scale(monkeypatch):
    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    result = gm.dedupe_df(_df(REFUSE_AT_N), allow_red_config=True)
    assert result is not None


def test_auto_configure_df_raises_on_red_at_scale_by_default(monkeypatch):
    from goldenmatch.core.autoconfig import auto_configure_df

    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    with pytest.raises(ControllerNotConfidentError):
        auto_configure_df(_df(REFUSE_AT_N))


def test_auto_configure_df_allow_red_config_true_returns_config(monkeypatch):
    from goldenmatch.core.autoconfig import auto_configure_df

    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    cfg = auto_configure_df(_df(REFUSE_AT_N), allow_red_config=True)
    assert cfg is not None


def test_match_df_raises_on_red_at_scale_by_default(monkeypatch):
    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    with pytest.raises(ControllerNotConfidentError):
        gm.match_df(_df(REFUSE_AT_N), _df(20))


def test_match_df_allow_red_config_true_runs_at_scale(monkeypatch):
    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    result = gm.match_df(_df(REFUSE_AT_N), _df(20), allow_red_config=True)
    assert result is not None


# --- confidence_required=False NO LONGER bypasses RED-refuse at scale (the
#     reporter's bug -- behavior change) -------------------------------------

def test_confidence_required_false_still_raises_on_red_at_scale(monkeypatch):
    # Pre-#715-reopened, confidence_required=False kept warn-and-run on a RED
    # commit. Now allow_red_config is the only escape; confidence_required is
    # out of the RED gate, so this still raises.
    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    with pytest.raises(ControllerNotConfidentError):
        gm.dedupe_df(_df(REFUSE_AT_N), confidence_required=False)


# --- error message surfaces the escape hatch -------------------------------

def test_error_message_mentions_allow_red_config(monkeypatch):
    from goldenmatch.core.autoconfig import auto_configure_df

    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    try:
        auto_configure_df(_df(REFUSE_AT_N))
    except ControllerNotConfidentError as e:
        assert "allow_red_config" in str(e)
    else:  # pragma: no cover
        pytest.fail("expected ControllerNotConfidentError")
