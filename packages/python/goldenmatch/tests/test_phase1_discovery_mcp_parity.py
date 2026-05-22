"""Tests for Phase 1 of v1.18.3 surface-sync roadmap.

Spec: docs/superpowers/specs/2026-05-22-phase-1-discovery-mcp-parity-design.md

Covers:
- 1.1 Python API re-exports (PluginRegistry, BUILTIN_PLUGINS,
      CorrectionSource, Decision)
- 1.2 MCP add_correction schema extension (field_correct decision +
      3 new optional properties)
- 1.3 MCP list_plugins tool
- 1.4 CLI `goldenmatch memory add` command (pair + field shapes)
"""
from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

# ---------------------------------------------------------------------------
# 1.1 Python API re-exports
# ---------------------------------------------------------------------------


def test_re_exports_plugin_registry_at_top_level():
    import goldenmatch
    assert hasattr(goldenmatch, "PluginRegistry")
    # The class itself, not an instance.
    assert callable(goldenmatch.PluginRegistry)


def test_re_exports_builtin_plugins_at_top_level():
    import goldenmatch
    assert hasattr(goldenmatch, "BUILTIN_PLUGINS")
    # 22 plugins after v1.18.2.
    assert len(goldenmatch.BUILTIN_PLUGINS) == 22


def test_re_exports_decision_and_correction_source_at_top_level():
    import goldenmatch
    assert hasattr(goldenmatch, "Decision")
    assert hasattr(goldenmatch, "CorrectionSource")
    assert goldenmatch.Decision.FIELD_CORRECT.value == "field_correct"


def test_all_includes_new_surface_names():
    import goldenmatch
    for name in (
        "PluginRegistry", "BUILTIN_PLUGINS", "Decision", "CorrectionSource",
    ):
        assert name in goldenmatch.__all__, f"{name} missing from __all__"


# ---------------------------------------------------------------------------
# 1.2 MCP add_correction schema + dispatch (field-level)
# ---------------------------------------------------------------------------


def test_mcp_add_correction_schema_includes_field_correct():
    from goldenmatch.mcp.memory_tools import MEMORY_TOOLS

    tool = next(t for t in MEMORY_TOOLS if t.name == "add_correction")
    enum = tool.inputSchema["properties"]["decision"]["enum"]
    assert "field_correct" in enum
    # Pair-level still supported.
    assert "approve" in enum
    assert "reject" in enum
    # The 3 new optional fields are in the schema.
    props = tool.inputSchema["properties"]
    for field in ("field_name", "original_value", "corrected_value", "cluster_id"):
        assert field in props, f"{field} missing from add_correction schema"


def test_mcp_add_correction_dispatch_field_level(tmp_path: Path):
    from goldenmatch.core.memory.store import MemoryStore
    from goldenmatch.mcp.memory_tools import _dispatch as handle_memory_tool

    db_path = str(tmp_path / "m.db")
    result = handle_memory_tool(
        "add_correction",
        {
            "decision": "field_correct",
            "cluster_id": 42,
            "field_name": "address1",
            "original_value": "1 Elm St",
            "corrected_value": "1 Elm Street, Apt 4B",
            "dataset": "test_dataset",
            "path": db_path,
        },
    )
    assert result["status"] == "ok"
    assert result["cluster_id"] == 42
    assert result["field_name"] == "address1"
    assert result["corrected_value"] == "1 Elm Street, Apt 4B"

    # Round-trip via the store.
    store = MemoryStore(backend="sqlite", path=db_path)
    rows = list(store.get_corrections(dataset="test_dataset"))
    store.close()
    assert len(rows) == 1
    assert rows[0].decision == "field_correct"
    assert rows[0].field_name == "address1"
    assert rows[0].corrected_value == "1 Elm Street, Apt 4B"


def test_mcp_add_correction_dispatch_pair_level_regression(tmp_path: Path):
    """Pair-level path still works (backward compat)."""
    from goldenmatch.mcp.memory_tools import _dispatch as handle_memory_tool

    db_path = str(tmp_path / "m.db")
    result = handle_memory_tool(
        "add_correction",
        {
            "decision": "approve",
            "id_a": 5,
            "id_b": 10,
            "dataset": "test_dataset",
            "path": db_path,
        },
    )
    assert result["status"] == "ok"
    assert result["id_a"] == 5
    assert result["id_b"] == 10
    assert result["decision"] == "approve"


def test_mcp_add_correction_field_correct_missing_field_name(tmp_path: Path):
    from goldenmatch.mcp.memory_tools import _dispatch as handle_memory_tool

    result = handle_memory_tool(
        "add_correction",
        {
            "decision": "field_correct",
            "corrected_value": "X",
            "dataset": "test",
            "path": str(tmp_path / "m.db"),
        },
    )
    assert "error" in result
    assert "field_name" in result["error"]


