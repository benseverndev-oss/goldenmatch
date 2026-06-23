"""Provenance spine (#1075 / #1078): actor + trust on identity writes.

Every event/edge write records WHO made the change (``actor``) and their
``trust``, so the append-only audit log lets a reviewer reconstruct exactly which
actor changed what, when, and why. Backward-compatible: pre-provenance rows and
callers that don't supply provenance read back as None.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

import pytest
from goldenmatch.identity import IdentityStore
from goldenmatch.identity.model import (
    EdgeKind,
    EvidenceEdge,
    IdentityEvent,
    IdentityNode,
)
from goldenmatch.identity.query import manual_merge, manual_split


@pytest.fixture()
def store(tmp_path):
    s = IdentityStore(backend="sqlite", path=str(tmp_path / "id.db"))
    yield s
    s.close()


def _mk_entity(store: IdentityStore, eid: str) -> None:
    store.upsert_identity(IdentityNode(entity_id=eid))


# ── round-trip ────────────────────────────────────────────────────────────────


def test_event_actor_trust_round_trip(store):
    _mk_entity(store, "e1")
    store.emit_event(IdentityEvent(
        entity_id="e1", kind="created",
        actor="agent:claude", trust=0.5, run_name="r1",
    ))
    [ev] = store.history("e1")
    assert ev.actor == "agent:claude"
    assert ev.trust == 0.5


def test_edge_actor_trust_round_trip(store):
    _mk_entity(store, "e1")
    store.add_edge(EvidenceEdge(
        entity_id="e1", record_a_id="s:a", record_b_id="s:b",
        kind=EdgeKind.SAME_AS.value, score=0.9,
        actor="pipeline", trust=0.9, run_name="r1",
    ))
    [edge] = store.edges_for_entity("e1")
    assert edge.actor == "pipeline"
    assert edge.trust == 0.9


def test_provenance_defaults_to_none(store):
    # a writer that doesn't supply actor/trust reads back as None (not an error).
    _mk_entity(store, "e1")
    store.emit_event(IdentityEvent(entity_id="e1", kind="created", run_name="r1"))
    [ev] = store.history("e1")
    assert ev.actor is None and ev.trust is None


# ── manual ops stamp provenance ───────────────────────────────────────────────


def test_manual_merge_stamps_actor_trust(store):
    _mk_entity(store, "keep")
    _mk_entity(store, "absorb")
    manual_merge(store, "keep", "absorb", reason="dup",
                 actor="steward:alice", trust=1.0)
    # both sides of the merge carry the provenance
    keep_ev = store.history("keep")[-1]
    absorb_ev = store.history("absorb")[-1]
    assert keep_ev.actor == "steward:alice" and keep_ev.trust == 1.0
    assert absorb_ev.actor == "steward:alice" and absorb_ev.trust == 1.0
    # the "why" rides in the payload
    assert keep_ev.payload["reason"] == "dup"


def test_manual_split_stamps_actor_trust(store):
    _mk_entity(store, "e1")
    from goldenmatch.identity.model import SourceRecord
    store.upsert_record(SourceRecord(
        record_id="s:a", source="s", source_pk="a",
        record_hash="h", entity_id="e1",
    ))
    out = manual_split(store, "e1", ["s:a"], actor="agent:bot", trust=0.5)
    new_eid = out["new_entity_id"]
    assert store.history("e1")[-1].actor == "agent:bot"
    assert store.history(new_eid)[-1].trust == 0.5


# ── audit-log export (#1078) ──────────────────────────────────────────────────


def test_export_audit_log_orders_and_filters(store):
    _mk_entity(store, "e1")
    _mk_entity(store, "e2")
    store.emit_event(IdentityEvent(entity_id="e1", kind="created",
                                   actor="pipeline", dataset="d1", run_name="r1"))
    store.emit_event(IdentityEvent(entity_id="e2", kind="created",
                                   actor="agent:x", dataset="d2", run_name="r1"))
    store.emit_event(IdentityEvent(entity_id="e1", kind="merged_with",
                                   actor="agent:x", dataset="d1", run_name="r2"))

    full = store.export_audit_log()
    assert len(full) == 3
    # commit order (event_id ASC)
    assert [e.event_id for e in full] == sorted(e.event_id for e in full)
    # every event is attributable
    assert all(e.actor for e in full)

    # filter by actor
    by_actor = store.export_audit_log(actor="agent:x")
    assert {e.entity_id for e in by_actor} == {"e1", "e2"}
    assert len(by_actor) == 2

    # filter by dataset
    by_ds = store.export_audit_log(dataset="d1")
    assert all(e.dataset == "d1" for e in by_ds) and len(by_ds) == 2


def test_export_audit_log_since_filter(store):
    _mk_entity(store, "e1")
    old = datetime.now() - timedelta(days=2)
    store.emit_event(IdentityEvent(entity_id="e1", kind="created",
                                   actor="pipeline", recorded_at=old, run_name="r1"))
    store.emit_event(IdentityEvent(entity_id="e1", kind="merged_with",
                                   actor="agent:x", run_name="r2"))
    recent = store.export_audit_log(since=datetime.now() - timedelta(hours=1))
    assert [e.kind for e in recent] == ["merged_with"]


# ── migration from a pre-provenance (v2) database ─────────────────────────────


def test_migration_adds_columns_to_pre_provenance_db(tmp_path):
    """An existing v2 DB (no actor/trust columns) gains them on open, old rows
    read back as None, and new writes carry provenance."""
    db = str(tmp_path / "old.db")
    # hand-build a v2-shape DB: events table WITHOUT actor/trust, user_version=2.
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE identity_nodes (entity_id TEXT PRIMARY KEY, status TEXT,
            merged_into TEXT, golden_record TEXT, confidence REAL, dataset TEXT,
            created_at TIMESTAMP, updated_at TIMESTAMP);
        CREATE TABLE evidence_edges (edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT, record_a_id TEXT, record_b_id TEXT, kind TEXT,
            score REAL, matchkey_name TEXT, field_scores TEXT,
            negative_evidence TEXT, controller_snapshot TEXT, run_name TEXT,
            dataset TEXT, recorded_at TIMESTAMP,
            UNIQUE(entity_id, record_a_id, record_b_id, kind, run_name));
        CREATE TABLE identity_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT, kind TEXT, payload TEXT, run_name TEXT, dataset TEXT,
            recorded_at TIMESTAMP);
        CREATE TABLE identity_aliases (alias TEXT, entity_id TEXT, kind TEXT,
            dataset TEXT, recorded_at TIMESTAMP, PRIMARY KEY (alias, kind, dataset));
        INSERT INTO identity_events (entity_id, kind, run_name, recorded_at)
            VALUES ('e1', 'created', 'r0', '2026-01-01T00:00:00');
        PRAGMA user_version = 2;
        """
    )
    conn.commit()
    conn.close()

    # open through IdentityStore -> migration runs
    s = IdentityStore(backend="sqlite", path=db)
    try:
        # the pre-provenance row reads back with None provenance (not an error)
        [old_ev] = s.history("e1")
        assert old_ev.actor is None and old_ev.trust is None
        # and a new write carries provenance through the migrated schema
        s.emit_event(IdentityEvent(entity_id="e1", kind="merged_with",
                                   actor="steward:bob", trust=1.0, run_name="r1"))
        latest = s.history("e1")[-1]
        assert latest.actor == "steward:bob" and latest.trust == 1.0
    finally:
        s.close()
