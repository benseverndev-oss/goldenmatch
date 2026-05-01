"""Tests for the pipeline handoff module."""
from __future__ import annotations

import os

from goldencheck.agent.handoff import findings_to_fbc, generate_handoff
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import ColumnProfile, DatasetProfile

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "simple.csv")


def _make_profile() -> DatasetProfile:
    """Build a minimal DatasetProfile for testing."""
    return DatasetProfile(
        file_path=FIXTURE,
        row_count=5,
        column_count=3,
        columns=[
            ColumnProfile(
                name="id",
                inferred_type="integer",
                null_count=0,
                null_pct=0.0,
                unique_count=5,
                unique_pct=100.0,
                row_count=5,
            ),
            ColumnProfile(
                name="name",
                inferred_type="string",
                null_count=0,
                null_pct=0.0,
                unique_count=5,
                unique_pct=100.0,
                row_count=5,
            ),
            ColumnProfile(
                name="email",
                inferred_type="email",
                null_count=1,
                null_pct=20.0,
                unique_count=4,
                unique_pct=80.0,
                row_count=5,
            ),
        ],
    )


def test_generate_handoff_pass():
    """No errors, no warnings, no pending reviews -> PASS attestation."""
    findings = [
        Finding(
            severity=Severity.INFO,
            column="id",
            check="uniqueness",
            message="All values unique",
            confidence=0.95,
        ),
    ]
    profile = _make_profile()
    result = generate_handoff(
        file_path=FIXTURE,
        findings=findings,
        profile=profile,
        pinned_rules=[],
        review_pending=0,
        dismissed=0,
        job_name="test-pass",
    )
    assert result["attestation"] == "PASS"


def test_generate_handoff_pass_with_warnings():
    """Warnings exist, all pinned/dismissed, no pending -> PASS_WITH_WARNINGS."""
    findings = [
        Finding(
            severity=Severity.WARNING,
            column="email",
            check="nullability",
            message="20% null",
            confidence=0.9,
        ),
    ]
    profile = _make_profile()
    result = generate_handoff(
        file_path=FIXTURE,
        findings=findings,
        profile=profile,
        pinned_rules=[{"column": "email", "check": "nullability"}],
        review_pending=0,
        dismissed=0,
        job_name="test-warn",
    )
    assert result["attestation"] == "PASS_WITH_WARNINGS"


def test_generate_handoff_review_required():
    """Pending reviews > 0 -> REVIEW_REQUIRED."""
    findings = [
        Finding(
            severity=Severity.INFO,
            column="name",
            check="format",
            message="Mixed case",
            confidence=0.6,
        ),
    ]
    profile = _make_profile()
    result = generate_handoff(
        file_path=FIXTURE,
        findings=findings,
        profile=profile,
        pinned_rules=[],
        review_pending=2,
        dismissed=0,
        job_name="test-review",
    )
    assert result["attestation"] == "REVIEW_REQUIRED"


def test_generate_handoff_fail():
    """Errors present -> FAIL."""
    findings = [
        Finding(
            severity=Severity.ERROR,
            column="email",
            check="format_violation",
            message="Invalid email format",
            affected_rows=1,
            confidence=0.95,
        ),
    ]
    profile = _make_profile()
    result = generate_handoff(
        file_path=FIXTURE,
        findings=findings,
        profile=profile,
        pinned_rules=[],
        review_pending=0,
        dismissed=0,
        job_name="test-fail",
    )
    assert result["attestation"] == "FAIL"


def test_handoff_schema_version():
    """Verify schema_version is 1."""
    profile = _make_profile()
    result = generate_handoff(
        file_path=FIXTURE,
        findings=[],
        profile=profile,
        pinned_rules=[],
        review_pending=0,
        dismissed=0,
        job_name="test-schema",
    )
    assert result["schema_version"] == 1


def test_handoff_file_hash():
    """Verify file_hash starts with 'sha256:'."""
    profile = _make_profile()
    result = generate_handoff(
        file_path=FIXTURE,
        findings=[],
        profile=profile,
        pinned_rules=[],
        review_pending=0,
        dismissed=0,
        job_name="test-hash",
    )
    assert result["file_hash"].startswith("sha256:")
    # sha256 hex digest is 64 chars
    assert len(result["file_hash"]) == len("sha256:") + 64


def test_handoff_columns_populated():
    """Verify columns dict has entries from the profile."""
    profile = _make_profile()
    result = generate_handoff(
        file_path=FIXTURE,
        findings=[],
        profile=profile,
        pinned_rules=[],
        review_pending=0,
        dismissed=0,
        job_name="test-columns",
    )
    columns = result["columns"]
    assert len(columns) == 3
    assert "id" in columns
    assert "name" in columns
    assert "email" in columns
    # Each column entry should have type, null_pct, unique_pct, issues
    for col_data in columns.values():
        assert "type" in col_data
        assert "null_pct" in col_data
        assert "unique_pct" in col_data
        assert "issues" in col_data


def test_findings_to_fbc_helper():
    """Verify findings_to_fbc aggregates errors and warnings per column."""
    findings = [
        Finding(
            severity=Severity.ERROR,
            column="email",
            check="format",
            message="bad format",
        ),
        Finding(
            severity=Severity.ERROR,
            column="email",
            check="nullability",
            message="nulls found",
        ),
        Finding(
            severity=Severity.WARNING,
            column="age",
            check="range",
            message="outlier",
        ),
        Finding(
            severity=Severity.INFO,
            column="id",
            check="uniqueness",
            message="all unique",
        ),
    ]
    fbc = findings_to_fbc(findings)
    assert fbc["email"]["errors"] == 2
    assert fbc["email"]["warnings"] == 0
    assert fbc["age"]["errors"] == 0
    assert fbc["age"]["warnings"] == 1
    # INFO findings are not counted
    assert "id" not in fbc
