from __future__ import annotations

from datetime import date

import polars as pl
import pyarrow.parquet as pq
import pytest
from goldencheck.engine.reader import _read_parquet_columns


def _write_parquet(tmp_path):
    import pyarrow as pa

    tbl = pa.table(
        {
            "i": [1, 2, None, 4],
            "f": [1.5, 2.5, 3.5, None],
            "s": ["a", "b", None, "d"],
            "b": [True, False, True, None],
            "d": [date(2021, 1, 5), date(2021, 2, 6), None, date(2021, 3, 7)],
        }
    )
    p = tmp_path / "f.parquet"
    pq.write_table(tbl, p)
    return p


def test_read_parquet_columns_matches_polars(tmp_path):
    p = _write_parquet(tmp_path)
    got = _read_parquet_columns(p)
    exp = pl.read_parquet(p).to_dict(as_series=False)
    assert got == exp


def _write_xlsx(tmp_path):
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["homog_int", "homog_str", "int_float_mix", "str_num_mix"])
    ws.append([1, "a", 1, "N/A"])
    ws.append([2, "b", 2.5, 1])
    ws.append([None, None, 3, 2])
    p = tmp_path / "f.xlsx"
    wb.save(p)
    return p


def test_read_excel_columns_matches_polars(tmp_path):
    p = _write_xlsx(tmp_path)
    from goldencheck.engine.reader import _read_excel_columns

    got = _read_excel_columns(p)
    exp = pl.read_excel(p, engine="openpyxl").to_dict(as_series=False)
    assert got == exp


def _write_xlsx_extra(tmp_path):
    """Extra columns covering bool/float/date coercion edge cases pinned empirically."""
    from datetime import date

    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(
        [
            "homog_float",
            "homog_date",
            "all_none",
            "str_bool_mix",
            "bool_int_mix",
            "bool_float_mix",
            "bool_only",
        ]
    )
    ws.append([1.5, date(2021, 1, 5), None, "N/A", True, True, True])
    ws.append([2.5, None, None, True, 1, 1.5, False])
    ws.append([None, None, None, 1, 0, False, None])
    p = tmp_path / "f_extra.xlsx"
    wb.save(p)
    return p


def test_read_excel_columns_matches_polars_edge_cases(tmp_path):
    p = _write_xlsx_extra(tmp_path)
    from goldencheck.engine.reader import _read_excel_columns

    got = _read_excel_columns(p)
    exp = pl.read_excel(p, engine="openpyxl").to_dict(as_series=False)
    assert got == exp


def test_read_csv_columns_matches_read_file(tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("i,s\n1,a\n2,b\n,c\n", encoding="utf-8")
    from goldencheck.engine.reader import _read_csv_columns, read_file

    got = _read_csv_columns(p)
    exp = read_file(p).to_dict(as_series=False)
    assert got == exp


def test_read_columns_dispatch_and_guards(tmp_path):
    from goldencheck.engine.reader import read_columns

    with pytest.raises(ValueError, match="Unsupported"):
        read_columns(tmp_path / "x.json")
    with pytest.raises(FileNotFoundError):
        read_columns(tmp_path / "missing.csv")
    empty = tmp_path / "e.csv"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="no data"):
        read_columns(empty)
