"""Quarantine routing in run_benchmarks.py.

The lane's whole job is to fail when quality moves. Quarantine deliberately
suppresses a failure, so it is the one piece of that logic that can silently
stop the lane working -- these pin that it suppresses ONLY what it claims to.

Network-free and dataset-free: `_check_quality_floors` is a pure function over
result dicts, so the cases below are the real ones the nightly produces.

These used to borrow the live `_QUARANTINE` entries as fixtures. That coupled
the MECHANISM's tests to whichever lanes happened to be quarantined, and when
the two product `(dedupe)` rows were retired 2026-08-24 the dict emptied and
every test here would have gone vacuous -- passing while testing nothing, at
exactly the moment nothing else covered the mechanism. They now drive a
SYNTHETIC entry, so the routing stays under test whether or not any real lane
is quarantined.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import run_benchmarks  # type: ignore[import-not-found]  # noqa: E402
from run_benchmarks import (  # noqa: E402
    _F1_FLOORS,
    _QUARANTINE,
    _check_quality_floors,
)

_LANE = "Synthetic (quarantined)"
_BASE = 0.20
_TOL = 0.03


@pytest.fixture(autouse=True)
def synthetic_quarantine(monkeypatch):
    """One quarantined lane and one un-quarantined lane, both floored."""
    monkeypatch.setattr(run_benchmarks, "_QUARANTINE", {
        _LANE: {"issue": 2748, "f1_at_quarantine": _BASE, "tolerance": _TOL,
                "why": "synthetic fixture for the routing tests"},
    })
    monkeypatch.setattr(run_benchmarks, "_F1_FLOORS", {
        _LANE: None,
        "Synthetic (floored)": 0.50,
    })


def _r(name, f1, health="green", stop_reason="converged"):
    return {"name": name, "f1": f1, "precision": 0.1, "recall": 0.1,
            "health": health, "stop_reason": stop_reason}


def test_a_dataset_at_its_baseline_does_not_fail_the_lane():
    failing, quarantined = _check_quality_floors([
        _r(_LANE, _BASE, "red", "budget_iterations"),
    ])
    assert failing == [], f"quarantined dataset still failed the lane: {failing}"
    assert len(quarantined) == 1, quarantined


def test_quarantined_breaches_are_still_reported():
    """Suppressing the failure must not suppress the evidence."""
    _, quarantined = _check_quality_floors([_r(_LANE, _BASE, "red", "x")])
    assert quarantined, "a quarantined breach vanished entirely"
    assert all("QUARANTINED" in q and "#" in q for q in quarantined), quarantined


# --- the ratchet, both directions -------------------------------------------

def test_degrading_past_the_baseline_fails():
    """A quarantine tracks a bug; it does not license it to deepen."""
    failing, _ = _check_quality_floors([_r(_LANE, _BASE - 0.2, "green")])
    assert any("DEGRADED" in f for f in failing), failing


def test_improving_past_the_baseline_fails():
    """Someone fixed it -- the quarantine now hides good news, so it must shout.

    This is the check that drove the Abt-Buy dedupe row's baseline up twice as
    the controller improved, and then flagged that leaving it quarantined would
    hide the fix at all.
    """
    failing, _ = _check_quality_floors([_r(_LANE, _BASE + 0.5, "green")])
    assert any("IMPROVED" in f for f in failing), failing


def test_inside_tolerance_stays_quarantined():
    failing, _ = _check_quality_floors([_r(_LANE, _BASE + _TOL / 2, "green")])
    assert failing == [], failing


def test_a_missing_f1_is_not_drift():
    """A crashed/skipped run has no number; inventing a verdict would be worse."""
    failing, _ = _check_quality_floors([
        {"name": _LANE, "f1": None, "health": "green", "stop_reason": "x"}
    ])
    assert not any("DEGRADED" in f or "IMPROVED" in f for f in failing), failing


# --- the guard: non-quarantined datasets must still fail --------------------

def test_a_non_quarantined_dataset_still_fails_on_floor():
    """Guards every test above: they pass trivially if nothing fails any more."""
    failing, _ = _check_quality_floors([_r("Synthetic (floored)", 0.10, "green")])
    assert any("below the floor" in f for f in failing), failing


def test_a_non_quarantined_dataset_still_fails_on_red():
    failing, _ = _check_quality_floors([_r("Synthetic (floored)", 0.99, "red", "budget_time")])
    assert any("RED" in f for f in failing), failing


# --- the real table's own invariants ----------------------------------------

def test_every_real_quarantine_entry_names_an_issue_and_a_known_dataset():
    """Runs against the LIVE tables, not the fixture.

    Vacuous today -- `_QUARANTINE` is empty since the product dedupe rows were
    retired -- and deliberately kept: the moment someone quarantines a lane
    again, it has to carry an issue number and a floor entry.
    """
    for name, q in _QUARANTINE.items():
        assert isinstance(q.get("issue"), int), f"{name} has no issue number"
        assert isinstance(q.get("f1_at_quarantine"), float), name
        assert name in _F1_FLOORS, f"{name} is quarantined but has no floor entry"


def test_the_invariant_above_would_actually_catch_a_bad_entry(monkeypatch):
    """Pins that the check is real, since the live dict it reads is empty."""
    monkeypatch.setattr(run_benchmarks, "_QUARANTINE",
                        {"Bogus": {"f1_at_quarantine": 0.1, "tolerance": 0.03}})
    with pytest.raises(AssertionError):
        for name, q in run_benchmarks._QUARANTINE.items():
            assert isinstance(q.get("issue"), int), f"{name} has no issue number"
