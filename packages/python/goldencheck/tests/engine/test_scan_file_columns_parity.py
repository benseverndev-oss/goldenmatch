"""P4a byte-identity gate: scan_columns(read_columns(f)) == the covered profilers re-run
over PolarsFrame(read_file(f)), for Parquet + XLSX."""
from __future__ import annotations

import pyarrow as pa
import pyarrow.parquet as pq
from goldencheck import scan_file_columns
from goldencheck.core._native_loader import native_enabled
from goldencheck.core.frame import PolarsFrame
from goldencheck.engine.reader import read_file
from goldencheck.engine.scanner import _HARD_PROFILERS, _MECHANICAL_PROFILERS
from goldencheck.relations.temporal import TemporalOrderProfiler


def _write_parquet(tmp_path):
    tbl = pa.table({
        "id": list(range(1, 21)),
        "grade": ["A", "B", "C", "D"] * 5,
        "email": [f"u{i}@x.com" for i in range(18)] + ["bad", "worse"],
        "start_date": [f"2021-01-{(i % 27) + 1:02d}" for i in range(20)],
        "end_date": [f"2020-01-{(i % 27) + 1:02d}" for i in range(20)],
    })
    p = tmp_path / "f.parquet"; pq.write_table(tbl, p); return p


def _write_xlsx(tmp_path):
    from openpyxl import Workbook
    wb = Workbook(); ws = wb.active
    ws.append(["id", "grade", "email"])
    for i in range(20):
        ws.append([i + 1, ["A", "B", "C", "D"][i % 4], f"u{i}@x.com" if i < 18 else "bad"])
    p = tmp_path / "f.xlsx"; wb.save(p); return p


def _expected_covered(path):
    df = read_file(path)
    pol = PolarsFrame(df)
    expected = []
    for name in df.columns:
        for profiler in _MECHANICAL_PROFILERS:
            expected.extend(profiler.profile(pol, name))
        if native_enabled("regex"):
            for profiler in _HARD_PROFILERS:
                expected.extend(profiler.profile(pol, name))
    if native_enabled("str_to_date"):
        expected.extend(TemporalOrderProfiler().profile(pol))
    return expected


def test_scan_file_columns_parquet_parity(tmp_path):
    p = _write_parquet(tmp_path)
    assert scan_file_columns(p) == _expected_covered(p)


def test_scan_file_columns_xlsx_parity(tmp_path):
    p = _write_xlsx(tmp_path)
    assert scan_file_columns(p) == _expected_covered(p)
