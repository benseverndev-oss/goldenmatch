"""Numeric helpers for profile emission. Used by scorer + cluster instrumentation."""
from __future__ import annotations


def histogram_20(scores: list[float]) -> list[int]:
    """20 fixed bins over [0, 1]. Score >= 1.0 lands in bin 19."""
    bins = [0] * 20
    for s in scores:
        idx = min(19, max(0, int(s * 20)))
        bins[idx] += 1
    return bins


def hartigan_dip(scores: list[float]) -> float:
    """Hartigan's dip statistic. Returns value in [0, 0.25]; small=unimodal.

    Hard-requires the ``diptest`` package (added as dep in Task 2.2).
    """
    if not scores:
        return 0.0
    import numpy as np
    import diptest
    return float(diptest.dipstat(np.asarray(scores)))


def mass_above(scores: list[float], threshold: float) -> float:
    if not scores:
        return 0.0
    return sum(1 for s in scores if s >= threshold) / len(scores)


def mass_borderline(scores: list[float], threshold: float, band: float = 0.1) -> float:
    if not scores:
        return 0.0
    lo, hi = threshold - band, threshold + band
    return sum(1 for s in scores if lo <= s <= hi) / len(scores)
