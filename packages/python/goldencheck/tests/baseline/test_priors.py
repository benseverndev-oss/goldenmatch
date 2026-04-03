"""Tests for goldencheck.baseline.priors — TDD."""
from __future__ import annotations

import pytest

from goldencheck.baseline.models import ConfidencePrior
from goldencheck.baseline.priors import apply_prior, build_priors
from goldencheck.models.finding import Finding, Severity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(check: str, column: str, confidence: float) -> Finding:
    return Finding(
        severity=Severity.WARNING,
        column=column,
        check=check,
        message="test",
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# build_priors
# ---------------------------------------------------------------------------


def test_build_priors_empty_findings():
    """Empty findings list returns empty dict."""
    result = build_priors([], row_count=1000)
    assert result == {}


def test_build_priors_single_finding():
    """Single finding produces correct check/column keys."""
    findings = [_make_finding("nulls", "email", 0.9)]
    result = build_priors(findings, row_count=500)

    assert "nulls" in result
    assert "email" in result["nulls"]
    prior = result["nulls"]["email"]
    assert prior.confidence == pytest.approx(0.9)
    assert prior.evidence_count == 500


def test_build_priors_keys_and_confidence_positive():
    """Verify check/column keys exist and confidence > 0."""
    findings = [
        _make_finding("nulls", "age", 0.7),
        _make_finding("outlier", "salary", 0.85),
        _make_finding("nulls", "name", 0.6),
    ]
    result = build_priors(findings, row_count=200)

    assert "nulls" in result
    assert "outlier" in result
    assert "age" in result["nulls"]
    assert "name" in result["nulls"]
    assert "salary" in result["outlier"]
    assert result["nulls"]["age"].confidence > 0
    assert result["outlier"]["salary"].confidence > 0


def test_build_priors_averages_confidence_per_group():
    """Multiple findings for the same (check, column) are averaged."""
    findings = [
        _make_finding("nulls", "email", 0.6),
        _make_finding("nulls", "email", 0.8),
        _make_finding("nulls", "email", 1.0),
    ]
    result = build_priors(findings, row_count=300)

    prior = result["nulls"]["email"]
    assert prior.confidence == pytest.approx(0.8)  # (0.6 + 0.8 + 1.0) / 3
    assert prior.evidence_count == 300


def test_build_priors_groups_by_check_and_column_independently():
    """Same column under different checks creates separate entries."""
    findings = [
        _make_finding("nulls", "age", 0.7),
        _make_finding("outlier", "age", 0.9),
    ]
    result = build_priors(findings, row_count=100)

    assert result["nulls"]["age"].confidence == pytest.approx(0.7)
    assert result["outlier"]["age"].confidence == pytest.approx(0.9)


def test_build_priors_evidence_count_is_row_count():
    """All priors carry the row_count as evidence_count."""
    findings = [
        _make_finding("nulls", "x", 0.5),
        _make_finding("outlier", "y", 0.5),
    ]
    result = build_priors(findings, row_count=12345)

    assert result["nulls"]["x"].evidence_count == 12345
    assert result["outlier"]["y"].evidence_count == 12345


# ---------------------------------------------------------------------------
# apply_prior
# ---------------------------------------------------------------------------


def test_apply_prior_result_between_raw_and_prior():
    """Result should be between raw_confidence and prior.confidence."""
    prior = ConfidencePrior(confidence=0.8, evidence_count=100)
    raw = 0.4
    result = apply_prior(raw, prior)
    # evidence_weight=1.0, prior_weight=min(100/100, 1.0)=1.0
    # adjusted = (0.4*1.0 + 0.8*1.0) / 2.0 = 0.6
    assert min(raw, prior.confidence) <= result <= max(raw, prior.confidence)
    assert result == pytest.approx(0.6)


def test_apply_prior_weak_prior_has_small_effect():
    """Weak prior (evidence_count=10) barely shifts raw confidence."""
    prior = ConfidencePrior(confidence=0.9, evidence_count=10)
    raw = 0.2
    result = apply_prior(raw, prior)
    # prior_weight = min(10/100, 1.0) = 0.1
    # adjusted = (0.2*1.0 + 0.9*0.1) / (1.0 + 0.1) = (0.2 + 0.09) / 1.1 ≈ 0.2636
    assert result == pytest.approx(0.2636, abs=1e-4)
    # Small shift: stays much closer to raw than to prior
    assert abs(result - raw) < abs(result - prior.confidence)


def test_apply_prior_strong_prior_has_large_effect():
    """Strong prior (evidence_count=10000) is capped at equal influence (weight=1.0)."""
    prior = ConfidencePrior(confidence=0.9, evidence_count=10000)
    raw = 0.2
    result = apply_prior(raw, prior)
    # prior_weight = min(10000/100, 1.0) = 1.0 (capped)
    # adjusted = (0.2*1.0 + 0.9*1.0) / (1.0 + 1.0) = 1.1 / 2.0 = 0.55
    assert result == pytest.approx(0.55)
    # Large shift: midpoint between raw and prior
    assert abs(result - prior.confidence) < abs(result - raw)


def test_apply_prior_clamps_to_zero():
    """Result is clamped to 0.0 if formula would go negative."""
    prior = ConfidencePrior(confidence=0.0, evidence_count=100)
    result = apply_prior(-0.5, prior)  # raw below 0
    assert result >= 0.0


def test_apply_prior_clamps_to_one():
    """Result is clamped to 1.0 if formula would exceed 1.0."""
    prior = ConfidencePrior(confidence=1.0, evidence_count=100)
    result = apply_prior(1.5, prior)  # raw above 1
    assert result <= 1.0


def test_apply_prior_rounds_to_4_decimal_places():
    """Result is rounded to exactly 4 decimal places."""
    prior = ConfidencePrior(confidence=0.9, evidence_count=10)
    result = apply_prior(0.2, prior)
    # Verify it has at most 4 decimal places
    assert result == round(result, 4)


def test_apply_prior_same_raw_and_prior_returns_same():
    """When raw equals prior.confidence the result is unchanged."""
    prior = ConfidencePrior(confidence=0.7, evidence_count=50)
    result = apply_prior(0.7, prior)
    assert result == pytest.approx(0.7)
