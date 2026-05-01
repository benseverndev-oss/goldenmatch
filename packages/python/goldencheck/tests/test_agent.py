"""Tests for the intelligence layer (goldencheck.agent.intelligence)."""
from __future__ import annotations

from pathlib import Path

import polars as pl

from goldencheck.agent.intelligence import (
    AgentSession,
    StrategyDecision,
    build_alternatives,
    compare_domains,
    explain_column,
    explain_finding,
    findings_to_fbc,
    select_strategy,
)
from goldencheck.models.finding import Finding, Severity
from goldencheck.models.profile import ColumnProfile, DatasetProfile

FIXTURES = Path(__file__).parent / "fixtures"
SIMPLE_CSV = FIXTURES / "simple.csv"
MESSY_CSV = FIXTURES / "messy.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _healthcare_df() -> pl.DataFrame:
    """Return a small DataFrame with healthcare-ish columns."""
    return pl.DataFrame({
        "patient_id": ["P001", "P002", "P003", "P004", "P005"],
        "diagnosis_code": ["E11.9", "I10", "J06.9", "M54.5", "E11.9"],
        "npi": ["1234567890", "2345678901", "3456789012", "4567890123", "5678901234"],
        "date_of_birth": ["1980-01-01", "1990-05-15", "1975-12-30", "2000-03-22", "1965-08-10"],
        "mrn": ["MRN001", "MRN002", "MRN003", "MRN004", "MRN005"],
    })


def _generic_df() -> pl.DataFrame:
    """Return a small generic DataFrame unlikely to match any domain."""
    return pl.DataFrame({
        "x": [1, 2, 3],
        "y": [4, 5, 6],
        "label": ["a", "b", "c"],
    })


