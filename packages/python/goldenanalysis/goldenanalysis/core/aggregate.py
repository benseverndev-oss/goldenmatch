"""Pure-Python/Polars aggregation primitives.

The ``_*_pure`` helpers are the **byte-identical reference** for the optional Rust
accelerator (``analysis-core``, Phase 4). Keep them deterministic: stable bin edges,
no reliance on float-key dict ordering, linear-interpolation quantiles (numpy
default).

``histogram`` / ``quantile`` dispatch to the native kernel when it has cleared the
gate (``_native_loader._GATED_ON``); both were measured **5.8-9.9x faster** than the
pure Python loop on Linux x86_64 at 1M-10M rows, INCLUDING the list->Arrow conversion
(see ``benchmarks/aggregate_benchmark.py`` + ``bench-analysis-native.yml``). The
native output is byte-identical to ``_*_pure`` (``tests/core/test_native_parity.py``).
"""

from __future__ import annotations

import builtins
from collections.abc import Sequence

import polars as pl

from goldenanalysis.core._native_loader import native_enabled, native_module


def null_ratio_per_column(df: pl.DataFrame) -> dict[str, float]:
    """Per-column null fraction. Empty frame => 0.0 for every column.

    Dispatches to the native kernel when gated (byte-identical to the pure path);
    an unsupported dtype falls back to ``_null_ratio_per_column_pure``.
    """
    if native_enabled("null_ratio_per_column"):
        try:
            ratios = native_module().null_ratio_per_column([df[c].to_arrow() for c in df.columns])
            return dict(zip(df.columns, ratios))
        except (TypeError, ValueError):
            pass
    return _null_ratio_per_column_pure(df)


def _null_ratio_per_column_pure(df: pl.DataFrame) -> dict[str, float]:
    n = df.height
    if n == 0:
        return {c: 0.0 for c in df.columns}
    return {c: df[c].null_count() / n for c in df.columns}


def duplicate_row_ratio(df: pl.DataFrame) -> float:
    """Fraction of rows participating in an exact-duplicate group (size >= 2).

    Every member of a duplicate group counts, not just the redundant copies: one
    identical pair among five rows => 2/5 == 0.4.

    Dispatches to the native kernel when gated (byte-identical to the pure path);
    an unsupported dtype falls back to ``_duplicate_row_ratio_pure``.
    """
    if native_enabled("duplicate_row_ratio"):
        try:
            return native_module().duplicate_row_ratio([df[c].to_arrow() for c in df.columns])
        except (TypeError, ValueError):
            pass
    return _duplicate_row_ratio_pure(df)


def _duplicate_row_ratio_pure(df: pl.DataFrame) -> float:
    n = df.height
    if n == 0:
        return 0.0
    return int(df.is_duplicated().sum()) / n


def distinct_count(series: pl.Series) -> int:
    """Number of distinct values in a column (matches ``Series.n_unique``).

    Dispatches to the native kernel when gated (byte-identical to the pure path);
    an unsupported dtype falls back to ``_distinct_count_pure``.
    """
    if native_enabled("distinct_count"):
        try:
            return native_module().distinct_count(series.to_arrow())
        except (TypeError, ValueError):
            pass
    return _distinct_count_pure(series)


def _distinct_count_pure(series: pl.Series) -> int:
    return series.n_unique()


def histogram(values: Sequence[float], bins: int) -> list[tuple[float, int]]:
    """Equal-width histogram over ``[min, max]``.

    Returns ``[(left_edge, count), ...]`` with ``bins`` entries. The right edge is
    inclusive (the max lands in the last bin). All-equal input collapses to a
    single ``[(value, count)]`` bin. Empty input or ``bins < 1`` => ``[]``.

    Dispatches to the native kernel when gated (byte-identical to ``_histogram_pure``).
    """
    if native_enabled("histogram"):
        return _histogram_native(values, bins)
    return _histogram_pure(values, bins)


