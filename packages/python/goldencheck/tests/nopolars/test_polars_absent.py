"""GoldenCheck 3.0.0: the DEFAULT scan path runs with **polars genuinely uninstalled**.

This module imports polars NOWHERE. It is the living proof for the 3.0.0 Flip end state:
`scan_file`/`scan_dataframe` are Arrow-native (the fused Rust/Arrow seam), so the full
default scan -- not just the covered-columns subset -- succeeds without polars. polars is
only needed for the opt-in [baseline] stat/drift features and the scan_dataframe(pl.DataFrame)
convenience overload.

It is `skipif`'d OUT of the normal suite (where polars IS present), so it is inert there
and only executes in the dedicated `goldencheck_nopolars` CI lane (and any local run where
polars is absent).

CONTRACT FLIP (3.0.0): pre-3.0 this lane asserted the full scan DECLINED (raised the
`goldencheck[polars]` ImportError) polars-absent. It now asserts the full scan SUCCEEDS via
the owned Arrow contract, emitting the neutral dtype vocabulary. The numeric/sequence/date/
regex checks self-skip when the native kernel is also absent (same graceful-degradation
pattern as the hard/temporal checks).
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

_HAS_POLARS = importlib.util.find_spec("polars") is not None

pytestmark = pytest.mark.skipif(
    _HAS_POLARS,
    reason="polars-absent proof -- only runs where polars is NOT installed (the S2.0 lane)",
)


def test_import_goldencheck_without_polars() -> None:
    import goldencheck  # must not raise, must not import polars

    assert "polars" not in sys.modules
    # the public entry points survive a polars-absent import
    for name in (
        "scan_dataframe",
        "scan_file",
        "read_file",
        "functional_dependencies",
        "Finding",
        "Severity",
    ):
        assert hasattr(goldencheck, name), name


def test_uncovered_path_raises_clean_error_without_polars() -> None:
    # Touching the lazy proxy fires the deferred `import polars`, which is absent here.
    # The proxy catches the real ModuleNotFoundError and re-raises a plain ImportError
    # with the `goldencheck[polars]` install hint (see goldencheck/_polars_lazy.py), so
    # that's the type + message this asserts, not ModuleNotFoundError.
    from goldencheck._polars_lazy import pl

    with pytest.raises(ImportError, match=r"goldencheck\[polars\]"):
        _ = pl.DataFrame


def test_covered_scan_columns_without_polars() -> None:
    from goldencheck import scan_columns

    findings = scan_columns(
        {
            "pk": list(range(120)),
            "grade": ["A", "B", "C"] * 40,
            "note": [None] * 120,
        }
    )
    checks = sorted({f.check for f in findings})
    # covered structural checks fire; nothing polars-only ran
    assert "uniqueness" in checks  # pk is 100% unique
    assert "cardinality" in checks  # grade is low-cardinality
    assert "nullability" in checks  # note is entirely null
    assert "polars" not in sys.modules


def test_hard_checks_run_without_polars() -> None:
    import pytest
    from goldencheck.core._native_loader import native_enabled

    if not native_enabled("regex"):
        pytest.skip("nopolars lane without native regex kernel; hard checks skip by design")
    from goldencheck import scan_columns

    findings = scan_columns({"email": [f"u{i}@x.com" for i in range(18)] + ["bad", "worse"]})
    checks = {f.check for f in findings}
    assert "format_detection" in checks  # regex ran polars-free
    assert "polars" not in sys.modules


def test_temporal_check_runs_without_polars() -> None:
    import pytest
    from goldencheck.core._native_loader import native_enabled

    if not native_enabled("str_to_date"):
        pytest.skip("nopolars lane without native date kernel; temporal skips by design")
    from goldencheck import scan_columns

    findings = scan_columns(
        {
            "start_date": ["2021-05-01", "2021-01-01"],
            "end_date": ["2021-01-01", "2021-06-01"],
        }
    )
    checks = {f.check for f in findings}
    assert "temporal_order" in checks
    assert "polars" not in sys.modules


def test_read_columns_parquet_excel_polars_free(tmp_path) -> None:
    # P4b Task 3: Parquet (pyarrow) and Excel (openpyxl) read + scan polars-free.
    import pyarrow as pa
    import pyarrow.parquet as pq
    from goldencheck import read_columns, scan_file_columns
    from openpyxl import Workbook

    pqp = tmp_path / "f.parquet"
    pq.write_table(pa.table({"id": [1, 2, 3], "grade": ["A", "B", "A"]}), pqp)
    assert read_columns(pqp) == {"id": [1, 2, 3], "grade": ["A", "B", "A"]}
    assert isinstance(scan_file_columns(pqp), list)

    wb = Workbook()
    ws = wb.active
    ws.append(["a", "b"])
    ws.append([1, "x"])
    xp = tmp_path / "f.xlsx"
    wb.save(xp)
    assert read_columns(xp)
    assert isinstance(scan_file_columns(xp), list)
    assert "polars" not in sys.modules


def test_csv_full_scan_succeeds_without_polars(tmp_path) -> None:
    # 3.0.0 CONTRACT FLIP: scan_file() now runs the FULL default scan Arrow-native,
    # so a CSV full scan SUCCEEDS polars-free (was: raised goldencheck[polars]).
    from goldencheck import read_columns
    from goldencheck.engine.scanner import scan_file

    csv = tmp_path / "c.csv"
    csv.write_text("a,b\n1,x\n2,y\n3,x\n", encoding="utf-8")
    # CSV reads via the owned inference path (int + str columns), no Polars:
    assert read_columns(csv) == {"a": [1, 2, 3], "b": ["x", "y", "x"]}
    # Full scan succeeds and emits the neutral dtype vocabulary:
    findings, profile = scan_file(csv)
    assert isinstance(findings, list)
    types = {c.name: c.inferred_type for c in profile.columns}
    assert types["a"] == "int"  # neutral vocab, not raw "Int64"
    assert types["b"] == "str"
    assert "polars" not in sys.modules


def test_parquet_full_scan_succeeds_without_polars(tmp_path) -> None:
    # Parquet full scan is Arrow-native end to end -- succeeds polars-free.
    import pyarrow as pa
    import pyarrow.parquet as pq
    from goldencheck.engine.scanner import scan_file

    pqp = tmp_path / "f.parquet"
    pq.write_table(pa.table({"pk": list(range(50)), "grade": ["A", "B"] * 25}), pqp)
    findings, profile = scan_file(pqp)
    assert isinstance(findings, list)
    checks = {f.check for f in findings}
    assert "uniqueness" in checks  # pk unique
    assert {c.name: c.inferred_type for c in profile.columns}["pk"] == "int"
    assert "polars" not in sys.modules


def test_scan_dataframe_accepts_arrow_table_without_polars() -> None:
    # 3.0.0 scan_dataframe accepts a pyarrow.Table natively (no pl.DataFrame needed).
    import pyarrow as pa
    from goldencheck.engine.scanner import scan_dataframe

    tbl = pa.table({"id": list(range(30)), "val": [1.0, 2.0, 3.0] * 10})
    findings, _profile = scan_dataframe(tbl)
    assert isinstance(findings, list)
    assert "polars" not in sys.modules
