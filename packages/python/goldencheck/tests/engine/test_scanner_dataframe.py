"""Parity tests for scan_dataframe vs scan_file.

scan_dataframe is the in-memory entry point that lets callers skip a
CSV round-trip. At the 10M quality-invariant-scale bench
(goldenmatch.core.quality.run_quality_check), writing df to a temp CSV
just so scan_file could read it back was 121s of pipeline_prep_quality_scan
wall. This module locks the in-memory path to produce the same findings.
"""
from pathlib import Path

import polars as pl
from goldencheck.engine.scanner import scan_dataframe, scan_file

FIXTURES = Path(__file__).parent.parent / "fixtures"


def test_scan_dataframe_returns_findings_and_profile():
    df = pl.read_csv(FIXTURES / "simple.csv")
    findings, profile = scan_dataframe(df)
    assert isinstance(findings, list)
    assert profile.row_count == df.height
    assert profile.column_count == len(df.columns)


def test_scan_dataframe_matches_scan_file_findings():
    """Same input -> same finding set (by (column, check) key)."""
    csv_path = FIXTURES / "simple.csv"
    df = pl.read_csv(csv_path)

    file_findings, _ = scan_file(csv_path)
    df_findings, _ = scan_dataframe(df, file_path=csv_path)

    file_keys = sorted((f.column, f.check, int(f.severity)) for f in file_findings)
    df_keys = sorted((f.column, f.check, int(f.severity)) for f in df_findings)
    assert file_keys == df_keys, (
        "scan_dataframe must produce the same findings as scan_file on the same data; "
        f"file={file_keys} df={df_keys}"
    )


def test_scan_dataframe_default_file_path_is_sentinel():
    df = pl.read_csv(FIXTURES / "simple.csv")
    _, profile = scan_dataframe(df)
    assert profile.file_path == "<dataframe>"


def test_scan_dataframe_respects_file_path_arg():
    df = pl.read_csv(FIXTURES / "simple.csv")
    _, profile = scan_dataframe(df, file_path="my_label.csv")
    assert profile.file_path == "my_label.csv"


def test_scan_dataframe_return_sample():
    df = pl.read_csv(FIXTURES / "simple.csv")
    _, profile, sample = scan_dataframe(df, return_sample=True)
    assert isinstance(sample, pl.DataFrame)
    assert sample.height <= df.height
    assert profile.row_count == df.height