def test_mcp_add_correction_field_correct_missing_corrected_value(tmp_path: Path):
    from goldenmatch.mcp.memory_tools import _dispatch as handle_memory_tool

    result = handle_memory_tool(
        "add_correction",
        {
            "decision": "field_correct",
            "field_name": "address1",
            "dataset": "test",
            "path": str(tmp_path / "m.db"),
        },
    )
    assert "error" in result
    assert "corrected_value" in result["error"]


# ---------------------------------------------------------------------------
# 1.3 MCP list_plugins tool
# ---------------------------------------------------------------------------


def test_mcp_list_plugins_tool_registered():
    from goldenmatch.mcp.memory_tools import MEMORY_TOOLS

    tool = next((t for t in MEMORY_TOOLS if t.name == "list_plugins"), None)
    assert tool is not None
    assert tool.inputSchema["properties"]["category"]["enum"] == [
        "all", "golden_strategy", "scorer", "transform", "connector",
    ]


def test_mcp_list_plugins_returns_22_builtins(tmp_path: Path):
    from goldenmatch.mcp.memory_tools import _dispatch as handle_memory_tool
    from goldenmatch.plugins.registry import PluginRegistry

    PluginRegistry.reset()
    result = handle_memory_tool("list_plugins", {"category": "golden_strategy"})
    PluginRegistry.reset()
    names = {p["name"] for p in result["golden_strategy"]}
    # Sample of expected builtins from each category:
    for expected in (
        "numeric_max", "numeric_mean", "email_normalize",
        "phone_digits_only", "system_of_record", "lifecycle_stage",
        "agreement_rate", "count_distinct",
    ):
        assert expected in names, f"{expected} not in MCP list_plugins"
    # All builtin entries have source=builtin.
    for entry in result["golden_strategy"]:
        if entry["name"] in {
            "numeric_max", "numeric_mean", "email_normalize",
        }:
            assert entry["source"] == "builtin"


def test_mcp_list_plugins_category_filter(tmp_path: Path):
    from goldenmatch.mcp.memory_tools import _dispatch as handle_memory_tool
    from goldenmatch.plugins.registry import PluginRegistry

    PluginRegistry.reset()
    result = handle_memory_tool("list_plugins", {"category": "scorer"})
    PluginRegistry.reset()
    # Only scorer category present.
    assert set(result.keys()) == {"scorer"}


# ---------------------------------------------------------------------------
# 1.4 CLI `goldenmatch memory add`
# ---------------------------------------------------------------------------


def test_cli_memory_add_pair_level(tmp_path: Path):
    from goldenmatch.cli.memory import memory_app
    from goldenmatch.core.memory.store import MemoryStore

    db_path = str(tmp_path / "m.db")
    runner = CliRunner()
    result = runner.invoke(memory_app, [
        "add",
        "--decision", "approve",
        "--id-a", "42",
        "--id-b", "99",
        "--dataset", "test",
        "--path", db_path,
    ])
    assert result.exit_code == 0, result.output
    assert "added" in result.output
    store = MemoryStore(backend="sqlite", path=db_path)
    rows = list(store.get_corrections(dataset="test"))
    store.close()
    assert len(rows) == 1
    assert rows[0].decision == "approve"
    assert rows[0].id_a == 42
    assert rows[0].id_b == 99


def test_cli_memory_add_field_level(tmp_path: Path):
    from goldenmatch.cli.memory import memory_app
    from goldenmatch.core.memory.store import MemoryStore

    db_path = str(tmp_path / "m.db")
    runner = CliRunner()
    result = runner.invoke(memory_app, [
        "add",
        "--decision", "field_correct",
        "--cluster-id", "42",
        "--field-name", "address1",
        "--corrected-value", "1 Elm Street, Apt 4B",
        "--original-value", "1 Elm St",
        "--dataset", "test",
        "--path", db_path,
    ])
    assert result.exit_code == 0, result.output
    store = MemoryStore(backend="sqlite", path=db_path)
    rows = list(store.get_corrections(dataset="test"))
    store.close()
    assert len(rows) == 1
    assert rows[0].decision == "field_correct"
    assert rows[0].field_name == "address1"
    assert rows[0].corrected_value == "1 Elm Street, Apt 4B"


def test_cli_memory_add_pair_level_missing_id_b(tmp_path: Path):
    from goldenmatch.cli.memory import memory_app

    runner = CliRunner()
    result = runner.invoke(memory_app, [
        "add",
        "--decision", "approve",
        "--id-a", "42",
        "--dataset", "test",
        "--path", str(tmp_path / "m.db"),
    ])
    assert result.exit_code == 1


def test_cli_memory_add_field_correct_missing_corrected_value(tmp_path: Path):
    from goldenmatch.cli.memory import memory_app

    runner = CliRunner()
    result = runner.invoke(memory_app, [
        "add",
        "--decision", "field_correct",
        "--cluster-id", "42",
        "--field-name", "address1",
        "--dataset", "test",
        "--path", str(tmp_path / "m.db"),
    ])
    assert result.exit_code == 1


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-v"])
