# tests/test_frame_seam_arrow.py
"""W1 ArrowFrame/ArrowColumn seam: delegation parity vs raw Polars, cross-backend
parity, null-semantics parity, and to_frame idempotency for pa.Table."""
from __future__ import annotations

import polars as pl
import pyarrow as pa
from goldenmatch.core.frame import ArrowFrame, Column, Frame, to_frame


def _data() -> dict:
    return {"name": ["ann", "bob", None, "ann"], "zip": [1, 2, 3, 1]}


def _table() -> pa.Table:
    return pa.table(_data())


def _pl_df() -> pl.DataFrame:
    return pl.DataFrame(_data())


def test_to_frame_wraps_arrow_table():
    frame = to_frame(_table())
    assert isinstance(frame, ArrowFrame)
    assert isinstance(frame, Frame)  # runtime_checkable Protocol


def test_to_frame_accepts_arrow_table_is_idempotent():
    frame = to_frame(_table())
    assert to_frame(frame) is frame


def test_arrow_frame_delegation_matches_raw_table():
    tbl = _table()
    frame = to_frame(tbl)
    assert frame.columns == tbl.column_names
    assert frame.height == tbl.num_rows
    assert frame.native is tbl


def test_arrow_column_delegation_matches_raw_table():
    tbl = _table()
    col = to_frame(tbl).column("name")
    assert isinstance(col, Column)
    assert len(col) == 4
    assert col.null_count() == tbl.column("name").null_count
    assert col.to_list() == tbl.column("name").to_pylist()


def test_arrow_to_arrow_columns_matches_kernel_ffi_shape():
    tbl = _table()
    arrow_cols = to_frame(tbl).to_arrow_columns(["name", "zip"])
    assert set(arrow_cols) == {"name", "zip"}
    for name in ("name", "zip"):
        assert arrow_cols[name].to_pylist() == tbl.column(name).to_pylist()


def test_to_frame_rejects_other():
    import pytest

    with pytest.raises(TypeError):
        to_frame([1, 2, 3])


def test_n_unique_null_semantics():
    """Polars n_unique() counts null as a distinct value; ArrowColumn must match."""
    pl_frame = to_frame(_pl_df())
    arrow_frame = to_frame(_table())
    assert pl_frame.column("name").n_unique() == 3
    assert arrow_frame.column("name").n_unique() == 3
    assert pl_frame.column("name").n_unique() == arrow_frame.column("name").n_unique()


def test_n_unique_all_null_untyped_column():
    """pa.table type-infers all-null data as null(); polars n_unique is 1."""
    pl_col = to_frame(pl.DataFrame({"x": [None, None, None]})).column("x")
    arrow_col = to_frame(pa.table({"x": [None, None, None]})).column("x")
    assert pl_col.n_unique() == 1
    assert arrow_col.n_unique() == 1
    assert pl_col.n_unique() == arrow_col.n_unique()


def test_n_unique_empty_untyped_column():
    """Untyped empty columns are null()-typed in arrow; polars n_unique is 0."""
    pl_col = to_frame(pl.DataFrame({"x": []})).column("x")
    arrow_col = to_frame(pa.table({"x": pa.array([])})).column("x")
    assert pl_col.n_unique() == 0
    assert arrow_col.n_unique() == 0
    assert pl_col.n_unique() == arrow_col.n_unique()


def test_n_unique_empty_typed_column():
    """Explicitly-typed empty columns have a count_distinct kernel; both agree on 0."""
    pl_col = to_frame(pl.DataFrame({"x": pl.Series([], dtype=pl.String)})).column("x")
    arrow_col = to_frame(pa.table({"x": pa.array([], type=pa.string())})).column("x")
    assert pl_col.n_unique() == 0
    assert arrow_col.n_unique() == 0
    assert pl_col.n_unique() == arrow_col.n_unique()


def test_n_unique_two_null_column():
    """Multiple nulls collapse to ONE distinct value on both backends."""
    data = ["a", None, None, "b"]
    pl_col = to_frame(pl.DataFrame({"x": data})).column("x")
    arrow_col = to_frame(pa.table({"x": data})).column("x")
    assert pl_col.n_unique() == 3
    assert arrow_col.n_unique() == 3
    assert pl_col.n_unique() == arrow_col.n_unique()


def test_cross_backend_parity():
    """Same logical data via pl.DataFrame->to_frame and pa.table->to_frame agree."""
    pl_frame = to_frame(_pl_df())
    arrow_frame = to_frame(_table())

    assert pl_frame.columns == arrow_frame.columns
    assert pl_frame.height == arrow_frame.height

    for name in pl_frame.columns:
        pl_col = pl_frame.column(name)
        arrow_col = arrow_frame.column(name)
        assert len(pl_col) == len(arrow_col)
        assert pl_col.null_count() == arrow_col.null_count()
        assert pl_col.n_unique() == arrow_col.n_unique()
        assert pl_col.to_list() == arrow_col.to_list()

    pl_arrow_cols = pl_frame.to_arrow_columns(["name", "zip"])
    arrow_arrow_cols = arrow_frame.to_arrow_columns(["name", "zip"])
    for name in ("name", "zip"):
        assert pl_arrow_cols[name].to_pylist() == arrow_arrow_cols[name].to_pylist()
