"""Native CSV I/O (Polars-eviction Phase 2) — the whole file->transform->file path
runs in one Rust call with NO ``pl.DataFrame``, no Polars, no pyarrow.

Parity contract (see docs/design/2026-07-07-polars-eviction-plan.md):
- **Manifest** — byte-identical to the Polars engine (same kernels/order, 3-row
  null-preserving samples, affected/total counts).
- **Output DATA** — cell-identical to the Polars engine reading the SAME file with
  type inference OFF (all-Utf8). CSV *serialization* is native's own (RFC4180);
  the comparison parses both outputs back and compares cells, not raw bytes.
"""
from __future__ import annotations

import sys

import goldenflow  # noqa: F401 -- import-time transform registration
import polars as pl
import pytest
from goldenflow import transform_df
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.core._native_loader import native_module
from goldenflow.engine import columnar

nm = native_module()
_HAS_CSV = nm is not None and hasattr(nm, "transform_csv")
pytestmark = pytest.mark.skipif(
    not _HAS_CSV, reason="native transform_csv not built (pre-Phase-2 wheel)"
)

# Messy rows: leading/trailing space, HTML, punctuation, unicode, empty (->null),
# an embedded comma (RFC4180 quoting), and a passthrough numeric column.
CSV_TEXT = (
    "name,email,keep\r\n"
    "  <b>John</b>  SMITH!  ,  JOHN@X.COM ,1\r\n"
    "\"o'BRIEN, jr.  123\",MARY@Y.com  ,2\r\n"
    ",,3\r\n"
    "café  éé  #7,b@z.io,4\r\n"
    "\"Smith, John\",q@a.com,5\r\n"
)


def _cfg(specs):
    return GoldenFlowConfig(transforms=[TransformSpec(column=c, ops=o) for c, o in specs])


def _manifest_rows(manifest):
    return [
        (
            r.column,
            r.transform,
            r.affected_rows,
            r.total_rows,
            tuple(r.sample_before or []),
            tuple(r.sample_after or []),
        )
        for r in manifest.records
    ]


@pytest.mark.parametrize(
    "specs",
    [
        [("name", ["strip", "lowercase"])],
        [("name", ["remove_html_tags", "strip", "collapse_whitespace", "remove_punctuation"])],
        [("name", ["strip", "title_case"]), ("email", ["strip", "lowercase"])],
        [("email", ["strip", "lowercase", "email_normalize"])],
        [("name", ["strip", "truncate:6"])],
    ],
)
def test_native_csv_equals_polars_engine(tmp_path, monkeypatch, specs) -> None:
    cfg = _cfg(specs)
    assert columnar.columnar_file_ready(cfg)

    inp = tmp_path / "in.csv"
    inp.write_bytes(CSV_TEXT.encode("utf-8"))

    # --- native whole-file path (Polars-free execution) ---
    out_native = tmp_path / "out_native.csv"
    manifest = columnar.transform_file(inp, out_native, cfg, source=str(inp))

    # --- reference: Polars engine reading the SAME file with inference OFF ---
    monkeypatch.delenv("GOLDENFLOW_ENGINE", raising=False)
    ref_df = pl.read_csv(inp, infer_schema_length=0)  # every column Utf8
    ref = transform_df(ref_df, config=cfg)

    # DATA parity: parse the native output back (all-Utf8) and compare cells.
    got_df = pl.read_csv(out_native, infer_schema_length=0)
    # the reference frame is also all-Utf8; align dtypes for a cell-wise compare
    assert got_df.columns == ref.df.columns
    for col in got_df.columns:
        assert got_df[col].to_list() == ref.df[col].cast(pl.Utf8).to_list(), (
            f"column {col!r} diverged"
        )

    # MANIFEST parity: same records (only transformed columns appear in both).
    assert _manifest_rows(manifest) == _manifest_rows(ref.manifest)


def test_native_csv_path_is_pyarrow_free(tmp_path) -> None:
    """The transform_csv call must not pull in pyarrow — the whole weight thesis."""
    inp = tmp_path / "in.csv"
    inp.write_bytes(CSV_TEXT.encode("utf-8"))
    out = tmp_path / "out.csv"
    had_pyarrow = "pyarrow" in sys.modules
    columnar.transform_file(inp, out, _cfg([("name", ["strip", "lowercase"])]))
    if not had_pyarrow:
        assert "pyarrow" not in sys.modules, "native CSV path pulled in pyarrow"


def test_parallel_matches_sequential_and_polars(tmp_path, monkeypatch) -> None:
    """The parallel reader/writer (forced via MIN_BYTES=0) must be byte-identical to
    the sequential path AND to the Polars engine — including a quoted field with
    embedded newlines that must NOT be split across chunks."""
    rows = []
    for i in range(400):
        if i == 200:
            rows.append(f'"multi\nline\nval",x{i}')  # embedded newlines in quotes
        else:
            rows.append(f"  Row{i}  ,x{i}")
    csv = ("name,other\n" + "\n".join(rows) + "\n").encode("utf-8")
    inp = tmp_path / "in.csv"
    inp.write_bytes(csv)
    cfg = _cfg([("name", ["strip", "lowercase"])])

    monkeypatch.setenv("GOLDENFLOW_NATIVE_CSV_PARALLEL_MIN_BYTES", "0")  # force parallel
    out_par = tmp_path / "par.csv"
    man_par = columnar.transform_file(inp, out_par, cfg)

    monkeypatch.setenv("GOLDENFLOW_NATIVE_CSV_PARALLEL_MIN_BYTES", "999999999")  # force seq
    out_seq = tmp_path / "seq.csv"
    man_seq = columnar.transform_file(inp, out_seq, cfg)

    par = pl.read_csv(out_par, infer_schema_length=0)
    seq = pl.read_csv(out_seq, infer_schema_length=0)
    assert par.equals(seq), "parallel output diverged from sequential"
    assert _manifest_rows(man_par) == _manifest_rows(man_seq)
    assert par.height == 400  # embedded-newline row not double-counted
    assert par["name"][200] == "multi\nline\nval"  # quoted newline preserved + stripped

    # and both equal the Polars engine reading inference-off
    monkeypatch.delenv("GOLDENFLOW_NATIVE_CSV_PARALLEL_MIN_BYTES", raising=False)
    monkeypatch.delenv("GOLDENFLOW_ENGINE", raising=False)
    ref = transform_df(pl.read_csv(inp, infer_schema_length=0), config=cfg)
    assert par["name"].to_list() == ref.df["name"].cast(pl.Utf8).to_list()


def test_passthrough_and_nulls_roundtrip(tmp_path) -> None:
    """Untransformed columns pass through unchanged; empty fields round-trip as
    empty (null) on both read and write."""
    inp = tmp_path / "in.csv"
    inp.write_text("a,b\n X ,\n,y\n", encoding="utf-8")
    out = tmp_path / "out.csv"
    columnar.transform_file(inp, out, _cfg([("a", ["strip"])]))
    back = pl.read_csv(out, infer_schema_length=0)
    assert back["a"].to_list() == ["X", None]  # stripped; empty->null
    assert back["b"].to_list() == [None, "y"]  # passthrough, null preserved
