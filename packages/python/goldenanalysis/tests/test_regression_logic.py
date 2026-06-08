"""Pure regression decision logic."""

from __future__ import annotations

from goldenanalysis._regressions import baseline_value, is_regression
from goldenanalysis.models import RegressionPolicy

HEALTHY = [0.97, 0.96, 0.98, 0.97, 0.97, 0.96, 0.97]


def test_baseline_strategies() -> None:
    assert baseline_value(HEALTHY, "rolling_median", window=7) == 0.97
    assert baseline_value(HEALTHY, "previous") == 0.97
    assert baseline_value([], "rolling_median") is None


def test_rolling_median_ignores_one_noisy_night() -> None:
    noisy = [0.97, 0.97, 0.97, 0.50, 0.97, 0.97, 0.97]  # one bad night
    assert baseline_value(noisy, "rolling_median", window=7) == 0.97  # median unmoved
    assert baseline_value(noisy, "previous") == 0.97


def test_recall_safe_bound_flags_under_per_metric_gate() -> None:
    policy = RegressionPolicy(default_pct=10.0, per_metric={"match.recall_safe_bound": 2.0})
    # higher_better: 0.97 -> 0.89 is -8.2%.
    assert is_regression("higher_better", 0.97, 0.89, policy.threshold_for("match.recall_safe_bound"))
    # The SAME drop does NOT flag under the global 10% gate.
    assert not is_regression("higher_better", 0.97, 0.89, policy.threshold_for("anything_else"))


def test_direction_aware() -> None:
    # higher_better only flags on a drop; a rise is fine.
    assert not is_regression("higher_better", 0.5, 0.9, 5.0)
    # lower_better only flags on a rise.
    assert is_regression("lower_better", 100, 130, 10.0)
    assert not is_regression("lower_better", 100, 70, 10.0)
    # neutral flags either way.
    assert is_regression("neutral", 0.58, 0.71, 10.0)  # +22.4%
    assert is_regression("neutral", 0.71, 0.58, 10.0)


def test_noise_wobble_does_not_flag() -> None:
    # +3% on a default 10% gate metric.
    assert not is_regression("neutral", 1.0, 1.03, 10.0)
