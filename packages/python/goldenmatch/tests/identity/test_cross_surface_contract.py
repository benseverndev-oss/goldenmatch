"""Cross-surface identity contract test.

One seeded fixture flowing through:

  - Python ``goldenmatch.identity.query.*``
  - REST router ``/api/v1/identities/...``
  - MCP tool ``identity_*``
  - A2A skill (reuses MCP dispatch by design -- still asserted)
  - DuckDB UDF ``goldenmatch_identity_*``

…with the contract:

  Same input -> same JSON payload across every surface.

pgrx is not exercised here -- it can't run on Windows and would need a
live Postgres. The ``rust_pgrx`` CI lane has a parallel smoke check.

This test is the load-bearing guardrail against contract drift. If a
surface gains a new key or changes a name, it must be reflected
everywhere or this test fails.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from goldenmatch.identity import (
    IdentityNode,
    IdentityStore,
    SourceRecord,
    find_by_record,
    find_conflicts,
    get_entity,
    history,
    list_entities,
    new_entity_id,
)
from goldenmatch.identity.model import EdgeKind, EventKind, EvidenceEdge, IdentityEvent
from goldenmatch.mcp.identity_tools import _dispatch as _mcp_dispatch
from goldenmatch.web.app import create_app
from goldenmatch.web.state import AppState

FIXTURES = Path(__file__).parent.parent / "web" / "fixtures" / "sample_project"


@pytest.fixture()
def seeded_store(tmp_path: Path) -> tuple[Path, str, dict[str, str]]:
    """Return (project_root, db_path, {eid1, eid2}) with a small graph seeded.

    Two identities, four records, one same_as edge, one conflicts_with edge,
    two events. Designed to make every result-shape key non-trivial.
    """
    import shutil

    project = tmp_path / "project"
    shutil.copytree(FIXTURES, project)
    db_dir = project / ".goldenmatch"
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(db_dir / "identity.db")

    eid1 = new_entity_id()
    eid2 = new_entity_id()

    with IdentityStore(path=db_path) as s:
        s.upsert_identity(IdentityNode(
            entity_id=eid1, dataset="contract", confidence=0.93,
            golden_record={"name": "Alice Smith", "email": "a@x.com"},
        ))
        s.upsert_identity(IdentityNode(
            entity_id=eid2, dataset="contract", confidence=0.71,
        ))
        s.upsert_record(SourceRecord(
            record_id="crm:1", source="crm", source_pk="1",
            record_hash="hA", entity_id=eid1, dataset="contract",
            payload={"name": "Alice Smith", "email": "a@x.com"},
        ))
        s.upsert_record(SourceRecord(
            record_id="crm:2", source="crm", source_pk="2",
            record_hash="hB", entity_id=eid1, dataset="contract",
            payload={"name": "Alyce Smith", "email": "a@x.com"},
        ))
        s.upsert_record(SourceRecord(
            record_id="erp:9", source="erp", source_pk="9",
            record_hash="hC", entity_id=eid2, dataset="contract",
        ))
        s.add_edge(EvidenceEdge(
            entity_id=eid1, record_a_id="crm:1", record_b_id="crm:2",
            kind=EdgeKind.SAME_AS.value, score=0.95,
            matchkey_name="weighted_default", run_name="r1",
            field_scores={"name": 0.92, "email": 1.0},
            dataset="contract",
        ))
        s.add_edge(EvidenceEdge(
            entity_id=eid2, record_a_id="erp:9", record_b_id="erp:404",
            kind=EdgeKind.CONFLICTS_WITH.value, score=0.42,
            matchkey_name="weighted_default", run_name="r1",
            dataset="contract",
        ))
        s.emit_event(IdentityEvent(
            entity_id=eid1, kind=EventKind.CREATED.value,
            payload={"cluster_id": 0}, run_name="r1", dataset="contract",
        ))
        s.emit_event(IdentityEvent(
            entity_id=eid1, kind=EventKind.ABSORBED_RECORD.value,
            payload={"record_id": "crm:2"}, run_name="r1", dataset="contract",
        ))

    return project, db_path, {"eid1": eid1, "eid2": eid2}


@pytest.fixture()
def client_for(seeded_store) -> tuple[TestClient, str, dict[str, str]]:
    project, db_path, ids = seeded_store
    app = create_app(AppState.from_project_dir(project))
    return TestClient(app), db_path, ids


# ── Helpers ─────────────────────────────────────────────────────────────


# Keys that vary by timestamp / autoincrement and should not be compared
# byte-equal across surfaces. We assert their TYPE only (str / int).
_VOLATILE_VIEW_KEYS = frozenset({
    "created_at", "updated_at",
    # nested:
    "first_seen_at", "last_seen_at", "recorded_at", "edge_id", "event_id",
})


def _strip_volatile(obj):
    if isinstance(obj, dict):
        return {k: _strip_volatile(v) for k, v in obj.items() if k not in _VOLATILE_VIEW_KEYS}
    if isinstance(obj, list):
        return [_strip_volatile(x) for x in obj]
    return obj


def _python_view(db_path: str, record_id: str) -> dict:
    with IdentityStore(path=db_path) as s:
        view = find_by_record(s, record_id)
    assert view is not None
    return view.to_dict()


def _rest_view(client: TestClient, record_id: str) -> dict:
    r = client.get(f"/api/v1/identities/by-record/{record_id}")
    assert r.status_code == 200
    return r.json()


def _mcp_view(db_path: str, record_id: str) -> dict:
    return _mcp_dispatch("identity_resolve", {"record_id": record_id, "path": db_path})


def _a2a_view(db_path: str, record_id: str) -> dict:
    # A2A intentionally shares the MCP dispatcher per goldenmatch/a2a/skills.py.
    # We still call it through the skill entry point so any future divergence
    # (parameter rename, error wrapping) is caught here.
    from goldenmatch.a2a.skills import dispatch_skill
    return dispatch_skill("identity_resolve", {"record_id": record_id, "path": db_path})


def _duckdb_view(db_path: str, record_id: str) -> dict:
    import duckdb
    from goldenmatch_duckdb.functions import register

    con = duckdb.connect()
    register(con)
    row = con.sql(
        "SELECT goldenmatch_identity_resolve(?, ?)",
        params=[record_id, db_path],
    ).fetchone()
    import json
    return json.loads(row[0])


# ── The contract ────────────────────────────────────────────────────────


class TestResolveContract:
    """`{Python, REST, MCP, A2A, DuckDB}.identity_resolve` -> identical JSON."""

    def test_record_to_view_shape_is_identical(self, client_for, seeded_store):
        client, db_path, ids = client_for
        _, _, _ = seeded_store

        py = _strip_volatile(_python_view(db_path, "crm:1"))
        rest = _strip_volatile(_rest_view(client, "crm:1"))
        mcp = _strip_volatile(_mcp_view(db_path, "crm:1"))
        a2a = _strip_volatile(_a2a_view(db_path, "crm:1"))
        duck = _strip_volatile(_duckdb_view(db_path, "crm:1"))

        # Build {surface: keys} for diagnostic output when assertions fail.
        for name, blob in [
            ("python", py), ("rest", rest), ("mcp", mcp), ("a2a", a2a), ("duckdb", duck),
        ]:
            assert blob["entity_id"] == ids["eid1"], f"{name} entity_id mismatch"

        # Top-level key set must match exactly.
        py_keys = set(py.keys())
        for name, other in [("rest", rest), ("mcp", mcp), ("a2a", a2a), ("duckdb", duck)]:
            other_keys = set(other.keys())
            assert py_keys == other_keys, (
                f"{name} key set differs from python: "
                f"missing={py_keys - other_keys} extra={other_keys - py_keys}"
            )

        # Full deep-equal on volatile-stripped payloads.
        assert py == rest, "REST diverged from Python"
        assert py == mcp, "MCP diverged from Python"
        assert py == a2a, "A2A diverged from Python"
        assert py == duck, "DuckDB diverged from Python"

    def test_record_count_consistent(self, client_for, seeded_store):
        """All five surfaces agree on how many records belong to the entity."""
        client, db_path, ids = client_for

        py_records = _python_view(db_path, "crm:1")["records"]
        rest_records = _rest_view(client, "crm:1")["records"]
        mcp_records = _mcp_view(db_path, "crm:1")["records"]
        a2a_records = _a2a_view(db_path, "crm:1")["records"]
        duck_records = _duckdb_view(db_path, "crm:1")["records"]

        counts = {
            "python": len(py_records),
            "rest": len(rest_records),
            "mcp": len(mcp_records),
            "a2a": len(a2a_records),
            "duckdb": len(duck_records),
        }
        assert len(set(counts.values())) == 1, f"record counts diverged: {counts}"

    def test_edge_field_scores_preserved(self, client_for, seeded_store):
        """field_scores is a JSON object that has bitten serializers before."""
        client, db_path, _ = client_for

        for name, fetch in [
            ("python", lambda: _python_view(db_path, "crm:1")),
            ("rest", lambda: _rest_view(client, "crm:1")),
            ("mcp", lambda: _mcp_view(db_path, "crm:1")),
            ("a2a", lambda: _a2a_view(db_path, "crm:1")),
            ("duckdb", lambda: _duckdb_view(db_path, "crm:1")),
        ]:
            edges = fetch()["edges"]
            assert len(edges) == 1, f"{name}: expected 1 edge"
            assert edges[0]["field_scores"] == {"name": 0.92, "email": 1.0}, (
                f"{name} field_scores serialization drift"
            )


class TestHistoryContract:
    """history(entity_id) -> identical event arrays."""

    def test_event_log_shape_is_identical(self, client_for, seeded_store):
        client, db_path, ids = client_for

        def py():
            with IdentityStore(path=db_path) as s:
                return history(s, ids["eid1"])
        rest = client.get(f"/api/v1/identities/{ids['eid1']}/history").json()["items"]
        mcp = _mcp_dispatch("identity_history",
                            {"entity_id": ids["eid1"], "path": db_path})["items"]
        from goldenmatch.a2a.skills import dispatch_skill
        a2a = dispatch_skill("identity_history",
                             {"entity_id": ids["eid1"], "path": db_path})["items"]

        import duckdb, json as _json
        from goldenmatch_duckdb.functions import register
        con = duckdb.connect(); register(con)
        duck = _json.loads(con.sql(
            "SELECT goldenmatch_identity_history(?, ?)",
            params=[ids["eid1"], db_path],
        ).fetchone()[0])

        py_events = _strip_volatile(py())
        rest = _strip_volatile(rest)
        mcp = _strip_volatile(mcp)
        a2a = _strip_volatile(a2a)
        duck = _strip_volatile(duck)

        assert py_events == rest, "REST history diverged"
        assert py_events == mcp, "MCP history diverged"
        assert py_events == a2a, "A2A history diverged"
        assert py_events == duck, "DuckDB history diverged"


class TestConflictsContract:
    """find_conflicts(dataset) -> identical edge arrays across surfaces."""

    def test_conflicts_shape_is_identical(self, client_for, seeded_store):
        client, db_path, _ = client_for

        with IdentityStore(path=db_path) as s:
            py_conflicts = find_conflicts(s, dataset="contract")

        rest = client.get("/api/v1/identities/conflicts?dataset=contract").json()["items"]
        mcp = _mcp_dispatch("identity_conflicts",
                            {"dataset": "contract", "path": db_path})["items"]
        from goldenmatch.a2a.skills import dispatch_skill
        a2a = dispatch_skill("identity_conflicts",
                             {"dataset": "contract", "path": db_path})["items"]

        import duckdb, json as _json
        from goldenmatch_duckdb.functions import register
        con = duckdb.connect(); register(con)
        duck = _json.loads(con.sql(
            "SELECT goldenmatch_identity_conflicts(?, ?)",
            params=["contract", db_path],
        ).fetchone()[0])

        py_stripped = _strip_volatile(py_conflicts)
        assert py_stripped == _strip_volatile(rest), "REST conflicts diverged"
        assert py_stripped == _strip_volatile(mcp), "MCP conflicts diverged"
        assert py_stripped == _strip_volatile(a2a), "A2A conflicts diverged"
        assert py_stripped == _strip_volatile(duck), "DuckDB conflicts diverged"


class TestListContract:
    """list_entities(dataset) -> identical identity summaries across surfaces."""

    def test_list_shape_is_identical(self, client_for, seeded_store):
        client, db_path, _ = client_for

        with IdentityStore(path=db_path) as s:
            py_items = list_entities(s, dataset="contract")
        rest = client.get("/api/v1/identities?dataset=contract").json()["items"]
        mcp = _mcp_dispatch("identity_list",
                            {"dataset": "contract", "path": db_path})["items"]
        from goldenmatch.a2a.skills import dispatch_skill
        a2a = dispatch_skill("identity_list",
                             {"dataset": "contract", "path": db_path})["items"]

        import duckdb, json as _json
        from goldenmatch_duckdb.functions import register
        con = duckdb.connect(); register(con)
        duck = _json.loads(con.sql(
            "SELECT goldenmatch_identity_list(?, ?, ?)",
            params=["contract", "", db_path],
        ).fetchone()[0])

        py_stripped = _strip_volatile(py_items)
        assert py_stripped == _strip_volatile(rest), "REST list diverged"
        assert py_stripped == _strip_volatile(mcp), "MCP list diverged"
        assert py_stripped == _strip_volatile(a2a), "A2A list diverged"
        assert py_stripped == _strip_volatile(duck), "DuckDB list diverged"


def test_missing_record_consistent(client_for, seeded_store):
    """``record_id='missing'`` -> the same not-found contract on every surface."""
    client, db_path, _ = client_for

    rest_resp = client.get("/api/v1/identities/by-record/totally-missing")
    assert rest_resp.status_code == 404
    assert "No identity for record" in rest_resp.json()["detail"]

    # Python returns None; MCP/A2A/DuckDB return {"found": False}.
    with IdentityStore(path=db_path) as s:
        assert find_by_record(s, "totally-missing") is None

    mcp = _mcp_dispatch("identity_resolve",
                        {"record_id": "totally-missing", "path": db_path})
    assert mcp == {"found": False}

    from goldenmatch.a2a.skills import dispatch_skill
    a2a = dispatch_skill("identity_resolve",
                         {"record_id": "totally-missing", "path": db_path})
    assert a2a == {"found": False}

    import duckdb, json as _json
    from goldenmatch_duckdb.functions import register
    con = duckdb.connect(); register(con)
    duck = _json.loads(con.sql(
        "SELECT goldenmatch_identity_resolve(?, ?)",
        params=["totally-missing", db_path],
    ).fetchone()[0])
    assert duck == {"found": False}
