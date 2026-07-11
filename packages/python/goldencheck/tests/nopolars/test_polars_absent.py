"""GoldenCheck Stage-2 S2.0: goldencheck works with **polars genuinely uninstalled**.

This module imports polars NOWHERE. It is the living proof for the Polars-eviction end
state (P4, where `polars` moves to the `[polars]` extra). Every other polars-free test in
the suite still touches polars somewhere, so none of them can run in a polars-absent
interpreter; this one can.

It is `skipif`'d OUT of the normal suite (where polars IS present), so it is inert there
and only executes in the dedicated `goldencheck_nopolars` CI lane (and any local run where
polars is absent).

NOTE (S2.0): goldencheck has no non-Polars `Column`/`Frame` backend yet (that arrives with
S2.1), so this lane asserts import-survival + a clean decline on the uncovered tail ONLY --
NOT a covered scan. The covered-scan assertions land when S2.1 ships the backend.
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


def test_csv_reads_owned_and_full_scan_declines_without_polars(tmp_path) -> None:
    # CSV now reads Polars-free via goldencheck's OWN inference contract (the native
    # csv_infer kernel, or the Python reference fallback) -- so read_columns(csv) works
    # WITHOUT Polars (deliberately differs from pl.read_csv; see engine/csv_infer.py).
    # But scan_file()'s FULL scan (classification/sampling/denial) reads via
    # read_file() -> pl.read_csv(), which still needs Polars and must decline with the
    # helpful `goldencheck[polars]` ImportError rather than crash or silently degrade.
    import pytest
    from goldencheck import read_columns
    from goldencheck.engine.scanner import scan_file

    csv = tmp_path / "c.csv"
    csv.write_text("a\n1\n", encoding="utf-8")
    # CSV reads via the owned path (int column), no ImportError, no Polars:
    assert read_columns(csv) == {"a": [1]}
    # scan_file() -> read_file() -> pl.read_csv() on the lazy proxy -> helpful ImportError:
    with pytest.raises(ImportError, match=r"goldencheck\[polars\]"):
        scan_file(csv)
    assert "polars" not in sys.modules
