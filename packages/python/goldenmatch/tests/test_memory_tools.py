"""Phase 7: MCP memory tool surface.

Five MCP tools wrap MemoryStore operations:
  list_corrections, add_correction, learn_thresholds, memory_stats, memory_export.

Each handler instantiates its own MemoryStore (matches AgentSession pattern --
no shared global state).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from goldenmatch.mcp.memory_tools import (
    _MEMORY_TOOL_NAMES,
    MEMORY_TOOLS,
    handle_memory_tool,
)

EXPECTED_NAMES = {
    "list_corrections",
    "add_correction",
    "learn_thresholds",
    "memory_stats",
    "memory_export",
    # v1.18.3 (#predefined-merge-plugins): plugin discovery via MCP.
    "list_plugins",
    # MCP tool-coverage pass: round-trips memory_export.
    "memory_import",
}


def test_memory_tools_registered():
    """MEMORY_TOOLS exposes the seven tool definitions; names match frozenset."""
    assert len(MEMORY_TOOLS) == 7
    names = {t.name for t in MEMORY_TOOLS}
    assert names == EXPECTED_NAMES
    assert _MEMORY_TOOL_NAMES == frozenset(EXPECTED_NAMES)
    # Each tool has a non-empty description and an inputSchema dict.
    for tool in MEMORY_TOOLS:
        assert tool.description
        assert isinstance(tool.inputSchema, dict)


def test_mcp_add_and_list_correction(tmp_path):
    """add_correction round-trips to list_corrections."""
    db = str(tmp_path / "mem.db")
    add_result = handle_memory_tool(
        "add_correction",
        {
            "id_a": 7,
            "id_b": 3,
            "decision": "approve",
            "dataset": "test_ds",
            "reason": "looks good",
            "path": db,
        },
    )
    assert len(add_result) == 1
    add_payload = json.loads(add_result[0].text)
    assert add_payload.get("status") == "ok"

    list_result = handle_memory_tool(
        "list_corrections",
        {"dataset": "test_ds", "path": db},
    )
    list_payload = json.loads(list_result[0].text)
    corrections = list_payload.get("corrections", [])
    assert len(corrections) == 1
    c = corrections[0]
    # Pair canonicalized to (min, max).
    assert c["id_a"] == 3
    assert c["id_b"] == 7
    assert c["decision"] == "approve"
    assert c["source"] == "agent"
    assert c["trust"] == 0.5
    assert c["dataset"] == "test_ds"
    assert c.get("reason") == "looks good"


def test_mcp_memory_stats_and_export(tmp_path):
    """memory_stats and memory_export work after a write."""
    db = str(tmp_path / "mem.db")
    handle_memory_tool(
        "add_correction",
        {
            "id_a": 1,
            "id_b": 2,
            "decision": "reject",
            "dataset": "ds1",
            "path": db,
        },
    )

    stats_payload = json.loads(
        handle_memory_tool("memory_stats", {"path": db})[0].text
    )
    assert stats_payload.get("total_corrections") == 1
    assert "last_learn_time" in stats_payload
    assert "adjustments" in stats_payload

    export_payload = json.loads(
        handle_memory_tool("memory_export", {"path": db})[0].text
    )
    rows = export_payload.get("corrections", [])
    assert len(rows) == 1
    assert rows[0]["dataset"] == "ds1"


def test_mcp_learn_thresholds_handler(tmp_path):
    """learn_thresholds handler runs without errors on empty store."""
    db = str(tmp_path / "mem.db")
    payload = json.loads(
        handle_memory_tool("learn_thresholds", {"path": db})[0].text
    )
    # Empty store -> no adjustments learned, but call must succeed.
    assert "adjustments" in payload


def test_mcp_add_correction_validates_dataset(tmp_path):
    """add_correction with empty dataset returns a structured error."""
    db = str(tmp_path / "mem.db")
    result = handle_memory_tool(
        "add_correction",
        {
            "id_a": 1,
            "id_b": 2,
            "decision": "approve",
            "dataset": "",
            "path": db,
        },
    )
    payload = json.loads(result[0].text)
    assert "error" in payload


def test_memory_tools_op_error_returns_structured_error(tmp_path, monkeypatch):
    """sqlite3.OperationalError is trapped and returned as TextContent error."""
    import sqlite3

    from goldenmatch.mcp import memory_tools as mt

    class _BoomStore:
        def __init__(self, *a, **k):
            raise sqlite3.OperationalError("disk I/O error")

    monkeypatch.setattr(mt, "MemoryStore", _BoomStore)

    result = handle_memory_tool(
        "memory_stats", {"path": str(tmp_path / "irrelevant.db")}
    )
    assert len(result) == 1
    payload = json.loads(result[0].text)
    assert "error" in payload
    assert "disk I/O error" in payload["error"] or "OperationalError" in payload["error"]


def test_server_card_description_count():
    """server.py description string advertises 55 MCP tools."""
    server_path = (
        Path(__file__).resolve().parent.parent / "goldenmatch" / "mcp" / "server.py"
    )
    text = server_path.read_text(encoding="utf-8")
    match = re.search(r"(\d+) MCP tools", text)
    assert match is not None
    assert int(match.group(1)) == 55
