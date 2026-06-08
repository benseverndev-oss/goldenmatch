"""Cross-run models: ``Baseline``, ``RegressionPolicy``, ``Regression``, ``TrendSeries``.

These drive ``ReportHistory.detect_regressions`` / ``trend`` (Phase 2b). The two
decisions the spec's worked scenario forced live here: the baseline is a *strategy*
(not just "previous"), and regression thresholds are *per-metric* and respect each
``Metric.direction``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from goldenanalysis.models.report import Direction

# "previous" / "rolling_median" / "last_known_good", or a pinned run_id string.
Baseline = Literal["previous", "rolling_median", "last_known_good"] | str


class RegressionPolicy(BaseModel):
    """Per-metric regression thresholds (percent). Falls back to ``default_pct``.

    ``direction`` comes from the ``Metric`` itself, so a ``lower_better`` metric only
    flags on an INCREASE and a ``higher_better`` metric only on a DECREASE.
    """

    default_pct: float = 10.0
    per_metric: dict[str, float] = Field(default_factory=dict)

    def threshold_for(self, key: str) -> float:
        return self.per_metric.get(key, self.default_pct)


class Regression(BaseModel):
    """A flagged (or considered) metric movement vs a baseline."""

    metric: str
    baseline: float
    current: float
    delta_pct: float
    flagged: bool
    direction: Direction = "neutral"


class TrendSeries(BaseModel):
    """A metric's value across a run history, oldest -> newest."""

    metric_key: str
    dataset: str
    points: list[tuple[str, float]] = Field(default_factory=list)  # (run_id, value)
