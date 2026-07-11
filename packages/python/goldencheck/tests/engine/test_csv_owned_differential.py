"""Differential snapshot: the OWNED (polars-free) CSV inference contract vs
`pl.read_csv`, run side by side on the same corpus. Polars must be present for
this test (it asserts against `pl.read_csv` directly), unlike
`test_read_columns_parquet_excel_are_polars_free` which blocks polars.

This test intentionally documents where the two contracts DIVERGE -- the owned
path is a different, simpler contract (see `engine/csv_infer.py`), not a
byte-identical stand-in for Polars' inference. Each divergence is asserted +
commented so a future change to either contract shows up here as a test
failure, not a silent behavior change.
"""

from __future__ import annotations

import polars as pl
from goldencheck.core._native_loader import native_available
from goldencheck.engine.reader import _read_csv_columns_owned


def _write(tmp_path, text: str):
    p = tmp_path / "corpus.csv"
    p.write_text(text, encoding="utf-8")
    return p


def _polars_read(p) -> dict:
    return pl.read_csv(p, infer_schema_length=10000).to_dict(as_series=False)


def test_leading_zero_diverges_owned_str_vs_polars_int(tmp_path):
    p = _write(tmp_path, "zip\n01234\n00099\n")
    owned = _read_csv_columns_owned(p)
    polars = _polars_read(p)
    # DELTA: leading-zero numeric strings. Owned keeps them as `str` (a zip code
    # shouldn't lose its leading zero); Polars infers `Int64` and drops it.
    assert owned == {"zip": ["01234", "00099"]}
    assert polars == {"zip": [1234, 99]}
    assert owned != polars


def test_inf_diverges_owned_str_vs_polars_float(tmp_path):
    p = _write(tmp_path, "v\n1.5\ninf\n")
    owned = _read_csv_columns_owned(p)
    polars = _polars_read(p)
    # DELTA: "inf" (case-insensitive). Owned's float check explicitly rejects
    # inf/nan/infinity tokens -> the whole column falls back to `str`. Polars
    # parses "inf" as the IEEE float `inf`.
    assert owned == {"v": ["1.5", "inf"]}
    assert polars == {"v": [1.5, float("inf")]}
    assert owned != polars


def test_nan_alone_matches_both_as_str(tmp_path):
    p = _write(tmp_path, "v\n1.5\nnan\n")
    owned = _read_csv_columns_owned(p)
    polars = _polars_read(p)
    # MATCH (not a delta): Polars itself declines to parse a bare "nan" token in
    # a mixed column as float (unlike "inf") -- both contracts land on `str`
    # here, so this is NOT one of the documented deltas.
    assert owned == polars == {"v": ["1.5", "nan"]}


def test_trailing_dot_diverges_owned_str_vs_polars_float(tmp_path):
    p = _write(tmp_path, "v\n5.\n1.0\n")
    owned = _read_csv_columns_owned(p)
    polars = _polars_read(p)
    # DELTA: "5." (trailing dot, no fractional digit). Owned's float regex
    # (`-?[0-9]*\.?[0-9]+`) requires at least one digit after an optional dot,
    # so "5." matches neither int nor float -> the whole column falls back to
    # `str`. Polars' (more permissive) numeric parser accepts "5." as `5.0`.
    assert owned == {"v": ["5.", "1.0"]}
    assert polars == {"v": [5.0, 1.0]}
    assert owned != polars


def test_unsigned_plus_prefix_matches_both_as_str(tmp_path):
    p = _write(tmp_path, "v\n+5\n1.0\n")
    owned = _read_csv_columns_owned(p)
    polars = _polars_read(p)
    # MATCH (not a delta): neither contract's numeric parser accepts a leading
    # `+` sign, so both fall back to `str` for this column.
    assert owned == polars == {"v": ["+5", "1.0"]}


def test_matching_columns_agree(tmp_path):
    p = _write(tmp_path, "id,name,flag\n1,alice,true\n2,bob,false\n")
    owned = _read_csv_columns_owned(p)
    polars = _polars_read(p)
    # MATCH: plain ints, plain strings, and lowercase true/false bools agree
    # between both contracts.
    assert owned == polars == {
        "id": [1, 2],
        "name": ["alice", "bob"],
        "flag": [True, False],
    }


def test_owned_python_reference_and_native_agree_on_corpus(tmp_path):
    """Sanity: whichever backend (native or Python reference) is live for
    `_read_csv_columns_owned`, it should match the Python reference directly --
    this doesn't re-prove parity (test_csv_infer_parity.py owns that), it just
    confirms the reader's dispatch didn't silently pick something else."""
    p = _write(tmp_path, "zip,val\n01234,1\n00099,inf\n")
    from goldencheck.engine.csv_infer import read_csv_owned

    via_reader = _read_csv_columns_owned(p)
    via_reference = read_csv_owned(p)
    assert via_reader == via_reference
    # Document whether native was actually exercised for this run (informational
    # only -- both backends are required to agree regardless).
    from goldencheck.core._native_loader import native_enabled

    print("native csv_infer active:", native_enabled("csv_infer"), native_available())


# ---------------------------------------------------------------------------
# "other"-dtype smoke + scan_columns integration (Step 5): exercise the OWNED
# path end to end through scan_columns, including the degenerate all-empty
# column case (owned's "zero non-empty values -> all-None str column" rule).
# ---------------------------------------------------------------------------


def test_all_empty_column_owned_read_scans_cleanly(tmp_path):
    from goldencheck.engine.scanner import scan_columns

    p = _write(tmp_path, "a,b\n1,\n2,\n3,\n")
    columns = _read_csv_columns_owned(p)
    # All-empty column -> str dtype, every value None (owned's documented rule).
    assert columns["b"] == [None, None, None]
    findings = scan_columns(columns)  # must not crash
    assert isinstance(findings, list)


def test_owned_read_scan_columns_produces_findings(tmp_path):
    from goldencheck.engine.scanner import scan_columns

    # 100 rows, id column fully non-null (0-nulls-required signal) plus a
    # mostly-null column (>80% null) -- both trip NullabilityProfiler
    # regardless of native regex availability.
    rows = ["id,mostly_null"]
    for i in range(100):
        val = "v" if i == 0 else ""
        rows.append(f"{i},{val}")
    p = _write(tmp_path, "\n".join(rows) + "\n")
    columns = _read_csv_columns_owned(p)
    findings = scan_columns(columns)
    assert len(findings) > 0
