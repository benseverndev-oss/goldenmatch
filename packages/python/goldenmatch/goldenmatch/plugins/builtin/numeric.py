"""Numeric-aggregate predefined plugins.

Each plugin satisfies ``GoldenStrategyPlugin`` from
``goldenmatch.plugins.base``. Non-numeric or null values are ignored;
all-null / all-unparseable input yields ``(None, 0.0)``.

Spec: ``docs/superpowers/specs/2026-05-22-predefined-merge-plugins-design.md``
"""
from __future__ import annotations

from typing import Any


def _to_float(value: Any) -> float | None:
    """Best-effort numeric coercion. Returns None for unparseable."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class NumericMaxStrategy:
    """Pick the largest numeric value in the cluster.

    Non-numeric values are ignored. All-non-numeric or all-null
    input -> (None, 0.0).

    Confidence: 1.0 when unique max; 0.7 on ties (first index wins).
    """

    name = "numeric_max"

    def merge(self, values: list, **_: Any) -> Any:
        nums = [(i, _to_float(v), v) for i, v in enumerate(values)]
        nums = [(i, f, v) for i, f, v in nums if f is not None]
        if not nums:
            return (None, 0.0)
        max_f = max(f for _, f, _ in nums)
        tied = [(i, v) for i, f, v in nums if f == max_f]
        conf = 1.0 if len(tied) == 1 else 0.7
        return (tied[0][1], conf, tied[0][0])


class NumericMinStrategy:
    """Pick the smallest numeric value. Same shape as `numeric_max`."""

    name = "numeric_min"

    def merge(self, values: list, **_: Any) -> Any:
        nums = [(i, _to_float(v), v) for i, v in enumerate(values)]
        nums = [(i, f, v) for i, f, v in nums if f is not None]
        if not nums:
            return (None, 0.0)
        min_f = min(f for _, f, _ in nums)
        tied = [(i, v) for i, f, v in nums if f == min_f]
        conf = 1.0 if len(tied) == 1 else 0.7
        return (tied[0][1], conf, tied[0][0])


class NumericMeanStrategy:
    """Pick the arithmetic mean of numeric values.

    Returned value is a Python float. Confidence reflects coverage:
    `non_null_count / total_count`. Useful for `account_balance`,
    `risk_score`, `confidence`-like fields where averaging across
    sources is sensible.

    NOTE: the mean is a synthesized value (no source record holds it),
    so the returned ``idx`` is 0 (no real provenance). Callers that
    need strict provenance should use `numeric_max` or `numeric_min`.
    """

    name = "numeric_mean"

    def merge(self, values: list, **_: Any) -> Any:
        nums = [_to_float(v) for v in values]
        valid = [f for f in nums if f is not None]
        if not valid:
            return (None, 0.0)
        mean = sum(valid) / len(valid)
        conf = len(valid) / len(values)
        return (mean, conf, 0)


class NumericMedianStrategy:
    """Pick the median numeric value -- resilient to outliers vs mean.

    For even counts, returns the lower-of-two-middles (no synthesized
    interpolation) so the returned value preserves a real source row's
    index. For odd counts, the unique middle value is returned with
    its real idx.

    Non-numeric values ignored. All-non-numeric -> (None, 0.0).
    Confidence: `non_null_count / total_count`.
    """

    name = "numeric_median"

    def merge(self, values: list, **_: Any) -> Any:
        candidates = [(i, _to_float(v), v) for i, v in enumerate(values)]
        candidates = [(i, f, v) for i, f, v in candidates if f is not None]
        if not candidates:
            return (None, 0.0)
        sorted_by_value = sorted(candidates, key=lambda x: x[1])
        n = len(sorted_by_value)
        # For even count, pick the lower middle (index n//2 - 1) so
        # the returned value preserves a real source's idx.
        mid_idx = (n - 1) // 2
        i, _f, v = sorted_by_value[mid_idx]
        conf = len(candidates) / len(values)
        return (v, conf, i)


class NumericSumStrategy:
    """Sum of numeric values. Useful for aggregating amounts / balances
    across sources where the golden record should reflect a total.

    Returned idx is 0 (synthesized value, no real provenance).
    Confidence: 1.0 when at least one non-null exists; 0.0 otherwise.
    """

    name = "numeric_sum"

    def merge(self, values: list, **_: Any) -> Any:
        nums = [_to_float(v) for v in values]
        valid = [f for f in nums if f is not None]
        if not valid:
            return (None, 0.0)
        total = sum(valid)
        return (total, 1.0, 0)


class NumericWeightedAverageStrategy:
    """Quality-weighted average of numeric values.

    Uses ``quality_weights`` (from the dispatcher; same length as
    ``values``) when available. Falls back to uniform-weighted
    average when weights are absent. Non-numeric values are excluded
    along with their weights.

    Confidence: `sum(non_null_weights) / sum(all_weights)` -- captures
    how much of the input's total weight was usable. Falls back to
    `non_null_count / len(values)` when no weights provided.

    Synthesized value; idx=0.
    """

    name = "numeric_weighted_average"

    def merge(
        self,
        values: list,
        *,
        quality_weights: list[float] | None = None,
        **_: Any,
    ) -> Any:
        pairs: list[tuple[float, float]] = []
        for i, v in enumerate(values):
            f = _to_float(v)
            if f is None:
                continue
            w = (
                float(quality_weights[i])
                if quality_weights is not None and i < len(quality_weights)
                else 1.0
            )
            if w <= 0:
                continue
            pairs.append((f, w))
        if not pairs:
            return (None, 0.0)
        total_weight = sum(w for _, w in pairs)
        if total_weight == 0:
            return (None, 0.0)
        avg = sum(f * w for f, w in pairs) / total_weight
        if quality_weights is not None:
            all_weight = sum(
                max(float(w), 0.0) for w in quality_weights[: len(values)]
            )
            conf = total_weight / all_weight if all_weight > 0 else 1.0
        else:
            conf = len(pairs) / len(values)
        return (avg, conf, 0)
