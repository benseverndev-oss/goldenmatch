"""Quarantine routing in run_benchmarks.py.

The lane's whole job is to fail when quality moves. Quarantine deliberately
suppresses a failure, so it is the one piece of that logic that can silently
stop the lane working -- these pin that it suppresses ONLY what it claims to.

Network-free and dataset-free: `_check_quality_floors` is a pure function over
result dicts, so the cases below are the real ones the nightly produces.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from run_benchmarks import (  # noqa: E402
    _F1_FLOORS,
    _QUARANTINE,
    _check_quality_floors,
)


def _r(name, f1, health="green", stop_reason="converged"):
    return {"name": name, "f1": f1, "precision": 0.1, "recall": 0.1,
            "health": health, "stop_reason": stop_reason}


# --- the two datasets that made #2457 permanently red -----------------------

def test_the_2026_08_21_nightly_no_longer_fails_the_lane():
    """The exact numbers from run 32457009104, which had been failing nightly."""
    failing, quarantined = _check_quality_floors([
        _r("Abt-Buy", 0.1723, "red", "budget_iterations"),
        _r("Amazon-Google", 0.1014, "red", "budget_time"),
    ])
    assert failing == [], f"quarantined datasets still failed the lane: {failing}"
    assert len(quarantined) == 3, quarantined  # Abt-Buy floor + 2 RED healths


def test_quarantined_breaches_are_still_reported():
    """Suppressing the failure must not suppress the evidence."""
    _, quarantined = _check_quality_floors([_r("Abt-Buy", 0.1723, "red", "x")])
    assert quarantined, "a quarantined breach vanished entirely"
    assert all("QUARANTINED" in q and "#" in q for q in quarantined), quarantined


# --- the ratchet, both directions -------------------------------------------

def test_degrading_past_the_baseline_fails():
    """A quarantine tracks a bug; it does not license it to deepen."""
    base = _QUARANTINE["Abt-Buy"]["f1_at_quarantine"]
    failing, _ = _check_quality_floors([_r("Abt-Buy", base - 0.2, "green")])
    assert any("DEGRADED" in f for f in failing), failing


def test_improving_past_the_baseline_fails():
    """Someone fixed it -- the quarantine now hides good news, so it must shout."""
    base = _QUARANTINE["Abt-Buy"]["f1_at_quarantine"]
    failing, _ = _check_quality_floors([_r("Abt-Buy", base + 0.5, "green")])
    assert any("IMPROVED" in f for f in failing), failing


def test_inside_tolerance_stays_quarantined():
    base = _QUARANTINE["Abt-Buy"]["f1_at_quarantine"]
    tol = _QUARANTINE["Abt-Buy"]["tolerance"]
    failing, _ = _check_quality_floors([_r("Abt-Buy", base + tol / 2, "green")])
    assert failing == [], failing


def test_a_missing_f1_is_not_drift():
    """A crashed/skipped run has no number; inventing a verdict would be worse."""
    failing, _ = _check_quality_floors([
        {"name": "Abt-Buy", "f1": None, "health": "green", "stop_reason": "x"}
    ])
    assert not any("DEGRADED" in f or "IMPROVED" in f for f in failing), failing


# --- the guard: non-quarantined datasets must still fail --------------------

def test_a_non_quarantined_dataset_still_fails_on_floor():
    """Guards every test above: they pass trivially if nothing fails any more."""
    failing, _ = _check_quality_floors([_r("Febrl3", 0.10, "green")])
    assert any("below the floor" in f for f in failing), failing


def test_a_non_quarantined_dataset_still_fails_on_red():
    failing, _ = _check_quality_floors([_r("Febrl3", 0.99, "red", "budget_time")])
    assert any("RED" in f for f in failing), failing


def test_every_quarantine_entry_names_an_issue_and_a_known_dataset():
    for name, q in _QUARANTINE.items():
        assert isinstance(q.get("issue"), int), f"{name} has no issue number"
        assert isinstance(q.get("f1_at_quarantine"), float), name
        assert name in _F1_FLOORS, f"{name} is quarantined but has no floor entry"
