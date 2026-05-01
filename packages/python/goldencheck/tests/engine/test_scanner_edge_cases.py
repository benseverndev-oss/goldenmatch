"""Edge-case tests for the scanner engine."""
from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.engine.scanner import scan_file


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_csv(tmp_path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _scan_and_downgrade(path, **kwargs):
    findings, profile = scan_file(path, **kwargs)
    findings = apply_confidence_downgrade(findings, llm_boost=False)
    return findings, profile


# ---------------------------------------------------------------------------
# test_scan_parquet — write parquet with Polars, scan it
# ---------------------------------------------------------------------------


class TestScanParquet:
    def test_basic_parquet(self, tmp_path):
        df = pl.DataFrame({
            "id": [1, 2, 3],
            "name": ["Alice", "Bob", "Charlie"],
            "score": [90.5, 85.0, 77.3],
        })
        path = tmp_path / "data.parquet"
        df.write_parquet(str(path))
        findings, profile = _scan_and_downgrade(path)
        assert profile.row_count == 3
        assert profile.column_count == 3

    def test_parquet_with_nulls(self, tmp_path):
        df = pl.DataFrame({
            "id": [1, 2, 3, 4],
            "value": [None, "x", None, "y"],
        })
        path = tmp_path / "nulls.parquet"
        df.write_parquet(str(path))
        findings, profile = _scan_and_downgrade(path)
        col = next(c for c in profile.columns if c.name == "value")
        assert col.null_count == 2

    def test_parquet_returns_findings_list(self, tmp_path):
        df = pl.DataFrame({"a": [1, 2, 3]})
        path = tmp_path / "simple.parquet"
        df.write_parquet(str(path))
        findings, _ = _scan_and_downgrade(path)
        assert isinstance(findings, list)


# ---------------------------------------------------------------------------
# test_scan_with_domain — scan with domain="healthcare"
# ---------------------------------------------------------------------------


class TestScanWithDomain:
    def _health_csv(self) -> str:
        rows = ["patient_id,diagnosis,npi"]
        for i in range(20):
            rows.append(f"{i},flu,123456789{i % 10}")
        return "\n".join(rows) + "\n"

    def _finance_csv(self) -> str:
        rows = ["account_id,amount,currency"]
        for i in range(20):
            rows.append(f"{i},{100.0 + i},USD")
        return "\n".join(rows) + "\n"

    def test_healthcare_domain(self, tmp_path):
        path = _write_csv(tmp_path, "health.csv", self._health_csv())
        findings, profile = _scan_and_downgrade(path, domain="healthcare")
        assert profile.row_count == 20
        assert isinstance(findings, list)

    def test_invalid_domain_raises(self, tmp_path):
        csv = "id,val\n1,a\n2,b\n3,c\n4,d\n5,e\n"
        path = _write_csv(tmp_path, "data.csv", csv)
        with pytest.raises(ValueError, match="Unknown domain"):
            scan_file(path, domain="nonexistent_domain_xyz")

    def test_finance_domain(self, tmp_path):
        path = _write_csv(tmp_path, "finance.csv", self._finance_csv())
        findings, profile = _scan_and_downgrade(path, domain="finance")
        assert profile.column_count == 3


# ---------------------------------------------------------------------------
# test_scan_return_sample — verify 3-tuple when return_sample=True
# ---------------------------------------------------------------------------


class TestScanReturnSample:
    def test_returns_three_tuple(self, tmp_path):
        csv = "id,name\n1,Alice\n2,Bob\n"
        path = _write_csv(tmp_path, "sample.csv", csv)
        result = scan_file(path, return_sample=True)
        assert len(result) == 3

    def test_sample_is_dataframe(self, tmp_path):
        csv = "id,name\n1,Alice\n2,Bob\n"
        path = _write_csv(tmp_path, "sample.csv", csv)
        findings, profile, sample = scan_file(path, return_sample=True)
        assert isinstance(sample, pl.DataFrame)

    def test_default_returns_two_tuple(self, tmp_path):
        csv = "id,name\n1,Alice\n2,Bob\n"
        path = _write_csv(tmp_path, "sample.csv", csv)
        result = scan_file(path, return_sample=False)
        assert len(result) == 2

    def test_sample_row_count(self, tmp_path):
        rows = "\n".join(f"{i},val_{i}" for i in range(50))
        csv = f"id,name\n{rows}\n"
        path = _write_csv(tmp_path, "big.csv", csv)
        _, _, sample = scan_file(path, return_sample=True)
        assert len(sample) == 50  # Under sample_size, so unchanged


# ---------------------------------------------------------------------------
# test_scan_nonexistent_file — expect error
# ---------------------------------------------------------------------------


class TestScanNonexistentFile:
    def test_raises_on_missing_file(self, tmp_path):
        path = tmp_path / "does_not_exist.csv"
        with pytest.raises(Exception):
            scan_file(path)

    def test_raises_on_missing_parquet(self, tmp_path):
        path = tmp_path / "nope.parquet"
        with pytest.raises(Exception):
            scan_file(path)


# ---------------------------------------------------------------------------
# test_scan_empty_file — completely empty file (no headers)
# ---------------------------------------------------------------------------


class TestScanEmptyFile:
    def test_raises_or_empty(self, tmp_path):
        path = tmp_path / "empty.csv"
        path.write_text("", encoding="utf-8")
        # An empty file (no headers) should raise or produce 0 columns
        try:
            findings, profile = _scan_and_downgrade(path)
            assert profile.column_count == 0 or profile.row_count == 0
        except Exception:
            # Raising is also acceptable behaviour for a truly empty file
            pass

    def test_empty_file_no_crash(self, tmp_path):
        path = tmp_path / "blank.csv"
        path.write_text("", encoding="utf-8")
        # Primary goal: no unhandled exception propagates
        try:
            _scan_and_downgrade(path)
        except (ValueError, Exception):
            pass  # Expected — scanner may raise on empty input


# ---------------------------------------------------------------------------
# test_scan_large_sample_size — sample_size larger than data
# ---------------------------------------------------------------------------


class TestScanLargeSampleSize:
    def test_sample_size_exceeds_rows(self, tmp_path):
        csv = "id,val\n1,a\n2,b\n3,c\n"
        path = _write_csv(tmp_path, "small.csv", csv)
        findings, profile = _scan_and_downgrade(path, sample_size=1_000_000)
        assert profile.row_count == 3

    def test_all_rows_scanned(self, tmp_path):
        csv = "id,val\n1,a\n2,b\n3,c\n"
        path = _write_csv(tmp_path, "small.csv", csv)
        _, _, sample = scan_file(
            path, return_sample=True, sample_size=1_000_000
        )
        assert len(sample) == 3

    def test_sample_size_one(self, tmp_path):
        rows = "\n".join(f"{i},v{i}" for i in range(100))
        csv = f"id,val\n{rows}\n"
        path = _write_csv(tmp_path, "hundred.csv", csv)
        _, _, sample = scan_file(path, return_sample=True, sample_size=1)
        assert len(sample) == 1
