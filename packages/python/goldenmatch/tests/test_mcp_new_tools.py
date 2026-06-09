"""Coverage for the MCP tool-coverage pass: 11 new tools across the four groups.

Base (server.py):  evaluate, analyze_blocking, compare_clusters, schema_match,
                   lineage, list_runs, rollback
Agent:             sensitivity, incremental
Identity:          identity_show
Memory:            memory_import
"""
from __future__ import annotations

import csv
import json

import pytest

# ── Registration ──────────────────────────────────────────────────────────────


def test_total_tool_count_is_55():
    from goldenmatch.mcp.agent_tools import AGENT_TOOLS
    from goldenmatch.mcp.identity_tools import IDENTITY_TOOLS
    from goldenmatch.mcp.memory_tools import MEMORY_TOOLS
    from goldenmatch.mcp.server import _BASE_TOOLS, TOOLS

    assert len(AGENT_TOOLS) == 17   # +1 certify_recall
    assert len(MEMORY_TOOLS) == 7
    assert len(IDENTITY_TOOLS) == 7
    assert len(_BASE_TOOLS) == 24
    assert len(TOOLS) == 55
    # No duplicate tool names across the whole surface.
    names = [t.name for t in TOOLS]
    assert len(names) == len(set(names))


def test_new_tool_names_registered():
    from goldenmatch.mcp.server import TOOLS

    names = {t.name for t in TOOLS}
    for new in (
        "evaluate", "analyze_blocking", "compare_clusters", "schema_match",
        "lineage", "list_runs", "rollback", "sensitivity", "incremental",
        "identity_show", "memory_import",
    ):
        assert new in names, f"{new} missing from TOOLS"


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def demo_file(tmp_path):
    f = tmp_path / "demo.csv"
    with open(f, "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["name", "email", "zip"])
        w.writerow(["John Smith", "john@test.com", "10001"])
        w.writerow(["Jon Smith", "john@test.com", "10001"])
        w.writerow(["Jane Doe", "jane@test.com", "90210"])
    return str(f)


@pytest.fixture
def simple_config(tmp_path):
    """Explicit exact-email config so create_server skips auto-config.

    Auto-config on a 3-field shape can enable rerank (cross-encoder download),
    which fails in offline CI. An explicit config keeps these tests hermetic.
    """
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "matchkeys:\n"
        "  - name: exact_email\n"
        "    type: exact\n"
        "    fields:\n"
        "      - field: email\n"
        "        transforms: [lowercase, strip]\n"
    )
    return str(cfg)


# ── Base tools on a loaded dataset ────────────────────────────────────────────


def test_evaluate(demo_file, simple_config, tmp_path):
    from goldenmatch.mcp.server import _handle_tool, create_server

    create_server([demo_file], simple_config)
    gt = tmp_path / "gt.csv"
    with open(gt, "w", newline="") as fp:
        w = csv.writer(fp)
        w.writerow(["id_a", "id_b"])
        w.writerow([0, 1])
    result = _handle_tool("evaluate", {"ground_truth_path": str(gt)})
    assert "f1" in result and "precision" in result and "recall" in result


def test_analyze_blocking(demo_file, simple_config):
    from goldenmatch.mcp.server import _handle_tool, create_server

    create_server([demo_file], simple_config)
    result = _handle_tool("analyze_blocking", {"limit": 5})
    assert "suggestions" in result
    assert isinstance(result["suggestions"], list)
    assert "matchkey_columns" in result


def test_lineage(demo_file, simple_config):
    from goldenmatch.mcp.server import _handle_tool, create_server

    create_server([demo_file], simple_config)
    result = _handle_tool("lineage", {"max_pairs": 10})
    assert "count" in result
    assert "lineage" in result


def test_lineage_to_dir(demo_file, simple_config, tmp_path):
    from goldenmatch.mcp.server import _handle_tool, create_server

    create_server([demo_file], simple_config)
    out = tmp_path / "lineage_out"
    out.mkdir()
    result = _handle_tool("lineage", {"max_pairs": 10, "output_dir": str(out)})
    assert "saved_to" in result


# ── Base tools that are file/store based (no loaded dataset needed) ────────────


def test_compare_clusters(tmp_path):
    from goldenmatch.mcp.server import _handle_tool

    a = tmp_path / "a.json"
    b = tmp_path / "b.json"
    a.write_text(json.dumps({"0": {"members": [0, 1, 2]}}))
    b.write_text(json.dumps({"0": {"members": [0, 1]}, "1": {"members": [2]}}))
    result = _handle_tool("compare_clusters", {
        "clusters_a_path": str(a), "clusters_b_path": str(b),
    })
    assert "twi" in result
    assert result["cc1"] == 1
    assert result["cc2"] == 2


