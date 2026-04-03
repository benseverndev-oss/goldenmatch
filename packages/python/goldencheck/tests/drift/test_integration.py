"""Integration tests: scan_file() with baseline drift detection."""
from __future__ import annotations

import tempfile
from pathlib import Path

import polars as pl
import pytest

from goldencheck.baseline.models import BaselineProfile, PatternGrammar, StatProfile
from goldencheck.engine.scanner import scan_file
from goldencheck.models.finding import Severity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_csv(df: pl.DataFrame, path: Path) -> None:
    """Write a Polars DataFrame to a CSV file."""
    df.write_csv(path)


def _make_clean_df(n: int = 200) -> pl.DataFrame:
    """Return a well-behaved DataFrame to use as the baseline source."""
    return pl.DataFrame({
        "age": list(range(20, 20 + n)),
        "score": [float(i % 100) for i in range(n)],
        "code": [f"AB-{i:04d}" for i in range(n)],
    })


def _make_drifted_df(n: int = 200) -> pl.DataFrame:
    """Return a DataFrame that has drifted from the clean baseline."""
    # age and score are shifted massively; code has mixed patterns
    return pl.DataFrame({
        "age": [10_000 + i for i in range(n)],       # extreme out-of-bounds values
        "score": [float(i % 100) for i in range(n)],
        "code": [f"AB-{i:04d}" if i % 5 != 0 else f"XX{i:05d}" for i in range(n)],
    })


def _make_minimal_baseline(source: str = "test.csv") -> BaselineProfile:
    """Return a hand-crafted BaselineProfile with stat + pattern info."""
    sp = StatProfile(
        distribution="normal",
        params={"loc": 30.0, "scale": 10.0},
        entropy=5.0,
        bounds={"min": 20.0, "max": 219.0, "p01": 20.5, "p99": 218.5},
    )
    pg = PatternGrammar(pattern="[A-Z]{2}-[0-9]{4}", coverage=1.0)
    return BaselineProfile(
        source=source,
        rows=200,
        columns=["age", "score", "code"],
        stat_profiles={"age": sp},
        patterns={"code": pg},
    )


# ---------------------------------------------------------------------------
# Test 1: scan_file without baseline works unchanged
# ---------------------------------------------------------------------------


class TestScanFileWithoutBaseline:
    """scan_file() without a baseline should behave exactly as before."""

    def test_returns_two_tuple_by_default(self) -> None:
        fixture = Path("tests/fixtures/simple.csv")
        result = scan_file(fixture)
        assert isinstance(result, tuple)
        assert len(result) == 2
        findings, profile = result
        assert isinstance(findings, list)

    def test_returns_three_tuple_with_return_sample(self) -> None:
        fixture = Path("tests/fixtures/simple.csv")
        result = scan_file(fixture, return_sample=True)
        assert len(result) == 3
        findings, profile, sample = result
        assert isinstance(sample, pl.DataFrame)

    def test_no_drift_findings_without_baseline(self) -> None:
        fixture = Path("tests/fixtures/simple.csv")
        findings, _ = scan_file(fixture)
        drift_sources = [f for f in findings if f.source == "baseline_drift"]
        assert len(drift_sources) == 0


# ---------------------------------------------------------------------------
# Test 2: scan_file with baseline adds drift findings
# ---------------------------------------------------------------------------


