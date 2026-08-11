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


def test_amazon_google_is_floored_at_its_observed_value_not_below():
    """The floor stops Amazon-Google getting WORSE; it does not bless 0.0697.

    Being explicit about this because the floor is set at the observed value, so
    the run that motivated #2470 sits exactly ON the boundary and passes. That is
    deliberate -- the gate's job is catching regressions, not failing every run
    until product matching improves -- but it means the guarantee here is
    narrower than "bad runs fail", and the test should say so.
    """
    observed = run_benchmarks._check_quality_floors(
        [{"name": "Amazon-Google", "f1": 0.0697, "precision": 0.2077, "recall": 0.0419}]
    )
    assert observed == [], "the observed run sits on the floor and is not a breach"

    worse = run_benchmarks._check_quality_floors(
        [{"name": "Amazon-Google", "f1": 0.04, "precision": 0.2, "recall": 0.03}]
    )
    assert worse, "a WORSE Amazon-Google run must breach the floor"
    assert "Amazon-Google" in worse[0]


def test_a_healthy_run_does_not_breach():
    breaches = run_benchmarks._check_quality_floors(
        [
            {"name": "Febrl3", "f1": 0.9912, "precision": 0.99, "recall": 0.99},
            {"name": "Abt-Buy", "f1": 0.5037, "precision": 0.82, "recall": 0.36},
        ]
    )
    assert breaches == [], breaches


def test_red_controller_health_fails_regardless_of_f1():
    """A RED config means auto-config never converged, so the metrics are not
    trustworthy even when they clear the floor. Elsewhere RED is a reasonable
    degradation; in a lane whose only job is measuring quality it is a FALSE
    RESULT."""
    breaches = run_benchmarks._check_quality_floors(
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
