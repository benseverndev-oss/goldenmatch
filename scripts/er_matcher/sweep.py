#!/usr/bin/env python3
"""Pure learning-curve sweep aggregation for the ER-matcher training gate
(SP2 Task 5).

The training "sweep" trains on data slices (10/25/50/100%) and collects
eval-loss per slice; this module defines the slice fractions and assembles
the ``learning_curve`` shape that ``perf_report.learning_curve_slope``
consumes. Stdlib only, box-safe (no torch/network)."""
from __future__ import annotations


def data_fractions() -> list[float]:
    """Ascending data-slice fractions used by the learning-curve sweep."""
    return [0.1, 0.25, 0.5, 1.0]


def slice_len(total: int, frac: float) -> int:
    """Number of rows for a ``frac`` slice of ``total``, floored at 1 so even
    a tiny fraction of a small dataset still yields a non-empty slice."""
    return max(1, round(total * frac))


def build_learning_curve(points: list[tuple[float, float]]) -> list[dict[str, float]]:
    """Assemble ``(frac, eval_loss)`` pairs into the ``learning_curve`` shape
    (``[{"frac": frac, "eval_loss": loss}, ...]``, ascending by ``frac``) that
    ``perf_report.learning_curve_slope`` consumes."""
    return [
        {"frac": frac, "eval_loss": loss}
        for frac, loss in sorted(points, key=lambda p: p[0])
    ]
