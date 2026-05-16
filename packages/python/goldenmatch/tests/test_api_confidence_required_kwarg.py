"""End-to-end API tests for the confidence_required kwarg.

Spec §Backward compatibility. The kwarg defaults to True (loud-not-slow);
False preserves today's warn-and-run behavior."""
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


def test_dedupe_df_default_raises_at_scale_on_red(monkeypatch):
    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    with pytest.raises(ControllerNotConfidentError):
        gm.dedupe_df(_df(REFUSE_AT_N))


def test_dedupe_df_confidence_required_false_short_circuits(monkeypatch):
    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    result = gm.dedupe_df(_df(REFUSE_AT_N), confidence_required=False)
    assert result is not None


def test_auto_configure_df_default_raises_at_scale_on_red(monkeypatch):
    from goldenmatch.core.autoconfig import auto_configure_df

    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    with pytest.raises(ControllerNotConfidentError):
        auto_configure_df(_df(REFUSE_AT_N))


def test_auto_configure_df_confidence_required_false_returns_v0(monkeypatch):
    from goldenmatch.core.autoconfig import auto_configure_df

    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    cfg = auto_configure_df(_df(REFUSE_AT_N), confidence_required=False)
    assert cfg is not None


def test_match_df_default_raises_at_scale_on_red(monkeypatch):
    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    target = _df(REFUSE_AT_N)
    reference = _df(100)
    with pytest.raises(ControllerNotConfidentError):
        gm.match_df(target, reference)


def test_match_df_confidence_required_false_short_circuits(monkeypatch):
    _force_red_history(monkeypatch, n_rows_in_df=REFUSE_AT_N)
    target = _df(REFUSE_AT_N)
    reference = _df(100)
    result = gm.match_df(target, reference, confidence_required=False)
    assert result is not None
