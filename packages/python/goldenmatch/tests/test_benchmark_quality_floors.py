"""#2470: the benchmarks lane must be able to FAIL on quality, not just crashes.

Run 31414594892 was GREEN with Amazon-Google at f1=0.0697 / recall=0.0419 --
finding 4.2% of true matches -- while the engine had itself logged that it
committed a best-effort RED config. A lane that cannot fail measures nothing.

These pin the gate rather than the floor VALUES: the numbers are expected to move
as the matcher improves, but "a bad run fails" must not.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# repo root: tests/ -> goldenmatch/ -> python/ -> packages/ -> ROOT
_SCRIPTS = Path(__file__).resolve().parents[4] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

run_benchmarks = pytest.importorskip("run_benchmarks")


#: A synthetic quarantined lane. These tests used to borrow the live entries
#: for "Amazon-Google (dedupe)" / "Abt-Buy (dedupe)", but those rows were
#: RETIRED 2026-08-24 -- measurement showed 98.1% of their ground truth was
#: cross-source, so they were linkage wearing a dedupe API and were not
#: measuring deduplication at all. `_QUARANTINE` is empty as a result.
#:
#: The gate these tests exist for is not tied to any particular lane, so drive
#: it with a fixture instead. Borrowing live entries is what coupled the
#: MECHANISM's tests to whichever rows happened to be quarantined, and it is
#: why retiring two rows broke a test file that has nothing to do with them.
_LANE = "Synthetic (quarantined)"
_BASE = 0.20


@pytest.fixture
def quarantined_lane(monkeypatch):
    monkeypatch.setattr(run_benchmarks, "_QUARANTINE", {
        _LANE: {"issue": 2748, "f1_at_quarantine": _BASE, "tolerance": 0.03,
                "why": "synthetic fixture for the quality-gate tests"},
    })
    monkeypatch.setattr(run_benchmarks, "_F1_FLOORS", {_LANE: None, "Febrl3": 0.90})


def test_a_quarantined_lane_at_its_baseline_is_reported_not_failed(quarantined_lane):
    """A quarantined lane's breaches report rather than fail -- but the ratchet
    still fails a run that DEGRADES past the baseline, which is the behaviour
    #2470 wanted ("a WORSE run must breach"), enforced against a live baseline
    instead of a static floor."""
    failing, _quarantined = run_benchmarks._check_quality_floors(
        [{"name": _LANE, "f1": _BASE, "precision": 0.2077, "recall": 0.0419}]
    )
    assert failing == [], f"at its own baseline it must not fail: {failing}"

    worse = run_benchmarks._check_quality_floors(
        [{"name": _LANE, "f1": _BASE - 0.16, "precision": 0.2, "recall": 0.03}]
    )[0]
    assert worse, "a WORSE run must still fail"
    assert any(_LANE in w for w in worse), worse


def test_a_healthy_run_does_not_breach(quarantined_lane):
    """A clean lane above its floor, alongside a quarantined lane sitting
    exactly at its baseline, must produce no breaches at all."""
    failing, _ = run_benchmarks._check_quality_floors(
        [
            {"name": "Febrl3", "f1": 0.9912, "precision": 0.99, "recall": 0.99},
            {"name": _LANE, "f1": _BASE, "precision": 0.11, "recall": 0.45},
        ]
    )
    assert failing == [], failing


def test_red_controller_health_fails_regardless_of_f1():
    """A RED config means auto-config never converged, so the metrics are not
    trustworthy even when they clear the floor. Elsewhere RED is a reasonable
    degradation; in a lane whose only job is measuring quality it is a FALSE
    RESULT."""
    breaches, _ = run_benchmarks._check_quality_floors(
        [
            {
                "name": "Febrl3",
                "f1": 0.99,  # comfortably above its floor
                "health": "RED",
                "stop_reason": "BUDGET_ITERATIONS",
            }
        ]
    )
    assert breaches, "RED health must fail even with a passing F1"
    assert "RED" in breaches[0]


def test_a_dataset_without_a_floor_is_not_silently_passed():
    """`None` means 'no trustworthy baseline yet'. That must be an explicit entry
    in the table, so an unfloored dataset is visible rather than merely absent."""
    assert "DBLP-ACM" in run_benchmarks._F1_FLOORS
    assert run_benchmarks._F1_FLOORS["DBLP-ACM"] is None


def test_every_floor_is_a_float_or_explicit_none():
    for name, floor in run_benchmarks._F1_FLOORS.items():
        assert floor is None or isinstance(floor, float), (name, floor)
        if isinstance(floor, float):
            assert 0.0 <= floor <= 1.0, (name, floor)
