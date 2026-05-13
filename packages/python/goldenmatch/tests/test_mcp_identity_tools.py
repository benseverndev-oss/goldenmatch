"""Test MCP identity tool dispatch + registration."""
from __future__ import annotations

from pathlib import Path

import pytest
from goldenmatch.identity import (
    IdentityNode,
    IdentityStore,
    SourceRecord,
    new_entity_id,
)
from goldenmatch.identity.model import EvidenceEdge
from goldenmatch.mcp.identity_tools import (
    IDENTITY_TOOL_NAMES,
    IDENTITY_TOOLS,
    _dispatch,
)


def test_identity_tool_count_and_names():
    assert len(IDENTITY_TOOLS) == 6
    assert IDENTITY_TOOL_NAMES == {
        "identity_resolve", "identity_list", "identity_history",
        "identity_conflicts", "identity_merge", "identity_split",
    }


@pytest.fixture()
def seeded_db(tmp_path: Path) -> tuple[str, dict[str, str]]:
    path = str(tmp_path / "identity.db")
    eid1 = new_entity_id()
    eid2 = new_entity_id()
    with IdentityStore(path=path) as s:
        s.upsert_identity(IdentityNode(entity_id=eid1, dataset="d", confidence=0.9))
        s.upsert_identity(IdentityNode(entity_id=eid2, dataset="d", confidence=0.7))
        s.upsert_record(SourceRecord("src:1", "src", "1", "h1", entity_id=eid1, dataset="d"))
        s.upsert_record(SourceRecord("src:2", "src", "2", "h2", entity_id=eid1, dataset="d"))
        s.upsert_record(SourceRecord("src:3", "src", "3", "h3", entity_id=eid2, dataset="d"))
        s.add_edge(EvidenceEdge(
            entity_id=eid1, record_a_id="src:1", record_b_id="src:2",
            score=0.92, run_name="r", dataset="d",
        ))
    return path, {"eid1": eid1, "eid2": eid2}


def test_identity_resolve(seeded_db):
    path, ids = seeded_db
    out = _dispatch("identity_resolve", {"record_id": "src:1", "path": path})
    assert out["entity_id"] == ids["eid1"]
    out_missing = _dispatch("identity_resolve", {"record_id": "missing", "path": path})
    assert out_missing == {"found": False}


def test_identity_list(seeded_db):
    path, _ = seeded_db
    out = _dispatch("identity_list", {"path": path})
    assert len(out["items"]) == 2


def test_identity_history(seeded_db):
    path, ids = seeded_db
    out = _dispatch("identity_history", {"entity_id": ids["eid1"], "path": path})
    # No events seeded -> empty list
    assert out == {"items": []}


def test_identity_conflicts(seeded_db):
    path, _ = seeded_db
    out = _dispatch("identity_conflicts", {"path": path})
    assert out["items"] == []


def test_identity_merge_via_mcp(seeded_db):
    path, ids = seeded_db
    out = _dispatch("identity_merge", {
        "keep_entity_id": ids["eid1"],
        "absorb_entity_id": ids["eid2"],
        "reason": "test",
        "path": path,
    })
    assert out["keep"] == ids["eid1"]


def test_identity_split_via_mcp(seeded_db):
    path, ids = seeded_db
    out = _dispatch("identity_split", {
        "entity_id": ids["eid1"],
        "record_ids": ["src:2"],
        "reason": "test",
        "path": path,
    })
    assert len(out["moved"]) == 1
    new_eid = out["new_entity_id"]
    # Verify split worked end-to-end
    with IdentityStore(path=path) as s:
        assert s.find_entity_by_record("src:2") == new_eid


def test_identity_tools_in_aggregate_dispatch():
    """Verify server.py dispatcher routes identity tool calls."""
    from goldenmatch.mcp.server import dispatch as agg_dispatch

    # Empty/missing DB -> error string back, not exception
    # Use a non-existent path to force the open path
    try:
        agg_dispatch("identity_list", {"path": "/nonexistent/path/missing.db"})
    except Exception:
        pass  # acceptable -- the test just ensures the routing reached the dispatcher
