# tests/test_frame_seam.py
"""W0 Frame/Column seam scaffold: delegation parity vs raw Polars + to_frame idempotency."""
from __future__ import annotations

import polars as pl
from goldenmatch.core.frame import Column, Frame, PolarsFrame, to_frame


def _df() -> pl.DataFrame:
    return pl.DataFrame({"name": ["ann", "bob", None, "ann"], "zip": [1, 2, 3, 1]})


def test_to_frame_wraps_polars_dataframe():
    frame = to_frame(_df())
    assert isinstance(frame, PolarsFrame)
    assert isinstance(frame, Frame)  # runtime_checkable Protocol


def test_to_frame_is_idempotent():
    frame = to_frame(_df())
    assert to_frame(frame) is frame


def test_frame_delegation_matches_raw_polars():
    df = _df()
    frame = to_frame(df)
    assert frame.columns == df.columns
    assert frame.height == df.height
    assert frame.native is df


def test_column_delegation_matches_raw_polars():
    df = _df()
    col = to_frame(df).column("name")
    assert isinstance(col, Column)
    assert len(col) == 4
    assert col.null_count() == df["name"].null_count()
    assert col.n_unique() == df["name"].n_unique()
    assert col.to_list() == df["name"].to_list()


def test_to_arrow_columns_matches_kernel_ffi_shape():
    """to_arrow_columns must produce exactly what the fused kernels consume today."""
    df = _df()
    arrow_cols = to_frame(df).to_arrow_columns(["name", "zip"])
    assert set(arrow_cols) == {"name", "zip"}
    for name in ("name", "zip"):
        assert arrow_cols[name].to_pylist() == df[name].to_arrow().to_pylist()
