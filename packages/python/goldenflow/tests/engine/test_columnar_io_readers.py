"""Polars-free Parquet + Excel readers: ``transform()`` reads them without Polars.

Parquet (pyarrow ``to_pydict``) and Excel (openpyxl) carry real dtypes, so both the
configured and zero-config paths run Polars-free on the typed dict — no string-inference
gap. Parquet is byte-identical to ``pl.read_parquet``; the Excel reader uses openpyxl
(lighter + more available than the ``fastexcel`` that ``pl.read_excel`` needs), so it's
validated by feeding the already-parity-tested transform path.
"""
from __future__ import annotations

import goldenflow
import polars as pl
import pytest
from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
from goldenflow.core._native_loader import native_module
from goldenflow.engine import columnar


def _mrows(m):
    return [
        (r.column, r.transform, r.affected_rows, tuple(r.sample_before or []),
         tuple(r.sample_after or [])) for r in m.records
    ]


def _native_ready() -> bool:
    nm = native_module()
    return nm is not None and columnar.native_columns_ready(nm)


FRAME = {"name": ["  John SMITH ", "jane", None], "city": ["  NYC ", "nyc", "LA"],
         "amount": [1, 2, 3], "price": [1.5, 2.5, None]}
CFG = GoldenFlowConfig(transforms=[
    TransformSpec(column="name", ops=["strip", "lowercase"]),
    TransformSpec(column="city", ops=["strip"]),
])


def test_read_parquet_columns_matches_polars(tmp_path) -> None:
    p = tmp_path / "t.parquet"
    pl.DataFrame(FRAME).write_parquet(p)
    got = columnar.read_parquet_columns(p)
    ref = pl.read_parquet(p)
    assert list(got.keys()) == ref.columns
    for c in ref.columns:
        assert got[c] == ref[c].to_list(), c


@pytest.mark.parametrize("config", [CFG, None])
def test_transform_parquet_equals_polars(tmp_path, config) -> None:
    if not _native_ready():
        pytest.skip("native core not built")
    p = tmp_path / "t.parquet"
    pl.DataFrame(FRAME).write_parquet(p)
    res = goldenflow.transform(str(p), config=config)
    ref = goldenflow.transform_df(pl.read_parquet(p), config=config)
    for c in ref.df.columns:
        assert res.columns[c] == ref.df[c].to_list(), c
    assert _mrows(res.manifest) == _mrows(ref.manifest)


def _write_xlsx(path, rows):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    wb.save(str(path))


def test_read_excel_columns(tmp_path) -> None:
    p = tmp_path / "t.xlsx"
    _write_xlsx(p, [["name", "city", "amount"], ["  John ", "NYC", 1], ["jane", "LA", 2], [None, "SF", 3]])
    got = columnar.read_excel_columns(p)
    assert got == {"name": ["  John ", "jane", None], "city": ["NYC", "LA", "SF"], "amount": [1, 2, 3]}


@pytest.mark.parametrize("config", [CFG, None])
def test_transform_excel_runs_polars_free_via_dict(tmp_path, config) -> None:
    if not _native_ready():
        pytest.skip("native core not built")
    p = tmp_path / "t.xlsx"
    _write_xlsx(p, [["name", "city"], ["  John SMITH ", "  NYC "], ["jane", "nyc"], [None, "LA"]])
    # transform(xlsx path) == transform(the read dict) — the transform path is
    # itself parity-tested; this pins the reader->transform wiring.
    from_path = goldenflow.transform(str(p), config=config)
    from_dict = goldenflow.transform(columnar.read_excel_columns(p), config=config)
    for c in from_dict.columns:
        assert from_path.columns[c] == from_dict.columns[c], c


def test_readers_are_polars_free(tmp_path) -> None:
    import subprocess
    import sys
    import textwrap

    if not _native_ready():
        pytest.skip("native core not built")
    p = tmp_path / "t.parquet"
    pl.DataFrame({"name": ["  Hi ", "JANE"], "k": [1, 2]}).write_parquet(p)
    out = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(
            f"""
            import sys, goldenflow
            from goldenflow.config.schema import GoldenFlowConfig, TransformSpec
            cfg = GoldenFlowConfig(transforms=[TransformSpec(column="name", ops=["strip", "lowercase"])])
            res = goldenflow.transform(r"{p}", config=cfg)
            assert res.columns["name"] == ["hi", "jane"], res.columns["name"]
            print("POLARS" if "polars" in sys.modules else "CLEAN")
            """
        )],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "CLEAN", out.stdout + out.stderr


def test_unsupported_suffix_declines(tmp_path) -> None:
    p = tmp_path / "t.json"
    p.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="csv/.parquet/.xlsx"):
        goldenflow.transform(str(p), config=CFG)
