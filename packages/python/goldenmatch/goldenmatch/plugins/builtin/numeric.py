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
