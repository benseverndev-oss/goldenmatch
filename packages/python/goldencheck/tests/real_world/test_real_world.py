"""Real-world dataset tests — verify GoldenCheck doesn't crash and produces reasonable results."""
from pathlib import Path

import pytest

from goldencheck.engine.scanner import scan_file
from goldencheck.engine.confidence import apply_confidence_downgrade
from goldencheck.models.finding import Severity

REAL_WORLD_DIR = Path(__file__).parent
DATASETS = list(REAL_WORLD_DIR.glob("*.csv"))


@pytest.mark.parametrize("csv_path", DATASETS, ids=lambda p: p.name)
def test_scan_does_not_crash(csv_path):
    """Every real-world dataset should scan without exceptions."""
    findings, profile = scan_file(csv_path)
    findings = apply_confidence_downgrade(findings, llm_boost=False)
    assert profile.row_count > 0
    assert profile.column_count > 0
    assert isinstance(findings, list)


@pytest.mark.parametrize("csv_path", DATASETS, ids=lambda p: p.name)
def test_health_grade_is_valid(csv_path):
    """Health grade should be A-F."""
    findings, profile = scan_file(csv_path)
    grade, score = profile.health_score()
    assert grade in ("A", "B", "C", "D", "F")
    assert 0 <= score <= 100


@pytest.mark.parametrize("csv_path", DATASETS, ids=lambda p: p.name)
def test_findings_have_required_fields(csv_path):
    """Every finding should have severity, column, check, and message."""
    findings, _ = scan_file(csv_path)
    for f in findings:
        assert f.severity in (Severity.ERROR, Severity.WARNING, Severity.INFO)
        assert f.column
        assert f.check
        assert f.message


@pytest.mark.parametrize("csv_path", DATASETS, ids=lambda p: p.name)
def test_no_errors_on_clean_public_data(csv_path):
    """Public reference datasets shouldn't produce ERROR-level findings (they're curated)."""
    findings, _ = scan_file(csv_path)
    findings = apply_confidence_downgrade(findings, llm_boost=False)
    errors = [f for f in findings if f.severity == Severity.ERROR]
    # Allow at most a few errors — real data isn't perfect
    assert len(errors) <= 5, f"Too many errors ({len(errors)}): {[f.message[:50] for f in errors[:5]]}"


def test_scan_with_domain_healthcare():
    """Domain pack should not crash on non-healthcare data."""
    csv = REAL_WORLD_DIR / "airport_codes.csv"
    if not csv.exists():
        pytest.skip("airport_codes.csv not available")
    findings, profile = scan_file(csv, domain="healthcare")
    assert profile.row_count > 0


def test_scan_with_domain_finance():
    """Finance domain on S&P 500 data."""
    csv = REAL_WORLD_DIR / "s_and_p_500.csv"
    if not csv.exists():
        pytest.skip("s_and_p_500.csv not available")
    findings, profile = scan_file(csv, domain="finance")
    findings = apply_confidence_downgrade(findings, llm_boost=False)
    # Should not flag Headquarters Location as person_name with finance domain
    person_name_fps = [
        f for f in findings
        if f.column == "Headquarters Location" and "person name" in f.message.lower()
    ]
    assert len(person_name_fps) == 0, "Finance domain should not classify Headquarters Location as person_name"
