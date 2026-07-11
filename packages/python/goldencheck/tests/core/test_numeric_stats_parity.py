"""Parity: the native `column_numeric_stats` + `count_outside` kernels must
reproduce what the `range_distribution` profiler gets from Polars via
`col.min()/max()/mean()/std()` and `filter_outside(lower, upper)`.

`min`/`max`/`count_nonnull` are exact; `mean`/`std` are float reductions so they
are compared within a relative epsilon (this is the suite's first float-stat
kernel). `std` is the SAMPLE std (ddof=1), matching Polars' default. NaN is
canonicalised before comparison (`NaN != NaN` would false-positive), and Polars'
`None` for empty / all-NaN min-max and `n<2` std maps to the kernel's `NaN`
sentinel.

`count_outside` receives the POLARS-computed `lower`/`upper` (`mean-3*std` /
`mean+3*std`) so boundary values agree exactly with `filter_outside`; the sample
strings must byte-match `[str(v) for v in outliers.to_list()[:5]]` for the
column's native dtype (Int64 -> `"1"`, Float64 -> Python `str(float)`).

Skips cleanly when the native extension isn't built (pure-Python-only env)."""
from __future__ import annotations

import math
import random

import polars as pl
import pytest
from goldencheck.core._native_loader import native_available, native_module

native_only = pytest.mark.skipif(
    not native_available(), reason="goldencheck native extension not built"
)

_REL_EPS = 1e-9


def _float_eq(native: float, polars: object) -> bool:
    """Compare a native f64 against a Polars scalar (which may be ``None`` /
    NaN / inf), canonicalising NaN and Polars-``None`` to the kernel's NaN
    sentinel and using a relative epsilon for finite values."""
    if polars is None:
        return math.isnan(native)
    p = float(polars)
    if math.isnan(p):
        return math.isnan(native)
    if math.isinf(p):
        return math.isinf(native) and (native > 0) == (p > 0)
    return abs(native - p) <= _REL_EPS * (1.0 + abs(p))


def _check_stats(s: pl.Series) -> None:
    count, mn, mx, mean, std, _sum = native_module().column_numeric_stats(s.to_arrow())
    expected_count = s.len() - s.null_count()
    assert count == expected_count, f"count mismatch for {s.dtype}: {s.to_list()!r}"
    assert _float_eq(mn, s.min()), f"min mismatch for {s.dtype}: native={mn} polars={s.min()}"
    assert _float_eq(mx, s.max()), f"max mismatch for {s.dtype}: native={mx} polars={s.max()}"
    assert _float_eq(mean, s.mean()), f"mean mismatch for {s.dtype}: {native_module().column_numeric_stats(s.to_arrow())!r}"
    assert _float_eq(std, s.std()), f"std mismatch for {s.dtype}: native={std} polars={s.std()}"


def _check_outside(s: pl.Series) -> None:
    """Mirror the profiler's outlier branch: only when Polars std is a positive
    finite number are bounds defined; assert the kernel's (count, sample) equals
    the Polars `filter_outside` result under those exact bounds."""
    mean = s.mean()
    std = s.std()
    if mean is None or std is None or not math.isfinite(std) or std <= 0:
        return
    lower = mean - 3 * std
    upper = mean + 3 * std
    non_null = s.drop_nulls()
    outliers = non_null.filter((non_null < lower) | (non_null > upper))
    exp_count = len(outliers)
    exp_sample = [str(v) for v in outliers.to_list()[:5]]

    count, sample = native_module().count_outside(s.to_arrow(), lower, upper)
    assert count == exp_count, f"outlier count mismatch for {s.dtype}: {native_module().count_outside(s.to_arrow(), lower, upper)!r} vs {exp_count}"
    assert sample == exp_sample, f"outlier sample mismatch for {s.dtype}: native={sample!r} polars={exp_sample!r}"


def _check(s: pl.Series) -> None:
    _check_stats(s)
    _check_outside(s)


# ---------------------------------------------------------------------------
# Structural / adversarial edge cases.
# ---------------------------------------------------------------------------
@native_only
def test_empty() -> None:
    _check(pl.Series("i", [], dtype=pl.Int64))


@native_only
def test_single_value_std_none() -> None:
    s = pl.Series("f", [5.0], dtype=pl.Float64)
    assert s.std() is None  # Polars: n<2 -> None
    _check(s)


