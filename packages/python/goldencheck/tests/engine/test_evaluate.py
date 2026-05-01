"""Tests for evaluate_scan — ground-truth comparison."""
from __future__ import annotations

from goldencheck.engine.evaluate import evaluate_scan
from goldencheck.models.finding import Finding, Severity


def _make_findings(pairs: list[tuple[str, str]]) -> list[Finding]:
    """Build minimal Finding objects from (column, check) tuples."""
    return [
        Finding(severity=Severity.WARNING, column=col, check=chk, message="test")
        for col, chk in pairs
    ]


def test_perfect_score():
    """All expected findings are present, no extras."""
    findings = _make_findings([("age", "null_ratio"), ("name", "whitespace")])
    expected = [
        {"column": "age", "check": "null_ratio"},
        {"column": "name", "check": "whitespace"},
    ]
    result = evaluate_scan(findings, expected)
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0
    assert result["f1"] == 1.0
    assert result["true_positives"] == 2
    assert result["false_positives"] == 0
    assert result["false_negatives"] == 0


def test_partial_recall():
    """Scanner misses one expected finding."""
    findings = _make_findings([("age", "null_ratio")])
    expected = [
        {"column": "age", "check": "null_ratio"},
        {"column": "name", "check": "whitespace"},
    ]
    result = evaluate_scan(findings, expected)
    assert result["precision"] == 1.0
    assert result["recall"] == 0.5
    assert result["true_positives"] == 1
    assert result["false_negatives"] == 1
    assert result["false_positives"] == 0
    assert 0 < result["f1"] < 1.0


def test_false_positives():
    """Scanner produces findings not in the expected set."""
    findings = _make_findings([("age", "null_ratio"), ("email", "format")])
    expected = [{"column": "age", "check": "null_ratio"}]
    result = evaluate_scan(findings, expected)
    assert result["precision"] == 0.5
    assert result["recall"] == 1.0
    assert result["true_positives"] == 1
    assert result["false_positives"] == 1
    assert result["false_negatives"] == 0


def test_empty_findings():
    """No findings produced, some expected — recall should be 0."""
    expected = [{"column": "age", "check": "null_ratio"}]
    result = evaluate_scan([], expected)
    assert result["precision"] == 1.0
    assert result["recall"] == 0.0
    assert result["f1"] == 0.0
    assert result["false_negatives"] == 1


def test_empty_expected():
    """No expected findings, some actual — precision should be 0."""
    findings = _make_findings([("age", "null_ratio")])
    result = evaluate_scan(findings, [])
    assert result["precision"] == 0.0
    assert result["recall"] == 1.0
    assert result["f1"] == 0.0
    assert result["false_positives"] == 1


def test_both_empty():
    """No findings and no expected — perfect score by convention."""
    result = evaluate_scan([], [])
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0
    assert result["f1"] == 1.0
    assert result["true_positives"] == 0


def test_details_sorted():
    """Detail tuples should be sorted for stable output."""
    findings = _make_findings([("b", "check2"), ("a", "check1")])
    expected = [{"column": "a", "check": "check1"}, {"column": "b", "check": "check2"}]
    result = evaluate_scan(findings, expected)
    assert result["tp_details"] == [("a", "check1"), ("b", "check2")]
