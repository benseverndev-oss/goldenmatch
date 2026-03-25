"""Tests for the A2A protocol server and skill dispatch."""
from __future__ import annotations

import os

from goldencheck.a2a.server import AGENT_CARD, _check_auth
from goldencheck.a2a.skills import dispatch_skill

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "simple.csv")

REQUIRED_CARD_FIELDS = [
    "name",
    "description",
    "url",
    "version",
    "provider",
    "capabilities",
    "skills",
    "authentication",
]


def test_agent_card_structure():
    """Verify AGENT_CARD has all required top-level fields."""
    for field in REQUIRED_CARD_FIELDS:
        assert field in AGENT_CARD, f"Missing field: {field}"
    assert isinstance(AGENT_CARD["provider"], dict)
    assert "organization" in AGENT_CARD["provider"]
    assert isinstance(AGENT_CARD["capabilities"], dict)
    assert isinstance(AGENT_CARD["authentication"], dict)
    assert "schemes" in AGENT_CARD["authentication"]


def test_agent_card_skills():
    """Verify all 9 skills have id, name, description, inputModes, outputModes."""
    skills = AGENT_CARD["skills"]
    assert len(skills) == 9
    for skill in skills:
        assert "id" in skill, f"Skill missing 'id': {skill}"
        assert "name" in skill, f"Skill missing 'name': {skill}"
        assert "description" in skill, f"Skill missing 'description': {skill}"
        assert "inputModes" in skill, f"Skill missing 'inputModes': {skill}"
        assert "outputModes" in skill, f"Skill missing 'outputModes': {skill}"
        assert len(skill["inputModes"]) > 0
        assert len(skill["outputModes"]) > 0


def _make_message(params: dict) -> dict:
    """Build an A2A-style message wrapping params."""
    return {"role": "user", "parts": [{"type": "data", "data": params}]}


def test_skill_dispatch_analyze():
    """dispatch_skill('analyze_data', ...) returns domain and strategy info."""
    message = _make_message({"file_path": FIXTURE})
    result = dispatch_skill("analyze_data", message)
    assert "error" not in result
    assert "domain" in result
    assert "domain_confidence" in result
    assert "sample_strategy" in result
    assert "profiler_strategy" in result


def test_skill_dispatch_scan():
    """dispatch_skill('scan', ...) returns findings and health info."""
    message = _make_message({"file_path": FIXTURE})
    result = dispatch_skill("scan", message)
    assert "error" not in result
    assert "job_name" in result
    assert "row_count" in result
    assert "column_count" in result
    assert "health" in result
    assert "total_findings" in result
    assert "findings" in result
    assert isinstance(result["findings"], list)


def test_skill_dispatch_unknown():
    """Unknown skill returns an error dict."""
    result = dispatch_skill("nonexistent_skill", {})
    assert "error" in result
    assert "nonexistent_skill" in result["error"]


class _FakeRequest:
    """Minimal stand-in for aiohttp.web.Request with just headers."""

    def __init__(self, headers: dict | None = None):
        self.headers = headers or {}


def test_auth_check_no_token(monkeypatch):
    """With no GOLDENCHECK_AGENT_TOKEN env var, auth always passes."""
    monkeypatch.delenv("GOLDENCHECK_AGENT_TOKEN", raising=False)
    request = _FakeRequest()
    assert _check_auth(request) is True


def test_auth_check_valid_token(monkeypatch):
    """With token set, a valid Bearer header passes."""
    monkeypatch.setenv("GOLDENCHECK_AGENT_TOKEN", "secret123")
    request = _FakeRequest(headers={"Authorization": "Bearer secret123"})
    assert _check_auth(request) is True


def test_auth_check_invalid_token(monkeypatch):
    """With token set, an invalid Bearer header fails."""
    monkeypatch.setenv("GOLDENCHECK_AGENT_TOKEN", "secret123")
    request = _FakeRequest(headers={"Authorization": "Bearer wrong-token"})
    assert _check_auth(request) is False
