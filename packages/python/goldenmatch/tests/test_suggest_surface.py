"""Tests for the free headroom trigger in goldenmatch.core.suggest.surface.

``headroom_signal(result)`` is PURE and FREE: it reads only
``result.postflight_report.controller_history`` (already computed by the
controller) and returns a ``HeadroomReason`` when the committed run shows
degraded health (RED/YELLOW) or a score-histogram dip; ``None`` otherwise.
It never raises and never calls the kernel / re-runs the pipeline.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest
from goldenmatch.core.complexity_profile import (
    ComplexityProfile,
    HealthVerdict,
    ScoringProfile,
)
from goldenmatch.core.suggest.surface import HeadroomReason, headroom_signal

# --- minimal fakes for the controller_history shape -------------------------


@dataclass
class _FakeEntry:
    """Stands in for HistoryEntry: exposes a ``.profile``."""

    profile: object


class _FakeHistory:
    """Stands in for RunHistory: ``pick_committed()`` returns the entry."""

    def __init__(self, entry):
        self._entry = entry

    def pick_committed(self, *args, **kwargs):  # match the real no-arg-callable form
        return self._entry


class _FakeReport:
    def __init__(self, history):
        self.controller_history = history


class _FakeResult:
    def __init__(self, report):
        self.postflight_report = report


def _result_for_profile(profile) -> _FakeResult:
    return _FakeResult(_FakeReport(_FakeHistory(_FakeEntry(profile))))


def _profile(health: HealthVerdict, dip: float = 0.0) -> ComplexityProfile:
    """A real ComplexityProfile whose health() yields ``health`` and whose
    scoring carries ``dip``. We drive health() via a tiny subclass override so
    we don't have to hand-craft sub-profiles for every verdict."""

    class _P(ComplexityProfile):
        def health(self_inner) -> HealthVerdict:  # noqa: N805
            return health

    return _P(scoring=ScoringProfile(dip_statistic=dip, n_pairs_scored=100))


# --- tests ------------------------------------------------------------------


def test_committed_red_returns_health_reason():
    reason = headroom_signal(_result_for_profile(_profile(HealthVerdict.RED)))
    assert reason is not None
    assert isinstance(reason, HeadroomReason)
    assert "health" in reason.kind
    assert "RED" in reason.kind or "red" in reason.kind


def test_committed_yellow_returns_health_reason():
    reason = headroom_signal(_result_for_profile(_profile(HealthVerdict.YELLOW)))
    assert reason is not None
    assert "health" in reason.kind


def test_green_no_dip_returns_none():
    assert headroom_signal(_result_for_profile(_profile(HealthVerdict.GREEN, dip=0.0))) is None


def test_green_with_dip_returns_dip_reason():
    reason = headroom_signal(_result_for_profile(_profile(HealthVerdict.GREEN, dip=0.2)))
    assert reason is not None
    assert reason.kind == "dip"


def test_postflight_report_none_returns_none():
    assert headroom_signal(_FakeResult(None)) is None


def test_controller_history_none_returns_none():
    # explicit-config path: controller never ran
    assert headroom_signal(_FakeResult(_FakeReport(None))) is None


def test_malformed_history_never_raises():
    class _Boom:
        def pick_committed(self, *a, **k):
            raise RuntimeError("boom")

    result = _FakeResult(_FakeReport(_Boom()))
    assert headroom_signal(result) is None


def test_committed_none_returns_none():
    # pick_committed() can legitimately return None (every entry errored)
    class _NoneHistory:
        def pick_committed(self, *a, **k):
            return None

    assert headroom_signal(_FakeResult(_FakeReport(_NoneHistory()))) is None


def test_result_without_postflight_attr_returns_none():
    assert headroom_signal(object()) is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
