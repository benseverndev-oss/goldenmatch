"""Wave 1 native-dispatch tests for the frame-stat kernels (box-safe pure path)."""

from __future__ import annotations

import polars as pl
from goldenanalysis.core import aggregate as agg


def test_distinct_count_matches_polars_n_unique():
    for data in ([1, 1, 2, None], [1.0, 1.0, 2.0], ["a", "a", "b"], []):
        s = pl.Series(data)
        assert agg.distinct_count(s) == s.n_unique()


def test_null_ratio_per_column_pure():
    df = pl.DataFrame({"a": [1, None, 3], "b": [None, None, None]})
    assert agg.null_ratio_per_column(df) == {"a": 1 / 3, "b": 1.0}


def test_duplicate_row_ratio_pure():
    df = pl.DataFrame({"a": [1, 1, 2], "b": ["x", "x", "y"]})  # rows 0,1 dup
    assert agg.duplicate_row_ratio(df) == 2 / 3


def test_empty_frame():
    df = pl.DataFrame({"a": []})
    assert agg.null_ratio_per_column(df) == {"a": 0.0}
    assert agg.duplicate_row_ratio(df) == 0.0
