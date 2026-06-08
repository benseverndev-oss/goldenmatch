"""Pure regression decision logic — baseline strategy + direction-aware policy.

Backend-free: operates on a list of ``(run_id, value)`` history points + the
current value. ``ReportHistory`` wires storage around this.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence

from goldenanalysis.models import Direction, RegressionPolicy


def baseline_value(history: Sequence[float], strategy: str, *, window: int = 7) -> float | None:
    """The baseline to compare the current value against.

    - ``"previous"`` / ``"last_known_good"``: the most recent historical value
      (v1: ``last_known_good`` aliases ``previous`` until a health signal exists).
    - ``"rolling_median"``: median of the last ``window`` historical values — immune
      to one noisy night, where ``previous`` would alternately flag and un-flag.
    - any other string is treated as a pinned ``run_id`` and is resolved by the
      caller (which has the run->value map); here it falls through to ``previous``.

    Returns None when there's no history to compare against.
    """
    if not history:
        return None
    if strategy == "rolling_median":
        tail = list(history[-window:])
        return float(statistics.median(tail))
    # "previous", "last_known_good", or a pinned id resolved upstream.
    return float(history[-1])


def delta_pct(baseline: float, current: float) -> float:
    if baseline == 0:
        return 0.0
    return (current - baseline) / baseline * 100.0


def is_regression(direction: Direction, baseline: float, current: float, threshold_pct: float) -> bool:
    """Direction-aware: a higher_better metric flags only on a DROP beyond the
    threshold; lower_better only on a RISE; neutral on either direction."""
    d = delta_pct(baseline, current)
    if direction == "higher_better":
        return d <= -threshold_pct
    if direction == "lower_better":
        return d >= threshold_pct
    return abs(d) >= threshold_pct


def evaluate_metric(
    *,
    key: str,
    direction: Direction,
    history: Sequence[float],
    current: float,
    strategy: str,
    window: int,
    policy: RegressionPolicy,
):
    """Return a ``Regression`` for one metric, or None when there's no baseline.

    ``flagged`` reflects the direction-aware per-metric gate; the record is always
    returned (when a baseline exists) so callers can show near-misses if they want.
    """
    from goldenanalysis.models import Regression

    base = baseline_value(history, strategy, window=window)
    if base is None:
        return None
    threshold = policy.threshold_for(key)
    return Regression(
        metric=key,
        baseline=base,
        current=current,
        delta_pct=delta_pct(base, current),
        flagged=is_regression(direction, base, current, threshold),
        direction=direction,
    )
