"""A2A controller-telemetry skill tests (v1.7-v1.12).

Covers the new `autoconfig` skill, the new `controller_telemetry` skill,
and the telemetry plumbing inside `deduplicate` / `match`.

Companion to test_mcp_controller_telemetry.py — A2A and MCP surface the
same controller artefacts, just through different agent protocols.
"""
from __future__ import annotations

import os
import tempfile

import polars as pl
import pytest

try:
    import aiohttp  # noqa: F401  # availability check for optional dep
    HAS_AIOHTTP = True
except ImportError:
    HAS_AIOHTTP = False

pytestmark = pytest.mark.skipif(not HAS_AIOHTTP, reason="aiohttp not installed")

if HAS_AIOHTTP:
    from goldenmatch.a2a.server import build_agent_card
    from goldenmatch.a2a.skills import dispatch_skill


@pytest.fixture
def tmp_csv():
    df = pl.DataFrame({
        "first_name": ["Alice", "alice", "Bob", "ALICE", "Charlie"],
        "last_name": ["Smith", "Smith", "Jones", "Smyth", "Brown"],
        "email": ["a@x.com", "a@x.com", "b@x.com", "a@x.com", "c@x.com"],
    })
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False) as f:
        df.write_csv(f.name)
        path = f.name
    yield path
    os.unlink(path)


class TestAgentCard:
    def test_card_advertises_v17_skills(self):
        """autoconfig + controller_telemetry must appear on the agent card."""
        card = build_agent_card("http://localhost:8200")
        ids = {s["id"] for s in card["skills"]}
        assert "autoconfig" in ids
        assert "controller_telemetry" in ids


class TestAutoconfigSkill:
    def test_returns_committed_config_and_telemetry(self, tmp_csv):
        """`autoconfig` skill runs the controller and returns both halves."""
        result = dispatch_skill("autoconfig", {"file_path": tmp_csv})
        assert "config" in result
        assert "telemetry" in result
        # Telemetry shape is shared with web / SQL / CLI / MCP — assert the
        # `available` key as the cross-surface contract.
        assert "available" in result["telemetry"]


class TestControllerTelemetrySkill:
    def test_per_session_note_on_stateless_dispatch(self):
        """A2A dispatch is stateless; surface that fact rather than silently
        returning the unavailable sentinel.
        """
        result = dispatch_skill("controller_telemetry", {})
        assert result["available"] is False
        assert "note" in result


class TestDeduplicateTelemetry:
    def test_deduplicate_result_includes_telemetry(self, tmp_csv):
        """The `deduplicate` skill embeds telemetry in its serialised result."""
        result = dispatch_skill("deduplicate", {"file_path": tmp_csv})
        assert "telemetry" in result
        assert "available" in result["telemetry"]
