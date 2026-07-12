"""Shadow-compute proof: the native `column_aggregate` fused kernel produces
the SAME `(len, null_count, n_unique_nonnull, dtype)` values as the Polars
computation the full-scan column loop (`_scan_dataframe_impl` in
`engine/scanner.py`) actually uses to build the authoritative `ColumnProfile`.

This does NOT assert anything about the scan's output -- that's covered by
the existing `test_scanner.py` / `test_scan_dataframe` suites, which stay
green unedited. This test proves the fused values are ready to become
authoritative at a future Flip, by checking them against a DataFrame with
the same variety of column shapes the full scan sees in production."""
from __future__ import annotations

from datetime import date

import polars as pl
import pytest
from goldencheck.core._native_loader import native_enabled, native_module
from goldencheck.core.frame import _neutral_dtype

native_only = pytest.mark.skipif(
    not native_enabled("column_aggregate"),
    reason="goldencheck native column_aggregate kernel not built/enabled",
)


@pytest.fixture
def shadow_df() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "int_col": pl.Series([1, 2, 1, None, -3, 2], dtype=pl.Int64),
            "float_col": pl.Series([1.5, float("nan"), 1.5, None, -3.5, 0.0], dtype=pl.Float64),
            "str_col": pl.Series(["a", "b", "a", None, "c", "b"], dtype=pl.Utf8),
            "bool_col": pl.Series([True, False, True, None, False, True], dtype=pl.Boolean),
            "date_col": pl.Series(
                [date(2020, 1, 1), date(2020, 1, 2), date(2020, 1, 1), None,
                 date(2020, 1, 3), date(2020, 1, 2)],
                dtype=pl.Date,
            ),
            "all_null_col": pl.Series([None, None, None, None, None, None], dtype=pl.Int64),
            "with_nulls_col": pl.Series(["x", None, "x", None, "y", None], dtype=pl.Utf8),
        }
    )


@native_only
def test_shadow_matches_polars_for_every_column(shadow_df: pl.DataFrame) -> None:
    for col_name in shadow_df.columns:
        col = shadow_df[col_name]
        expected = (
            len(col),
            col.null_count(),
            col.drop_nulls().n_unique(),
            _neutral_dtype(col.dtype),
        )
        actual = native_module().column_aggregate(col.to_arrow())
        assert actual == expected, f"mismatch for column {col_name!r}: {actual} != {expected}"


@native_only
def test_shadow_runs_on_real_scan_path(shadow_df: pl.DataFrame) -> None:
    """The scanner's full-scan column loop shadow-computes column_aggregate on
    every column without raising and without altering the authoritative
    ColumnProfile output -- exercised end-to-end via scan_dataframe."""
    from goldencheck.engine.scanner import scan_dataframe

    findings, profile = scan_dataframe(shadow_df, file_path="<shadow>")
    assert {cp.name for cp in profile.columns} == set(shadow_df.columns)
    for cp in profile.columns:
        col = shadow_df[cp.name]
        non_null = col.drop_nulls()
        assert cp.null_count == col.null_count()
        assert cp.unique_count == (non_null.n_unique() if len(non_null) > 0 else 0)
        # Flip owned dtype contract: inferred_type is the NEUTRAL vocabulary
        # (str/int/uint/float/date/datetime/bool/other), not raw ``str(pl.dtype)``.
        assert cp.inferred_type == _neutral_dtype(col.dtype)
