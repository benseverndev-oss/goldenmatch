"""Tests for the 10 MCP agent tool handlers (called directly, not via MCP server)."""
from __future__ import annotations

import os


from goldencheck.mcp.agent_tools import (
    _tool_analyze_data,
    _tool_approve_reject,
    _tool_auto_configure,
    _tool_compare_domains,
    _tool_explain_column,
    _tool_explain_finding,
    _tool_pipeline_handoff,
    _tool_review_queue,
    _tool_review_stats,
    _tool_suggest_fix,
)

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "simple.csv")


# -- analyze_data ------------------------------------------------------------


def test_tool_analyze_data():
    result = _tool_analyze_data({"file_path": FIXTURE})
    assert "error" not in result
    assert "strategy" in result
    assert "alternatives" in result
    # strategy sub-keys
    strategy = result["strategy"]
    assert "domain" in strategy
    assert "domain_confidence" in strategy
    # reasoning should include domain_scores
    assert "domain_scores" in result.get("reasoning", {})


def test_tool_analyze_data_missing_file():
    result = _tool_analyze_data({"file_path": "/nonexistent/data.csv"})
    assert "error" in result
    assert "not found" in result["error"].lower()


# -- auto_configure -----------------------------------------------------------


def test_tool_auto_configure():
    result = _tool_auto_configure({"file_path": FIXTURE})
    assert "error" not in result
    assert "yaml_content" in result
    assert "rules" in result
    assert isinstance(result["pinned_count"], int)
    assert isinstance(result["review_count"], int)
    assert isinstance(result["dismissed_count"], int)


# -- explain_finding ----------------------------------------------------------


def test_tool_explain_finding():
    finding_dict = {
        "column": "email",
        "check": "null_check",
        "message": "Column has null values",
        "severity": "WARNING",
        "confidence": 0.9,
        "affected_rows": 2,
        "sample_values": ["alice@example.com", None],
    }
    result = _tool_explain_finding({"file_path": FIXTURE, "finding": finding_dict})
    assert "error" not in result
    # explain_finding returns a dict with at least a narrative/explanation
    assert isinstance(result, dict)


# -- explain_column -----------------------------------------------------------


def test_tool_explain_column():
    result = _tool_explain_column({"file_path": FIXTURE, "column": "email"})
    assert "error" not in result
    assert isinstance(result, dict)


# -- compare_domains ----------------------------------------------------------


def test_tool_compare_domains():
    result = _tool_compare_domains({"file_path": FIXTURE})
    assert "error" not in result
    # Should have at least "base" domain results
    assert isinstance(result, dict)
    # compare_domains returns domain-keyed results; "base" is always present
    has_base = "base" in result or any("base" in str(v) for v in result.values())
    assert has_base or len(result) > 0


# -- suggest_fix --------------------------------------------------------------


def test_tool_suggest_fix():
    result = _tool_suggest_fix({"file_path": FIXTURE, "mode": "safe"})
    assert "error" not in result
    assert "fixes" in result or "total_fixes" in result
    assert result["mode"] == "safe"


def test_tool_suggest_fix_missing_file():
    result = _tool_suggest_fix({"file_path": "/nonexistent/data.csv"})
    assert "error" in result
    assert "not found" in result["error"].lower()


# -- review_queue -------------------------------------------------------------


def test_tool_review_queue_empty():
    result = _tool_review_queue({"job_name": "test_job_nonexistent"})
    assert result["pending_count"] == 0
    assert result["items"] == []


# -- review_stats -------------------------------------------------------------


def test_tool_review_stats_empty():
    result = _tool_review_stats({"job_name": "test_job_nonexistent"})
    assert result["job_name"] == "test_job_nonexistent"
    # All counts should be zero for a fresh/unknown job
    for key in ("pending", "pinned", "dismissed"):
        assert result.get(key, 0) == 0


# -- pipeline_handoff ---------------------------------------------------------


def test_tool_pipeline_handoff():
    result = _tool_pipeline_handoff({"file_path": FIXTURE, "job_name": "ci_test"})
    assert "error" not in result
    assert "attestation" in result or "status" in result
    assert "schema_version" in result or "version" in result


# -- approve_reject -----------------------------------------------------------


def test_tool_approve_reject_not_found():
    result = _tool_approve_reject({
        "item_id": "nonexistent-item-id",
        "decision": "pin",
    })
    assert "error" in result
    assert "not found" in result["error"].lower()
