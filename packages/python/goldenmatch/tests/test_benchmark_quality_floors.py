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


def test_amazon_google_at_its_quarantine_baseline_is_reported_not_failed():
    """Amazon-Google is quarantined (#2717), so its breaches report, not fail.

    This test used to assert the 0.0697 run "sits on the floor and is not a
    breach". That is no longer the contract, and the change is deliberate:
    0.0697 was the #2470 run, and the CURRENT observed value is 0.1014. Feeding
    0.0697 today is a real DEGRADATION and the ratchet fails it -- which is the
    behaviour the original test wanted ("a WORSE run must breach"), now enforced
    against a live baseline instead of a static floor.
    """
    base = run_benchmarks._QUARANTINE["Amazon-Google (dedupe)"]["f1_at_quarantine"]
    failing, quarantined = run_benchmarks._check_quality_floors(
        [{"name": "Amazon-Google (dedupe)", "f1": base, "precision": 0.2077, "recall": 0.0419}]
    )
    assert failing == [], f"at its own baseline it must not fail: {failing}"

    worse = run_benchmarks._check_quality_floors(
        [{"name": "Amazon-Google (dedupe)", "f1": 0.04, "precision": 0.2, "recall": 0.03}]
    )[0]
    assert worse, "a WORSE Amazon-Google run must still fail"
    assert any("Amazon-Google" in w for w in worse), worse


def test_a_healthy_run_does_not_breach():
    """Abt-Buy sits at its quarantine baseline here, not at 0.5037.

    0.5037 is the DISPUTED, unreproduced number (see the `Abt-Buy` note in
    `_F1_FLOORS`); every reproduction gives 0.1723. Feeding it now trips the
    IMPROVED ratchet, which is correct -- a jump that size means someone fixed
    it and the quarantine should be lifted. Using it as "a healthy run" was
    baking an unreproducible measurement into a test.
    """
    base = run_benchmarks._QUARANTINE["Abt-Buy (dedupe)"]["f1_at_quarantine"]
    failing, _ = run_benchmarks._check_quality_floors(
        [
            {"name": "Febrl3", "f1": 0.9912, "precision": 0.99, "recall": 0.99},
            {"name": "Abt-Buy (dedupe)", "f1": base, "precision": 0.11, "recall": 0.45},
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
