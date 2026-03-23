"""Integration test: full LLM boost flow with mocked provider."""
import json
from pathlib import Path
from unittest.mock import patch
from goldencheck.engine.scanner import scan_file_with_llm

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
    findings, profile = scan_file_with_llm(FIXTURES / "simple.csv", provider="anthropic")
    assert any(f.source == "llm" for f in findings)
    assert any(f.check == "semantic" for f in findings)
    mock_call.assert_called_once()
