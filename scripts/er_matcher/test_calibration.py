"""Tests for pure temperature-scaling calibration (SP3 Task 1).

Post-hoc calibration: fit a scalar temperature T minimizing NLL over per-pair
logits + binary match labels, then apply sigmoid(z/T) -- pure stdlib (math),
box-safe (no torch/scipy/numpy/network)."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))


from calibration import (  # noqa: E402
    apply_temperature,
    ece_from_probs,
    fit_temperature,
    logit,
    sigmoid,
)


def test_sigmoid_logit_roundtrip():
    for p in (0.1, 0.5, 0.9):
        assert abs(sigmoid(logit(p)) - p) < 1e-9


def test_fit_temperature_softens_overconfident():
    logits = [4.0, 4.0, -4.0, -4.0, 4.0]       # P(match) ~0.98/0.02 -- overconfident
    labels = [True, False, False, True, True]  # several wrong
    assert fit_temperature(logits, labels) > 1.0   # softening


def test_apply_temperature():
    assert abs(apply_temperature(2.0, T=2.0) - sigmoid(1.0)) < 1e-9


def test_well_calibrated_T_near_one():
    # NON-degenerate + already-calibrated: P=0.7 rows 70% True, P=0.3 rows 30% True -> T~1.
    # (Do NOT use logits=[0.0]*N -- sigmoid(0/T)=0.5 for all T -> flat/underdetermined.)
    logits = [logit(0.7)] * 100 + [logit(0.3)] * 100
    labels = [i < 70 for i in range(100)] + [i < 30 for i in range(100)]
    assert 0.7 < fit_temperature(logits, labels) < 1.4


def test_ece_from_probs_perfect_is_zero():
    # probs exactly equal to per-bin accuracy -> ~0 ECE
    probs = [0.9] * 90 + [0.1] * 10
    labels = [i < 81 for i in range(90)] + [i < 1 for i in range(10)]  # ~0.9 / ~0.1 accurate
    assert ece_from_probs(probs, labels) < 0.15