def _histogram_pure(values: Sequence[float], bins: int) -> list[tuple[float, int]]:
    vals = [float(v) for v in values if v is not None]
    if not vals or bins < 1:
        return []
    lo, hi = builtins.min(vals), builtins.max(vals)
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


def _histogram_native(values: Sequence[float], bins: int) -> list[tuple[float, int]]:
    import pyarrow as pa

    vals = [float(v) for v in values if v is not None]
    arr = pa.array(vals, type=pa.float64())
    # pyo3 returns a list of (float, int) tuples -- the same shape as the pure path.
    return native_module().histogram(arr, bins)


def quantile(values: Sequence[float], q: float) -> float:
    """Linear-interpolation quantile (numpy default). Empty input => 0.0.

    Dispatches to the native kernel when gated (byte-identical to ``_quantile_pure``).
    """
    if native_enabled("quantile"):
        return _quantile_native(values, q)
    return _quantile_pure(values, q)


def _quantile_pure(values: Sequence[float], q: float) -> float:
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


def _quantile_native(values: Sequence[float], q: float) -> float:
    import pyarrow as pa

    vals = [float(v) for v in values if v is not None]
    arr = pa.array(vals, type=pa.float64())
    return native_module().quantile(arr, q)


def mean(values: Sequence[float]) -> float:
    """Arithmetic mean. Empty input => 0.0.

    Dispatches to the native kernel when gated (byte-identical to ``_mean_pure``).
    """
    if native_enabled("mean"):
        return _mean_native(values)
    return _mean_pure(values)


def _mean_pure(values: Sequence[float]) -> float:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _mean_native(values: Sequence[float]) -> float:
    import pyarrow as pa

    vals = [float(v) for v in values if v is not None]
    arr = pa.array(vals, type=pa.float64())
    return native_module().mean(arr)


def min(values: Sequence[float]) -> float:
    """Minimum over finite values. Empty input => 0.0.

    Dispatches to the native kernel when gated (byte-identical to ``_min_pure``).
    """
    if native_enabled("min"):
        return _min_native(values)
    return _min_pure(values)


def _min_pure(values: Sequence[float]) -> float:
    vals = [float(v) for v in values if v is not None]
    return builtins.min(vals) if vals else 0.0


def _min_native(values: Sequence[float]) -> float:
    import pyarrow as pa

    vals = [float(v) for v in values if v is not None]
    arr = pa.array(vals, type=pa.float64())
    return native_module().min(arr)


def max(values: Sequence[float]) -> float:
    """Maximum over finite values. Empty input => 0.0.

    Dispatches to the native kernel when gated (byte-identical to ``_max_pure``).
    """
    if native_enabled("max"):
        return _max_native(values)
    return _max_pure(values)


def _max_pure(values: Sequence[float]) -> float:
    vals = [float(v) for v in values if v is not None]
    return builtins.max(vals) if vals else 0.0


def _max_native(values: Sequence[float]) -> float:
    import pyarrow as pa

    vals = [float(v) for v in values if v is not None]
    arr = pa.array(vals, type=pa.float64())
    return native_module().max(arr)


def cluster_size_histogram(sizes: Sequence[float]) -> list[int]:
    """Counts of cluster sizes == 1, == 2, == 3, and >= 4 (4 buckets).

    Dispatches to the native kernel when gated (byte-identical to the pure path).
    """
    if native_enabled("cluster_size_histogram"):
        import pyarrow as pa

        arr = pa.array([float(s) for s in sizes], type=pa.float64())
        return list(native_module().cluster_size_histogram(arr))
    return _cluster_size_histogram_pure(sizes)


def _cluster_size_histogram_pure(sizes: Sequence[float]) -> list[int]:
    return [
        sum(1 for s in sizes if s == 1),
        sum(1 for s in sizes if s == 2),
        sum(1 for s in sizes if s == 3),
        sum(1 for s in sizes if s >= 4),
    ]
