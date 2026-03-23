"""Integration test: full LLM boost flow with mocked provider."""
import json
from pathlib import Path
from unittest.mock import patch
import polars as pl
from goldencheck.engine.scanner import scan_file_with_llm
from goldencheck.models.finding import Finding, Severity

FIXTURES = Path(__file__).parent.parent / "fixtures"

MOCK_RESPONSE = json.dumps({
    "columns": {
        "email": {
            "semantic_type": "email",
            "issues": [{"severity": "error", "check": "semantic", "message": "non-emails in email column", "affected_values": ["not-an-email"]}],
            "upgrades": [],
            "downgrades": [],
        }
    },
    "relations": [],
})

@patch("goldencheck.llm.providers.check_llm_available")
@patch("goldencheck.llm.providers.call_llm", return_value=(MOCK_RESPONSE, 1800, 420))
def test_llm_boost_integration(mock_call, mock_check):
    """When all profiler findings are high confidence, LLM boost is skipped."""
    findings, profile = scan_file_with_llm(FIXTURES / "simple.csv", provider="anthropic")
    # All findings from simple.csv are >= 0.5 confidence after corroboration boost,
    # so LLM is correctly skipped — no LLM-sourced findings expected.
    mock_call.assert_not_called()
    assert profile is not None


@patch("goldencheck.llm.providers.check_llm_available")
@patch("goldencheck.llm.providers.call_llm", return_value=(MOCK_RESPONSE, 1800, 420))
def test_llm_boost_called_when_low_confidence_findings(mock_call, mock_check):
    """When low-confidence findings exist, LLM boost is invoked."""
    low_conf_finding = Finding(
        severity=Severity.WARNING,
        column="email",
        check="format_detection",
        message="suspicious values",
        confidence=0.3,
    )

    with patch("goldencheck.engine.scanner.scan_file") as mock_scan:
        mock_profile = object()
        # Return a low-confidence finding so LLM boost is triggered
        mock_scan.return_value = ([low_conf_finding], mock_profile, pl.DataFrame({"email": ["a@b.com"]}))
        findings, profile = scan_file_with_llm(FIXTURES / "simple.csv", provider="anthropic")

    mock_call.assert_called_once()
    assert any(f.source == "llm" for f in findings)
    assert any(f.check == "semantic" for f in findings)
