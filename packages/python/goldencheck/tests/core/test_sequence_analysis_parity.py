"""Parity: the native `sequence_analysis` kernel must reproduce what the
`sequence_detection` profiler gets from Polars via `col.diff().drop_nulls()` +
`count_eq(1)` + `count_gt(0)` + `is_sorted()` + `min()/max()` +
`set(unique().to_list())` and the `range(min, max+1) not in present` gap scan.

Every field is integer/bool exact (int/uint only, so NaN-free) -- the harness
registers `sequence_analysis` with an EMPTY divergence class. Key adversarial
case: `[i64::MIN, i64::MAX]` -- Polars `diff()` keeps Int64 and WRAPS
(`MAX - MIN` -> -1), and the kernel matches with `wrapping_sub` (plain `-` would
panic). The gap span there is 2^64, so the reference computes `gap_count`
arithmetically (`expected - present_size`) and the sample lazily (islice) --
identical to the profiler's `range(...)` list result without materialising it.

Skips cleanly when the native extension isn't built (pure-Python-only env)."""
from __future__ import annotations

import itertools
import random

import polars as pl
import pytest
from goldencheck.core._native_loader import native_available, native_module

native_only = pytest.mark.skipif(
    not native_available(), reason="goldencheck native extension not built"
)


def _polars_reference(s: pl.Series) -> tuple:
    """Reproduce the profiler's per-field Polars computation as the oracle,
    normalised to the kernel's return tuple shape."""
    non_null = s.drop_nulls()
    total = len(non_null)
    diffs = non_null.diff().drop_nulls()
    n_diffs = len(diffs)
    unit_diff_count = int((diffs == 1).sum())
    positive_diff_count = int((diffs > 0).sum())
    is_sorted = bool(non_null.is_sorted())
    col_min = int(non_null.min())
    col_max = int(non_null.max())
    present = set(non_null.unique().to_list())
    present_size = len(present)

    expected = col_max - col_min + 1
    if expected <= total:
        gap_count = 0
        gap_sample: list[int] = []
    else:
        # All distinct values lie in [min, max], so the number of missing ints
        # is exactly expected - present_size (identical to the profiler's
        # len([v for v in range(min, max+1) if v not in present])). The first-10
        # sample uses a lazy generator so a 2^64-wide span doesn't hang.
        gap_count = expected - present_size
        gap_sample = list(
            itertools.islice(
                (v for v in range(col_min, col_max + 1) if v not in present), 10
            )
        )
    return (
        n_diffs,
        unit_diff_count,
        positive_diff_count,
        is_sorted,
        col_min,
        col_max,
        present_size,
        gap_count,
        gap_sample,
    )


def _check(s: pl.Series) -> None:
    got = native_module().sequence_analysis(s.to_arrow())
    assert got is not None, f"kernel declined an int column: {s.to_list()!r}"
    n_diffs, unit, pos, is_sorted, mn, mx, present_size, gap_count, gap_sample = got
    exp = _polars_reference(s)
    actual = (
        n_diffs,
        unit,
        pos,
        is_sorted,
        mn,
        mx,
        present_size,
        gap_count,
        list(gap_sample),
    )
    assert actual == exp, f"sequence_analysis mismatch for {s.dtype}: native={actual!r} polars={exp!r}"


# ---------------------------------------------------------------------------
# Structural / adversarial edge cases.
# ---------------------------------------------------------------------------
@native_only
def test_tight_sequential_no_gap() -> None:
    _check(pl.Series("i", [1, 2, 3, 4, 5, 6, 7], dtype=pl.Int64))


@native_only
def test_gapped_ascending() -> None:
    _check(pl.Series("i", [1, 2, 4, 7, 8, 12, 20], dtype=pl.Int64))


@native_only
def test_unsorted() -> None:
    _check(pl.Series("i", [3, 1, 2, 9, 5, 4], dtype=pl.Int64))


@native_only
def test_duplicates_suppress_gap_guard() -> None:
    # [1,1,3]: total=3, present={1,3}, expected=3 -> 3<=3, no gaps flagged even
    # though 2 is missing (the profiler's expected_count<=total quirk).
    _check(pl.Series("i", [1, 1, 3], dtype=pl.Int64))


@native_only
def test_gapped_with_dups() -> None:
    _check(pl.Series("i", [10, 10, 12, 15, 15, 20], dtype=pl.Int64))


@native_only
def test_nulls_dropped() -> None:
    _check(pl.Series("i", [1, None, 2, None, 5, 6], dtype=pl.Int64))


@native_only
def test_uint32() -> None:
    _check(pl.Series("u", [100, 101, 103, 106, 107], dtype=pl.UInt32))


@native_only
def test_int64_min_max_wrapping_diff() -> None:
    s = pl.Series("i", [-(2**63), 2**63 - 1], dtype=pl.Int64)
    # Polars diff wraps Int64: MAX - MIN == -1, so no positive/unit diff.
    assert s.diff().drop_nulls().to_list() == [-1]
    _check(s)


@native_only
def test_descending() -> None:
    _check(pl.Series("i", [9, 7, 5, 3, 1], dtype=pl.Int64))


@native_only
def test_flat_all_same() -> None:
    # present_size=1, expected=1<=total, no gaps.
    _check(pl.Series("i", [5, 5, 5, 5], dtype=pl.Int64))


@native_only
def test_negative_range() -> None:
    _check(pl.Series("i", [-10, -8, -7, -3, -1, 0, 2], dtype=pl.Int64))


# ---------------------------------------------------------------------------
# Non-int columns / <2 values -> the kernel declines (None).
# ---------------------------------------------------------------------------
@native_only
def test_float_declined() -> None:
    assert native_module().sequence_analysis(pl.Series("f", [1.0, 2.0, 3.0]).to_arrow()) is None


@native_only
def test_single_value_declined() -> None:
    assert native_module().sequence_analysis(pl.Series("i", [42], dtype=pl.Int64).to_arrow()) is None


@native_only
def test_all_null_declined() -> None:
    s = pl.Series("i", [None, None], dtype=pl.Int64)
    assert native_module().sequence_analysis(s.to_arrow()) is None


# ---------------------------------------------------------------------------
# Random fuzz over int / uint columns (nulls, gaps, dups, reordering).
# ---------------------------------------------------------------------------
@native_only
@pytest.mark.parametrize("seed", range(16))
def test_random_int(seed: int) -> None:
    rng = random.Random(seed)
    n = rng.randint(2, 300)
    pool = [None] + list(range(0, 120))
    vals = [rng.choice(pool) for _ in range(n)]
    s = pl.Series("i", vals, dtype=pl.Int64)
    if s.drop_nulls().len() < 2:
        pytest.skip("fewer than 2 non-null values")
    _check(s)


@native_only
@pytest.mark.parametrize("seed", range(8))
def test_random_uint(seed: int) -> None:
    rng = random.Random(seed)
    n = rng.randint(2, 300)
    pool = [None] + list(range(0, 200))
    vals = [rng.choice(pool) for _ in range(n)]
    s = pl.Series("u", vals, dtype=pl.UInt32)
    if s.drop_nulls().len() < 2:
        pytest.skip("fewer than 2 non-null values")
    _check(s)