def test_schema_match(tmp_path):
    from goldenmatch.mcp.server import _handle_tool

    fa = tmp_path / "a.csv"
    fb = tmp_path / "b.csv"
    fa.write_text("full_name,email\nJohn,j@x.com\n")
    fb.write_text("contact_name,email_address\nJohn,j@x.com\n")
    result = _handle_tool("schema_match", {"file_a": str(fa), "file_b": str(fb)})
    assert "mappings" in result
    assert isinstance(result["mappings"], list)
    assert len(result["mappings"]) >= 1


def test_list_runs_empty(tmp_path):
    from goldenmatch.mcp.server import _handle_tool

    result = _handle_tool("list_runs", {"output_dir": str(tmp_path)})
    assert result["runs"] == []


def test_rollback_missing_run(tmp_path):
    from goldenmatch.mcp.server import _handle_tool

    result = _handle_tool("rollback", {"run_id": "nope", "output_dir": str(tmp_path)})
    assert "error" in result


# ── Agent tools ───────────────────────────────────────────────────────────────


def test_incremental(tmp_path):
    from goldenmatch.core.agent import AgentSession
    from goldenmatch.mcp.agent_tools import _dispatch

    base = tmp_path / "base.csv"
    new = tmp_path / "new.csv"
    base.write_text("name,email\nJohn Smith,john@x.com\nJane Doe,jane@x.com\n")
    new.write_text("name,email\nJohnny Smith,john@x.com\nBob New,bob@x.com\n")

    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "matchkeys:\n"
        "  - name: exact_email\n"
        "    type: exact\n"
        "    fields:\n"
        "      - field: email\n"
        "        transforms: [lowercase, strip]\n"
    )
    result = _dispatch(
        "incremental",
        {"base_file": str(base), "new_records": str(new), "config": str(cfg)},
        AgentSession,
    )
    assert result["matched_to_base"] == 1
    assert result["new_entities"] == 1
    assert any(m["base_row_id"] == 0 for m in result["matches"])


def test_sensitivity_requires_sweep(tmp_path):
    from goldenmatch.core.agent import AgentSession
    from goldenmatch.mcp.agent_tools import _dispatch

    f = tmp_path / "d.csv"
    f.write_text("name,email\nA,a@x.com\n")
    cfg = tmp_path / "c.yaml"
    cfg.write_text(
        "matchkeys:\n"
        "  - name: exact_email\n"
        "    type: exact\n"
        "    fields:\n"
        "      - field: email\n"
    )
    # Empty sweep -> structured error, not an exception.
    result = _dispatch(
        "sensitivity",
        {"file_path": str(f), "sweep": [], "config": str(cfg)},
        AgentSession,
    )
    assert "error" in result

    # Malformed sweep spec -> structured error.
    result2 = _dispatch(
        "sensitivity",
        {"file_path": str(f), "sweep": ["threshold:0.7"], "config": str(cfg)},
        AgentSession,
    )
    assert "error" in result2


# ── Identity ──────────────────────────────────────────────────────────────────


def test_identity_show(tmp_path):
    from goldenmatch.identity import IdentityNode, IdentityStore, SourceRecord, new_entity_id
    from goldenmatch.mcp.identity_tools import _dispatch

    path = str(tmp_path / "identity.db")
    eid = new_entity_id()
    with IdentityStore(path=path) as s:
        s.upsert_identity(IdentityNode(entity_id=eid, dataset="d", confidence=0.9))
        s.upsert_record(SourceRecord("src:1", "src", "1", "h1", entity_id=eid, dataset="d"))

    result = _dispatch("identity_show", {"entity_id": eid, "path": path})
    assert result.get("entity_id") == eid

    missing = _dispatch("identity_show", {"entity_id": "does-not-exist", "path": path})
    assert missing == {"found": False}


# ── Memory ────────────────────────────────────────────────────────────────────


def test_memory_import_round_trips_export(tmp_path):
    from goldenmatch.mcp.memory_tools import _dispatch

    path = str(tmp_path / "memory.db")
    corrections = [
        {
            "id_a": 1, "id_b": 2, "decision": "reject", "source": "agent",
            "trust": 0.5, "dataset": "ds1",
        },
        {
            "id_a": 3, "id_b": 4, "decision": "approve", "source": "steward",
            "trust": 1.0, "dataset": "ds1",
        },
    ]
    imported = _dispatch("memory_import", {"corrections": corrections, "path": path})
    assert imported["imported"] == 2

    exported = _dispatch("memory_export", {"path": path})
    assert exported["count"] == 2
    decisions = {c["decision"] for c in exported["corrections"]}
    assert decisions == {"reject", "approve"}
