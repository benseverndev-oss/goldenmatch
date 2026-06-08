"""Pure-Python/Polars aggregation primitives.

This module is the **byte-identical reference** for the optional Rust accelerator
(``analysis-core``, Phase 4). Keep it deterministic: stable bin edges, no reliance
on float-key dict ordering, linear-interpolation quantiles (numpy default).
"""

from __future__ import annotations

from collections.abc import Sequence

import polars as pl


def null_ratio_per_column(df: pl.DataFrame) -> dict[str, float]:
    """Per-column null fraction. Empty frame => 0.0 for every column."""
    n = df.height
    if n == 0:
        return {c: 0.0 for c in df.columns}
    return {c: df[c].null_count() / n for c in df.columns}


def duplicate_row_ratio(df: pl.DataFrame) -> float:
    """Fraction of rows participating in an exact-duplicate group (size >= 2).

    Every member of a duplicate group counts, not just the redundant copies: one
    identical pair among five rows => 2/5 == 0.4.
    """
    n = df.height
    if n == 0:
        return 0.0
    return int(df.is_duplicated().sum()) / n


def histogram(values: Sequence[float], bins: int) -> list[tuple[float, int]]:
    """Equal-width histogram over ``[min, max]``.

    Returns ``[(left_edge, count), ...]`` with ``bins`` entries. The right edge is
    inclusive (the max lands in the last bin). All-equal input collapses to a
    single ``[(value, count)]`` bin. Empty input or ``bins < 1`` => ``[]``.
    """
    vals = [float(v) for v in values if v is not None]
    if not vals or bins < 1:
        return []
    lo, hi = min(vals), max(vals)
    if hi == lo:
        return [(lo, len(vals))]
    width = (hi - lo) / bins
    counts = [0] * bins
    for v in vals:
        idx = int((v - lo) / width)
        if idx >= bins:  # right-edge inclusive
            idx = bins - 1
        counts[idx] += 1
    return [(lo + i * width, counts[i]) for i in range(bins)]


def quantile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (numpy default). Empty input => 0.0."""
    vals = sorted(float(v) for v in values if v is not None)
    if not vals:
        return 0.0
    if len(vals) == 1:
        return vals[0]
    pos = q * (len(vals) - 1)
    lo_idx = int(pos)
    frac = pos - lo_idx
    if lo_idx + 1 < len(vals):
        return vals[lo_idx] + (vals[lo_idx + 1] - vals[lo_idx]) * frac
    return vals[lo_idx]
