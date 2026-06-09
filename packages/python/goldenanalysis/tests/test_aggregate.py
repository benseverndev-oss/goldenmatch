"""Exact-value tests for the pure-Python/Polars aggregation primitives.

These are the byte-identical reference the future Rust kernel (Phase 4) must match,
so the asserted values are the contract.
"""

from __future__ import annotations

import polars as pl
from goldenanalysis.core import aggregate as agg


def test_null_ratio_per_column() -> None:
    df = pl.DataFrame({"a": [1, 2, None, None, 5], "b": [1, 2, 3, 4, 5]})
    ratios = agg.null_ratio_per_column(df)
    assert ratios == {"a": 0.4, "b": 0.0}


def test_duplicate_row_ratio() -> None:
    # 5 rows, rows 0 and 1 identical => both count as duplicate rows => 2/5.
    df = pl.DataFrame(
        {
            "a": [1, 1, 2, 3, 4],
            "b": ["x", "x", "y", "z", "w"],
        }
    )
    assert agg.duplicate_row_ratio(df) == 0.4


def test_duplicate_row_ratio_no_dupes() -> None:
    df = pl.DataFrame({"a": [1, 2, 3]})
    assert agg.duplicate_row_ratio(df) == 0.0


def test_histogram_equal_width() -> None:
    assert agg.histogram([1, 2, 3, 4], bins=2) == [(1.0, 2), (2.5, 2)]


def test_histogram_single_value() -> None:
    assert agg.histogram([7, 7, 7], bins=4) == [(7.0, 3)]


def test_quantile_linear() -> None:
    vals = [1, 2, 3, 4]
    assert agg.quantile(vals, 0.5) == 2.5
    assert agg.quantile(vals, 0.0) == 1.0
    assert agg.quantile(vals, 1.0) == 4.0


def test_empty_frame() -> None:
    df = pl.DataFrame({"a": []})
    assert agg.null_ratio_per_column(df) == {"a": 0.0}
    assert agg.duplicate_row_ratio(df) == 0.0
