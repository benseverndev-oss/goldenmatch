"""Cross-run model contracts."""

from __future__ import annotations

from goldenanalysis.models import Regression, RegressionPolicy, TrendSeries


def test_policy_threshold_fallback() -> None:
    p = RegressionPolicy(default_pct=10.0, per_metric={"match.recall_safe_bound": 2.0})
    assert p.threshold_for("match.recall_safe_bound") == 2.0
    assert p.threshold_for("other") == 10.0


def test_regression_roundtrip() -> None:
    r = Regression(
        metric="match.recall_safe_bound",
        baseline=0.97,
        current=0.89,
        delta_pct=-8.2,
        flagged=True,
        direction="higher_better",
    )
    again = Regression.model_validate_json(r.model_dump_json())
    assert again == r


def test_trend_series_holds_points() -> None:
    ts = TrendSeries(metric_key="cluster.singleton_ratio", dataset="customers", points=[("r1", 0.58), ("r2", 0.71)])
    assert ts.points[-1] == ("r2", 0.71)