@native_only
def test_all_null() -> None:
    _check(pl.Series("i", [None, None, None], dtype=pl.Int64))


@native_only
def test_all_same() -> None:
    _check(pl.Series("f", [3.0, 3.0, 3.0], dtype=pl.Float64))


@native_only
def test_int64() -> None:
    _check(pl.Series("i", [1, 2, 3, 4, 100, -100, None], dtype=pl.Int64))


@native_only
def test_uint32() -> None:
    _check(pl.Series("u", [1, 2, 3, 4, 1000, None], dtype=pl.UInt32))


@native_only
def test_float64_with_nulls() -> None:
    _check(pl.Series("f", [1.5, 2.5, 1000.0, -1234.25, None, 0.5], dtype=pl.Float64))


@native_only
def test_negatives() -> None:
    _check(pl.Series("i", [-5, -3, -10, -1, -50], dtype=pl.Int64))


@native_only
def test_nan_ignored_for_minmax_propagates_to_mean_std() -> None:
    s = pl.Series("f", [1.0, 2.0, float("nan"), 3.0], dtype=pl.Float64)
    # Polars: min/max ignore NaN; mean/std propagate NaN.
    assert s.min() == 1.0 and s.max() == 3.0
    assert math.isnan(s.mean()) and math.isnan(s.std())
    _check_stats(s)


@native_only
def test_inf_participates_in_minmax() -> None:
    s = pl.Series("f", [1.0, 2.0, float("inf"), 3.0], dtype=pl.Float64)
    assert math.isinf(s.max()) and s.min() == 1.0
    _check_stats(s)


@native_only
def test_neg_inf_min() -> None:
    s = pl.Series("f", [1.0, 2.0, float("-inf"), 3.0], dtype=pl.Float64)
    assert math.isinf(s.min()) and s.min() < 0
    _check_stats(s)


@native_only
def test_all_nan() -> None:
    s = pl.Series("f", [float("nan"), float("nan")], dtype=pl.Float64)
    # Polars all-NaN min/max is NaN (only EMPTY is None); the kernel matches.
    assert math.isnan(s.min()) and math.isnan(s.max())
    _check_stats(s)


# ---------------------------------------------------------------------------
# Outlier-formatting: an int column and a float column each with clear outliers,
# to pin the dtype-specific `str(v)` form (Int64 -> "1000", Float64 -> "1000.0").
# ---------------------------------------------------------------------------
@native_only
def test_outlier_sample_int_dtype() -> None:
    vals = [0] * 50 + [10_000, -10_000, 12_345]
    _check(pl.Series("i", vals, dtype=pl.Int64))


@native_only
def test_outlier_sample_float_dtype() -> None:
    vals = [0.0] * 50 + [10_000.5, -9_999.25, 1234.75]
    _check(pl.Series("f", vals, dtype=pl.Float64))


# ---------------------------------------------------------------------------
# Random fuzz over int / uint / float columns (nulls, NaN, inf in the pool).
# ---------------------------------------------------------------------------
@native_only
@pytest.mark.parametrize("seed", range(12))
def test_random_int(seed: int) -> None:
    rng = random.Random(seed)
    n = rng.randint(0, 300)
    # Occasional extreme values so the +/-3 sigma outlier branch fires.
    pool = [None] + list(range(-40, 41)) + [5000, -5000]
    vals = [rng.choice(pool) for _ in range(n)]
    _check(pl.Series("i", vals, dtype=pl.Int64))


@native_only
@pytest.mark.parametrize("seed", range(12))
def test_random_float(seed: int) -> None:
    rng = random.Random(seed)
    n = rng.randint(0, 300)
    pool = [None, 0.0, -0.0] + [rng.uniform(-50, 50) for _ in range(20)] + [9999.5, -8888.25]
    vals = [rng.choice(pool) for _ in range(n)]
    _check(pl.Series("f", vals, dtype=pl.Float64))


@native_only
@pytest.mark.parametrize("seed", range(8))
def test_random_uint(seed: int) -> None:
    rng = random.Random(seed)
    n = rng.randint(0, 300)
    pool = [None] + list(range(0, 60)) + [9000]
    vals = [rng.choice(pool) for _ in range(n)]
    _check(pl.Series("u", vals, dtype=pl.UInt32))
