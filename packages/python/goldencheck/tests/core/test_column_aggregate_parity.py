"""Parity: the native `column_aggregate` fused kernel must produce the SAME
`(len, null_count, n_unique_nonnull, dtype)` tuple Polars would give via
`len(s)`, `s.null_count()`, `s.drop_nulls().n_unique()`, and
`goldencheck.core.frame._neutral_dtype(s.dtype)`.

Covers every neutral dtype category (str, int, uint, float, date, datetime,
bool, other-via-a-List) plus the float NaN / signed-zero uniqueness
subtleties documented in `goldencheck-core/src/aggregate.rs`: Polars
`n_unique()` treats all NaN payloads as one distinct value, and treats
`-0.0`/`0.0` as equal -- both verified empirically against Polars 1.40.1.

Skips cleanly when the native extension isn't built (pure-Python-only env)."""
from __future__ import annotations

import random
from datetime import date, datetime

import polars as pl
import pytest
from goldencheck.core._native_loader import native_available, native_module
from goldencheck.core.frame import _neutral_dtype

native_only = pytest.mark.skipif(
    not native_available(), reason="goldencheck native extension not built"
)


def _check(s: pl.Series) -> None:
    ln, nc, nu, dt = native_module().column_aggregate(s.to_arrow())
    assert ln == len(s), f"len mismatch for {s.dtype}"
    assert nc == s.null_count(), f"null_count mismatch for {s.dtype}"
    assert nu == s.drop_nulls().n_unique(), f"n_unique mismatch for {s.dtype}: {s.to_list()!r}"
    assert dt == _neutral_dtype(s.dtype), f"dtype mismatch: native={dt} expected={_neutral_dtype(s.dtype)}"


# ---------------------------------------------------------------------------
# One case per neutral dtype category, each with nulls + a repeat + a
# singleton so len/null_count/n_unique are all exercised together.
# ---------------------------------------------------------------------------
@native_only
def test_str_dtype() -> None:
    _check(pl.Series("s", ["a", "b", "a", None, "a"], dtype=pl.Utf8))


@native_only
def test_int_dtype() -> None:
    _check(pl.Series("i", [1, 2, 1, None, -3], dtype=pl.Int64))


@native_only
def test_uint_dtype() -> None:
    _check(pl.Series("u", [1, 2, 1, None, 3], dtype=pl.UInt32))


@native_only
def test_float_dtype() -> None:
    _check(pl.Series("f", [1.5, 2.5, 1.5, None, -3.5], dtype=pl.Float64))


@native_only
def test_date_dtype() -> None:
    _check(
        pl.Series(
            "d",
            [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 1), None],
            dtype=pl.Date,
        )
    )


@native_only
def test_datetime_dtype() -> None:
    _check(
        pl.Series(
            "dt",
            [datetime(2020, 1, 1, 1), datetime(2020, 1, 1, 2), datetime(2020, 1, 1, 1), None],
            dtype=pl.Datetime,
        )
    )


@native_only
def test_bool_dtype() -> None:
    _check(pl.Series("b", [True, False, True, None], dtype=pl.Boolean))


@native_only
def test_other_dtype_list() -> None:
    # A List column has no neutral-dtype str/int/... mapping -> "other". The
    # kernel must not panic on it; n_unique_nonnull is best-effort (0) so we
    # only assert len/null_count/dtype here (n_unique is documented not
    # meaningful for "other").
    s = pl.Series("l", [[1, 2], [3], None, [1, 2]], dtype=pl.List(pl.Int64))
    ln, nc, _nu, dt = native_module().column_aggregate(s.to_arrow())
    assert ln == len(s)
    assert nc == s.null_count()
    assert dt == "other" == _neutral_dtype(s.dtype)


# ---------------------------------------------------------------------------
# NaN / signed-zero float parity -- the empirically-verified Polars semantics
# the Rust kernel's canonicalisation matches (see aggregate.rs doc comment).
# ---------------------------------------------------------------------------
@native_only
def test_float_nan_collapses_to_one_distinct_value() -> None:
    s = pl.Series("f", [1.0, 2.0, float("nan"), float("nan")], dtype=pl.Float64)
    assert s.n_unique() == 3  # Polars: {1.0, 2.0, NaN}
    _check(s)


@native_only
def test_float_signed_zero_collapses_to_one_distinct_value() -> None:
    s = pl.Series("f", [0.0, -0.0], dtype=pl.Float64)
    assert s.n_unique() == 1  # Polars: 0.0 == -0.0
    _check(s)


@native_only
def test_float_mixed_nan_and_signed_zero() -> None:
    s = pl.Series(
        "f", [1.0, 2.0, float("nan"), float("nan"), 0.0, -0.0], dtype=pl.Float64
    )
    _check(s)


@native_only
def test_float32_nan_and_signed_zero() -> None:
    s = pl.Series("f", [1.0, float("nan"), float("nan"), 0.0, -0.0], dtype=pl.Float32)
    _check(s)


# ---------------------------------------------------------------------------
# Structural edge cases: all-null, single value, empty.
# ---------------------------------------------------------------------------
@native_only
def test_all_null() -> None:
    _check(pl.Series("i", [None, None, None], dtype=pl.Int64))


@native_only
def test_single_value() -> None:
    _check(pl.Series("s", ["only"], dtype=pl.Utf8))


@native_only
def test_empty() -> None:
    _check(pl.Series("i", [], dtype=pl.Int64))


# ---------------------------------------------------------------------------
# Random fuzz over each dtype category.
# ---------------------------------------------------------------------------
@native_only
@pytest.mark.parametrize("seed", range(10))
def test_random_int(seed: int) -> None:
    rng = random.Random(seed)
    n = rng.randint(0, 200)
    vals = [rng.choice([None, rng.randint(-50, 50)]) for _ in range(n)]
    _check(pl.Series("i", vals, dtype=pl.Int64))


@native_only
@pytest.mark.parametrize("seed", range(10))
def test_random_float(seed: int) -> None:
    rng = random.Random(seed)
    n = rng.randint(0, 200)
    pool = [None, 0.0, -0.0, float("nan")] + [rng.uniform(-50, 50) for _ in range(20)]
    vals = [rng.choice(pool) for _ in range(n)]
    _check(pl.Series("f", vals, dtype=pl.Float64))


@native_only
@pytest.mark.parametrize("seed", range(10))
def test_random_str(seed: int) -> None:
    rng = random.Random(seed)
    n = rng.randint(0, 200)
    pool = [None, "a", "b", "c", "aa", "bb", ""]
    vals = [rng.choice(pool) for _ in range(n)]
    _check(pl.Series("s", vals, dtype=pl.Utf8))
