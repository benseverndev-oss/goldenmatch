"""DuckDB Identity Graph UDF tests.

Seeds a small identity DB then queries via the registered UDFs to verify
the contract at
``docs/superpowers/specs/2026-05-12-identity-graph-duckdb-contract.md``.
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from goldenmatch.identity import (
    IdentityNode,
    IdentityStore,
    SourceRecord,
    new_entity_id,
)
from goldenmatch.identity.model import EvidenceEdge
from goldenmatch_duckdb.functions import register


@pytest.fixture()
def identity_db(tmp_path: Path) -> tuple[str, dict[str, str]]:
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
            score=0.95, matchkey_name="m", run_name="r1", dataset="d",
        ))
        s.add_edge(EvidenceEdge(
            entity_id=eid2, record_a_id="src:3", record_b_id="src:4",
            kind="conflicts_with", score=0.4, run_name="r1", dataset="d",
        ))
    return path, {"eid1": eid1, "eid2": eid2}


@pytest.fixture()
def con():
    c = duckdb.connect()
    register(c)
    return c


class TestIdentityResolve:
    def test_resolves_record(self, con, identity_db):
        path, ids = identity_db
        row = con.sql(
            "SELECT goldenmatch_identity_resolve(?, ?)",
            params=["src:1", path],
        ).fetchone()
        payload = json.loads(row[0])
        assert payload["entity_id"] == ids["eid1"]
        assert len(payload["records"]) == 2
        assert len(payload["edges"]) == 1

    def test_missing_record_returns_not_found(self, con, identity_db):
        path, _ = identity_db
        row = con.sql(
            "SELECT goldenmatch_identity_resolve(?, ?)",
            params=["src:missing", path],
        ).fetchone()
        assert json.loads(row[0]) == {"found": False}


class TestIdentityView:
    def test_returns_view_by_entity_id(self, con, identity_db):
        path, ids = identity_db
        row = con.sql(
            "SELECT goldenmatch_identity_view(?, ?)",
            params=[ids["eid1"], path],
        ).fetchone()
        payload = json.loads(row[0])
        assert payload["entity_id"] == ids["eid1"]
        assert payload["status"] == "active"

    def test_unknown_entity_returns_not_found(self, con, identity_db):
        path, _ = identity_db
        row = con.sql(
            "SELECT goldenmatch_identity_view(?, ?)",
            params=["missing-uuid", path],
        ).fetchone()
        assert json.loads(row[0]) == {"found": False}


class TestIdentityHistory:
    def test_empty_history_returns_array(self, con, identity_db):
        path, ids = identity_db
        row = con.sql(
            "SELECT goldenmatch_identity_history(?, ?)",
            params=[ids["eid1"], path],
        ).fetchone()
        # No events seeded -> empty array
        assert json.loads(row[0]) == []


class TestIdentityConflicts:
    def test_lists_conflict_edges(self, con, identity_db):
        path, _ = identity_db
        row = con.sql(
            "SELECT goldenmatch_identity_conflicts(?, ?)",
            params=["d", path],
        ).fetchone()
        items = json.loads(row[0])
        assert len(items) == 1
        assert items[0]["record_a_id"] == "src:3"
        # Filter implies kind=conflicts_with so the serializer omits it
        assert items[0]["record_b_id"] == "src:4"

    def test_dataset_filter_empty_string_returns_all(self, con, identity_db):
        path, _ = identity_db
        row = con.sql(
            "SELECT goldenmatch_identity_conflicts(?, ?)",
            params=["", path],
        ).fetchone()
        assert len(json.loads(row[0])) == 1


class TestIdentityList:
    def test_lists_with_filters(self, con, identity_db):
        path, _ = identity_db
        row = con.sql(
            "SELECT goldenmatch_identity_list(?, ?, ?)",
            params=["d", "active", path],
        ).fetchone()
        items = json.loads(row[0])
        assert len(items) == 2
        assert all(i["status"] == "active" for i in items)

    def test_no_filters_returns_all(self, con, identity_db):
        path, _ = identity_db
        row = con.sql(
            "SELECT goldenmatch_identity_list(?, ?, ?)",
            params=["", "", path],
        ).fetchone()
        assert len(json.loads(row[0])) == 2
