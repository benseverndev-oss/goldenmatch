"""Native Arrow ``Column`` (Polars-eviction Phase 1b) — the pyarrow-free C-Data
substrate. Proves the native Column round-trips a Polars frame's Arrow buffers via
``__arrow_c_stream__`` (ingest + egress) and applies the owned fused chain
zero-copy, byte-identical to the Polars engine, WITHOUT importing pyarrow.
"""
from __future__ import annotations

import sys

import goldenflow  # noqa: F401
import polars as pl
import pytest
from goldenflow.core._native_loader import native_module

nm = native_module()
_HAS_COLUMN = nm is not None and hasattr(nm, "Column")
pytestmark = pytest.mark.skipif(
    not _HAS_COLUMN, reason="native Column (arrow C-data) not built (pre-Phase-1b wheel)"
)


def _col(values: list):
    return native_module().Column.from_arrow(pl.DataFrame({"c": values}))


def test_ingest_egress_roundtrip_is_identity() -> None:
    values = ["  A  ", "b", None, "café", "", "  X Y  "]
    col = _col(values)
    assert len(col) == len(values)
    assert col.to_pylist() == values
    # egress via __arrow_c_stream__ back into Polars -> identical
    back = pl.from_arrow(col)
    got = back.to_series(0) if isinstance(back, pl.DataFrame) else back
    assert got.to_list() == values


def test_apply_chain_matches_polars_engine() -> None:
    from goldenflow import transform_df
    from goldenflow.config.schema import GoldenFlowConfig, TransformSpec

    values = ["  <b>John</b>  SMITH  ", "o'BRIEN, JR.", None, "café  éé", "  a  b  "]
    ops = [("strip", []), ("lowercase", []), ("collapse_whitespace", []), ("remove_html_tags", [])]

    col = _col(values)
    out_col, changed = col.apply_chain(ops)
    native_vals = out_col.to_pylist()

    # reference: the Polars engine
    cfg = GoldenFlowConfig(transforms=[TransformSpec(column="c", ops=[n for n, _ in ops])])
    ref = transform_df(pl.DataFrame({"c": values}), config=cfg)
    assert native_vals == ref.df["c"].to_list()
    # affected counts match the manifest
    assert [int(x) for x in changed] == [r.affected_rows for r in ref.manifest.records]


def test_roundtrip_is_pyarrow_free() -> None:
    """The whole ingest -> chain -> egress path must not import pyarrow — that is
    the entire point (native ~5MB substrate, not native+pyarrow+polars ~80MB)."""
    # pyarrow may already be present from an unrelated earlier import in the run;
    # only assert the Column path itself doesn't newly require it.
    had_pyarrow = "pyarrow" in sys.modules
    col = _col(["  A  ", None, "b"])
    out, _ = col.apply_chain([("strip", []), ("uppercase", [])])
    _ = pl.from_arrow(out)
    if not had_pyarrow:
        assert "pyarrow" not in sys.modules, "Column C-data path pulled in pyarrow"


def test_chunked_input_concatenates() -> None:
    # a chunked Polars column (append creates chunks) still ingests as one array
    s = pl.Series("c", ["  a  ", None])
    s2 = pl.concat([s, pl.Series("c", ["B", "c "])], rechunk=False)
    col = native_module().Column.from_arrow(s2.to_frame())
    assert col.to_pylist() == ["  a  ", None, "B", "c "]
    out, _ = col.apply_chain([("strip", []), ("lowercase", [])])
    assert out.to_pylist() == ["a", None, "b", "c"]