class TestScanFileWithBaseline:
    """scan_file() with a BaselineProfile object should produce drift findings."""

    def test_baseline_adds_drift_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write drifted data to a CSV
            drifted_path = Path(tmpdir) / "data.csv"
            _write_csv(_make_drifted_df(), drifted_path)

            # Baseline trained on clean data
            baseline = _make_minimal_baseline(source="data.csv")

            findings, _ = scan_file(drifted_path, baseline=baseline)

            # At least some drift findings must be present
            drift_findings = [f for f in findings if f.source == "baseline_drift"]
            assert len(drift_findings) > 0, (
                "Expected drift findings when scanning drifted data against a baseline"
            )

    def test_drift_findings_have_correct_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            drifted_path = Path(tmpdir) / "data.csv"
            _write_csv(_make_drifted_df(), drifted_path)
            baseline = _make_minimal_baseline(source="data.csv")

            findings, _ = scan_file(drifted_path, baseline=baseline)

            drift_findings = [f for f in findings if f.source == "baseline_drift"]
            for f in drift_findings:
                assert f.source == "baseline_drift"
                assert f.severity in (Severity.ERROR, Severity.WARNING, Severity.INFO)

    def test_pattern_consistency_suppressed_for_baseline_covered_columns(self) -> None:
        """PatternConsistencyProfiler findings for baseline-covered columns are removed."""
        with tempfile.TemporaryDirectory() as tmpdir:
            drifted_path = Path(tmpdir) / "data.csv"
            _write_csv(_make_drifted_df(), drifted_path)
            # Baseline covers the 'code' column with a pattern
            baseline = _make_minimal_baseline(source="data.csv")
            assert "code" in baseline.patterns

            findings, _ = scan_file(drifted_path, baseline=baseline)

            pc_code_findings = [
                f for f in findings
                if f.check == "pattern_consistency" and f.column == "code"
            ]
            assert len(pc_code_findings) == 0, (
                "pattern_consistency findings for baseline-covered 'code' column should be suppressed"
            )

    def test_no_baseline_warning_when_source_matches(self, caplog: pytest.LogCaptureFixture) -> None:
        """No filename mismatch warning when baseline source matches scan file name."""
        import logging
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "data.csv"
            _write_csv(_make_clean_df(), path)
            baseline = _make_minimal_baseline(source=str(path))  # source_filename == "data.csv"

            with caplog.at_level(logging.WARNING, logger="goldencheck.engine.scanner"):
                scan_file(path, baseline=baseline)

            mismatch_warnings = [r for r in caplog.records if "doesn't match" in r.message]
            assert len(mismatch_warnings) == 0

    def test_baseline_source_mismatch_logs_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """A warning is logged when baseline source filename differs from scan path."""
        import logging
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "other_file.csv"
            _write_csv(_make_clean_df(), path)
            # Baseline has a different source filename
            baseline = _make_minimal_baseline(source="original_data.csv")

            with caplog.at_level(logging.WARNING, logger="goldencheck.engine.scanner"):
                scan_file(path, baseline=baseline)

            mismatch_warnings = [r for r in caplog.records if "doesn't match" in r.message]
            assert len(mismatch_warnings) >= 1


# ---------------------------------------------------------------------------
# Test 3: scan_file with baseline path (YAML)
# ---------------------------------------------------------------------------


class TestScanFileWithBaselinePath:
    """scan_file() should accept a Path to a YAML baseline file."""

    def test_accepts_baseline_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            drifted_path = tmpdir_path / "data.csv"
            baseline_path = tmpdir_path / "baseline.yml"

            _write_csv(_make_drifted_df(), drifted_path)

            # Save baseline to YAML
            baseline = _make_minimal_baseline(source="data.csv")
            baseline.save(str(baseline_path))
            assert baseline_path.exists()

            # Pass path — scanner should load it automatically
            findings, _ = scan_file(drifted_path, baseline=baseline_path)
            assert isinstance(findings, list)

    def test_baseline_path_produces_drift_findings(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            drifted_path = tmpdir_path / "data.csv"
            baseline_path = tmpdir_path / "baseline.yml"

            _write_csv(_make_drifted_df(), drifted_path)

            baseline = _make_minimal_baseline(source="data.csv")
            baseline.save(str(baseline_path))

            findings, _ = scan_file(drifted_path, baseline=baseline_path)

            drift_findings = [f for f in findings if f.source == "baseline_drift"]
            assert len(drift_findings) > 0, (
                "Expected drift findings when passing a YAML baseline path"
            )

    def test_baseline_str_path_also_works(self) -> None:
        """str path (not Path object) should be handled too."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir_path = Path(tmpdir)
            drifted_path = tmpdir_path / "data.csv"
            baseline_path = tmpdir_path / "baseline.yml"

            _write_csv(_make_drifted_df(), drifted_path)

            baseline = _make_minimal_baseline(source="data.csv")
            baseline.save(str(baseline_path))

            # Pass as str
            findings, _ = scan_file(drifted_path, baseline=str(baseline_path))
            drift_findings = [f for f in findings if f.source == "baseline_drift"]
            assert len(drift_findings) > 0
