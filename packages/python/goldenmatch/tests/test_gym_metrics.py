import math

from scripts.suggest_quality.metrics import recovery_pct


def test_full_recovery_is_one():
    assert abs(recovery_pct(0.70, 0.90, 0.90) - 1.0) < 1e-9

def test_half_recovery():
    assert abs(recovery_pct(0.70, 0.80, 0.90) - 0.5) < 1e-9

def test_overshoot_above_one():
    assert recovery_pct(0.70, 0.95, 0.90) > 1.0

def test_negative_when_made_worse():
    assert recovery_pct(0.70, 0.60, 0.90) < 0.0

def test_no_damage_returns_nan():
    assert math.isnan(recovery_pct(0.90, 0.90, 0.90))
    assert math.isnan(recovery_pct(0.899, 0.90, 0.90))  # gap 0.001 < 0.005
