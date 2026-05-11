"""Tests for the MCP controller-telemetry surface (v1.7-v1.12).

Covers the rewired `auto_configure` tool, the new `controller_telemetry`
tool, and telemetry plumbing through `agent_deduplicate` /
`agent_match_sources`.

Mirrors the web ControllerPanel and the CLI `goldenmatch autoconfig` —
all three surfaces should expose the same JSON shape so cross-surface
parsers / docs stay aligned.
"""
from __future__ import annotations

import os
import tempfile

import polars as pl
import pytest
from goldenmatch.core.agent import AgentSession
from goldenmatch.mcp.agent_tools import AGENT_TOOLS, _dispatch


@pytest.fixture
def tmp_csv():
    df = pl.DataFrame({
        "first_name": ["Alice", "alice", "Bob", "ALICE", "Charlie"],
        "last_name": ["Smith", "Smith", "Jones", "Smyth", "Brown"],
        "email": [
            "a@x.com", "a@x.com", "b@x.com",
            "a@x.com", "c@x.com",
        ],
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        df.write_csv(f.name)
        path = f.name
    yield path
    os.unlink(path)


class TestAgentToolsCatalog:
    def test_controller_telemetry_tool_registered(self):
        """New v1.7-v1.12 tool surfaces alongside auto_configure."""
        names = {t.name for t in AGENT_TOOLS}
        assert "auto_configure" in names
        assert "controller_telemetry" in names

    def test_auto_configure_description_mentions_controller(self):
        """Description should signal it now runs the controller (v1.7+)."""
        tool = next(t for t in AGENT_TOOLS if t.name == "auto_configure")
        # Either the new wording or the legacy one — both acceptable, but
        # at least one v1.7-v1.12 keyword should show.
        text = tool.description.lower()
        assert any(
            kw in text
            for kw in ("autoconfigcontroller", "controller", "telemetry", "stop_reason", "path y")
        ), f"description didn't mention v1.7+ concepts: {tool.description!r}"


class TestAutoConfigureDispatch:
    def test_returns_config_and_telemetry(self, tmp_csv):
        """auto_configure now invokes the controller and returns telemetry."""
        result = _dispatch(
            "auto_configure",
            {"file_path": tmp_csv},
            AgentSession,
        )
        assert "config" in result
        assert "telemetry" in result
        telemetry = result["telemetry"]
        assert "available" in telemetry
        if telemetry["available"]:
            # Controller-driven path produces stop_reason + health.
            assert telemetry.get("stop_reason") is not None
            assert telemetry.get("health") in {"green", "yellow", "red"}


class TestControllerTelemetryDispatch:
    def test_returns_unavailable_sentinel(self, tmp_csv):
        """controller_telemetry is per-session; MCP dispatch is stateless,
        so the dispatch should return an unavailable + a note explaining
        why callers should use inline telemetry from auto_configure instead.
        """
        result = _dispatch(
            "controller_telemetry",
            {},
            AgentSession,
        )
        assert result["available"] is False
        assert "note" in result


class TestAgentDeduplicateTelemetry:
    def test_includes_telemetry_field(self, tmp_csv):
        """agent_deduplicate result includes a telemetry blob (or sentinel)."""
        result = _dispatch(
            "agent_deduplicate",
            {"file_path": tmp_csv},
            AgentSession,
        )
        assert "telemetry" in result
        assert "available" in result["telemetry"]