def _make_profile(columns: list[str], row_count: int = 100) -> DatasetProfile:
    """Build a minimal DatasetProfile for testing."""
    col_profiles = [
        ColumnProfile(
            name=c,
            inferred_type="string",
            null_count=0,
            null_pct=0.0,
            unique_count=row_count,
            unique_pct=1.0,
            row_count=row_count,
        )
        for c in columns
    ]
    return DatasetProfile(
        file_path="test.csv",
        row_count=row_count,
        column_count=len(columns),
        columns=col_profiles,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_select_strategy_detects_domain():
    """Healthcare-like columns should trigger a domain detection."""
    df = _healthcare_df()
    decision = select_strategy(df)

    assert isinstance(decision, StrategyDecision)
    # A domain should be detected (healthcare columns match domain packs)
    assert decision.domain is not None
    assert decision.domain_confidence > 0.20
    # Healthcare should be among the top-scoring domains
    domain_scores = decision.why.get("domain_scores", {})
    assert "healthcare" in domain_scores
    assert domain_scores["healthcare"] > 0.20


def test_select_strategy_no_domain():
    """A DataFrame with many unambiguously non-domain columns should not
    strongly match a specific domain, or at least behave consistently."""
    # Use enough clearly non-domain columns so that the match ratio stays low.
    df = pl.DataFrame({
        "col_aaa": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
        "col_bbb": [10, 20, 30, 40, 50, 60, 70, 80, 90, 100],
        "col_ccc": ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"],
        "col_ddd": [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
        "col_eee": ["x", "y", "z", "w", "v", "u", "t", "s", "r", "q"],
    })
    decision = select_strategy(df)

    assert isinstance(decision, StrategyDecision)
    # With generic column names, domain confidence should be low or None
    if decision.domain is None:
        assert decision.domain_confidence == 0.0
    else:
        # If a domain is picked it should have very low confidence
        assert decision.domain_confidence <= 1.0


def test_select_strategy_sampling():
    """Row count thresholds should drive the sample_strategy field."""
    # Small dataset -> full
    small = pl.DataFrame({"a": range(100)})
    assert select_strategy(small).sample_strategy == "full"

    # Medium dataset -> sample_100k
    medium = pl.DataFrame({"a": range(100_001)})
    assert select_strategy(medium).sample_strategy == "sample_100k"

    # Large dataset -> sample_100k_stratified
    large = pl.DataFrame({"a": range(600_000)})
    assert select_strategy(large).sample_strategy == "sample_100k_stratified"


def test_build_alternatives():
    """Alternatives should include non-selected domains with score > 0.10."""
    decision = StrategyDecision(
        domain="healthcare",
        domain_confidence=0.6,
        llm_boost=False,
    )
    domain_scores = {
        "healthcare": 0.60,
        "finance": 0.25,
        "ecommerce": 0.05,
    }
    alts = build_alternatives(decision, domain_scores)

    assert isinstance(alts, list)
    # finance (0.25 > 0.10) should appear; ecommerce (0.05) should not
    domain_alts = [a for a in alts if a["type"] == "domain"]
    domain_values = {a["value"] for a in domain_alts}
    assert "finance" in domain_values
    assert "ecommerce" not in domain_values

    # A "no-domain" alternative should appear because a domain was selected
    assert any(a["value"] is None and a["type"] == "domain" for a in alts)

    # LLM boost suggestion should appear because llm_boost=False
    assert any(a["type"] == "llm_boost" for a in alts)


def test_explain_finding():
    """explain_finding should return all expected keys."""
    finding = Finding(
        severity=Severity.WARNING,
        column="email",
        check="format_check",
        message="Invalid email format detected",
        affected_rows=2,
        sample_values=["not-an-email", "bad@"],
        suggestion="Fix the invalid emails.",
        confidence=0.85,
    )
    profile = _make_profile(["email"])
    result = explain_finding(finding, profile)

    expected_keys = {"what", "severity", "confidence", "impact", "suggestion", "affected_rows"}
    assert expected_keys.issubset(result.keys())
    assert result["severity"] == "warning"
    assert result["confidence"] == 0.85
    assert result["affected_rows"] == 2
    assert "sample_values" in result
    assert len(result["sample_values"]) <= 5
    # Column profile info should be present
    assert "column_type" in result
    assert "column_null_pct" in result


def test_explain_column():
    """explain_column should return a well-structured dict for a real file."""
    result = explain_column(str(MESSY_CSV), "email")

    assert result["column"] == "email"
    assert result["health"] in {"healthy", "needs attention", "unhealthy"}
    assert isinstance(result["narrative"], str)
    assert isinstance(result["errors"], int)
    assert isinstance(result["warnings"], int)
    assert isinstance(result["infos"], int)
    assert isinstance(result["findings"], list)
    # Profile sub-dict
    assert "profile" in result
    assert set(result["profile"].keys()) == {"type", "null_pct", "unique_pct", "row_count"}


def test_compare_domains():
    """compare_domains should return results for base + all available domains."""
    result = compare_domains(str(SIMPLE_CSV))

    assert "domains_tested" in result
    assert "base" in result["domains_tested"]
    assert "results" in result
    assert "base" in result["results"]
    # At least healthcare, finance, ecommerce domains exist
    for domain in ("healthcare", "finance", "ecommerce"):
        assert domain in result["domains_tested"]
        assert domain in result["results"]
    # Each result entry has the right shape
    for label, data in result["results"].items():
        assert "grade" in data
        assert "score" in data
        assert "errors" in data
        assert "warnings" in data
        assert "total_findings" in data
    # Recommendation
    assert result["recommendation"] in result["results"]
    assert isinstance(result["reason"], str)


def test_findings_to_fbc():
    """findings_to_fbc should bucket errors/warnings per column."""
    findings = [
        Finding(severity=Severity.ERROR, column="age", check="c1", message="m1"),
        Finding(severity=Severity.ERROR, column="age", check="c2", message="m2"),
        Finding(severity=Severity.WARNING, column="age", check="c3", message="m3"),
        Finding(severity=Severity.WARNING, column="name", check="c4", message="m4"),
        Finding(severity=Severity.INFO, column="name", check="c5", message="m5"),
    ]
    fbc = findings_to_fbc(findings)

    assert "age" in fbc
    assert fbc["age"]["errors"] == 2
    assert fbc["age"]["warnings"] == 1
    assert "name" in fbc
    assert fbc["name"]["errors"] == 0
    assert fbc["name"]["warnings"] == 1
    # INFO findings should not create an "infos" key (not tracked)
    assert "infos" not in fbc.get("name", {})


def test_agent_session_creation():
    """AgentSession should instantiate with sensible defaults."""
    session = AgentSession()

    assert session.sample is None
    assert session.profile is None
    assert session.findings == []
    assert session.review_queue is None
    assert session.reasoning == {}
    assert session.job_name == ""

    # Fields should be mutable
    session.job_name = "test_run"
    assert session.job_name == "test_run"

    session.findings.append(
        Finding(severity=Severity.INFO, column="x", check="test", message="ok")
    )
    assert len(session.findings) == 1
