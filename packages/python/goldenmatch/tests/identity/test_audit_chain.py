"""Tamper-evident audit log (#1078): per-event content hash + seal chain.

The identity event log is append-only, but "append-only by convention" is not
provable. These tests lock the two integrity layers:

  * ``event_content_hash`` is stamped at insert and round-trips through the DB;
  * ``seal_audit_log`` / ``verify_audit_chain`` detect content edits, deletion,
    reordering, and insertion of sealed events.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import pytest
from goldenmatch.identity import IdentityStore
from goldenmatch.identity.audit import (
    event_content_hash,
    seal_audit_log,
    verify_audit_chain,
)
from goldenmatch.identity.model import IdentityEvent, IdentityNode


@pytest.fixture()
def store_path(tmp_path):
    return str(tmp_path / "id.db")


@pytest.fixture()
def store(store_path):
    s = IdentityStore(backend="sqlite", path=store_path)
    yield s
    s.close()


def _mk_entity(store: IdentityStore, eid: str) -> None:
    store.upsert_identity(IdentityNode(entity_id=eid))


def _emit(store: IdentityStore, eid: str, kind: str, **kw) -> None:
    store.emit_event(IdentityEvent(entity_id=eid, kind=kind, run_name="r", **kw))


# ── per-event content hash ────────────────────────────────────────────────────


def test_entry_hash_stamped_and_round_trips(store):
    _mk_entity(store, "e1")
    store.emit_event(IdentityEvent(
        entity_id="e1", kind="created", actor="agent:x", trust=0.5,
        payload={"reason": "init"}, run_name="r1",
    ))
    [ev] = store.history("e1")
    assert ev.entry_hash is not None
    # the stored hash equals a fresh recompute of the read-back event
    assert ev.entry_hash == event_content_hash(ev)


def test_content_hash_is_deterministic_and_field_sensitive():
    a = IdentityEvent(entity_id="e1", kind="created", actor="x", trust=0.5)
    b = IdentityEvent(entity_id="e1", kind="created", actor="x", trust=0.5,
                      recorded_at=a.recorded_at)
    assert event_content_hash(a) == event_content_hash(b)
    # any content change flips the hash
    c = IdentityEvent(entity_id="e1", kind="merged_with", actor="x", trust=0.5,
                      recorded_at=a.recorded_at)
    assert event_content_hash(a) != event_content_hash(c)


# ── seal ──────────────────────────────────────────────────────────────────────


def test_seal_creates_root_and_is_idempotent(store):
    _mk_entity(store, "e1")
    _emit(store, "e1", "created")
    _emit(store, "e1", "merged_with")

    seal = seal_audit_log(store, actor="steward:alice")
    assert seal is not None
    assert seal.event_count == 2
    assert seal.root_hash
    assert seal.last_event_id is not None

    # nothing new -> no-op
    assert seal_audit_log(store) is None

    # a new event -> a new chained seal
    _emit(store, "e1", "retired")
    seal2 = seal_audit_log(store)
    assert seal2 is not None
    assert seal2.event_count == 3
    assert seal2.prev_seal_id == seal.seal_id
    assert seal2.prev_root == seal.root_hash
    assert seal2.root_hash != seal.root_hash


def test_verify_clean_log_ok(store):
    _mk_entity(store, "e1")
    for k in ("created", "merged_with", "retired"):
        _emit(store, "e1", k)
    seal_audit_log(store)
    res = verify_audit_chain(store)
    assert res.ok is True
    assert res.events_checked == 3
    assert res.seals_checked == 1
    assert "intact" in res.summary()


def test_verify_unsealed_log_ok(store):
    # content hashes alone verify clean even before any seal exists.
    _mk_entity(store, "e1")
    _emit(store, "e1", "created")
    res = verify_audit_chain(store)
    assert res.ok is True
    assert res.seals_checked == 0


# ── tamper detection ──────────────────────────────────────────────────────────


def test_detects_content_edit(store, store_path):
    _mk_entity(store, "e1")
    store.emit_event(IdentityEvent(entity_id="e1", kind="created",
                                   payload={"reason": "ok"}, run_name="r1"))
    seal_audit_log(store)
    [ev] = store.history("e1")
    store.close()

    # edit the payload in place WITHOUT touching entry_hash (naive tamper)
    raw = sqlite3.connect(store_path)
    raw.execute(
        "UPDATE identity_events SET payload = ? WHERE event_id = ?",
        ('{"reason": "tampered"}', ev.event_id),
    )
    raw.commit()
    raw.close()

    s2 = IdentityStore(backend="sqlite", path=store_path)
    try:
        res = verify_audit_chain(s2)
        assert res.ok is False
        assert ev.event_id in res.content_mismatches
        assert "BROKEN" in res.summary()
    finally:
        s2.close()


def test_detects_content_edit_with_rehashed_entry(store, store_path):
    # sophisticated tamper: edit payload AND recompute entry_hash to match.
    # the content check passes, but the seal-chain replay no longer reproduces
    # the sealed root -> caught by the seal layer.
    _mk_entity(store, "e1")
    store.emit_event(IdentityEvent(entity_id="e1", kind="created",
                                   payload={"reason": "ok"}, run_name="r1"))
    seal_audit_log(store)
    [ev] = store.history("e1")
    store.close()

    forged = IdentityEvent(
        entity_id=ev.entity_id, kind=ev.kind, payload={"reason": "tampered"},
        run_name=ev.run_name, dataset=ev.dataset, actor=ev.actor,
        trust=ev.trust, recorded_at=ev.recorded_at,
    )
    raw = sqlite3.connect(store_path)
    raw.execute(
        "UPDATE identity_events SET payload = ?, entry_hash = ? WHERE event_id = ?",
        ('{"reason": "tampered"}', event_content_hash(forged), ev.event_id),
    )
    raw.commit()
    raw.close()

    s2 = IdentityStore(backend="sqlite", path=store_path)
    try:
        res = verify_audit_chain(s2)
        assert res.ok is False
        assert res.content_mismatches == []  # content check fooled
        assert res.seal_mismatches  # but the chain catches it
    finally:
        s2.close()


def test_detects_deletion_of_sealed_event(store, store_path):
    _mk_entity(store, "e1")
    for k in ("created", "merged_with", "retired"):
        _emit(store, "e1", k)
    seal_audit_log(store)
    events = store.history("e1")
    victim = events[1].event_id  # a non-boundary sealed event
    store.close()

    raw = sqlite3.connect(store_path)
    raw.execute("DELETE FROM identity_events WHERE event_id = ?", (victim,))
    raw.commit()
    raw.close()

    s2 = IdentityStore(backend="sqlite", path=store_path)
    try:
        res = verify_audit_chain(s2)
        assert res.ok is False
        # boundary event still present -> count/root differ at the boundary
        assert res.seal_mismatches
    finally:
        s2.close()


def test_detects_deletion_of_boundary_event(store, store_path):
    _mk_entity(store, "e1")
    for k in ("created", "merged_with", "retired"):
        _emit(store, "e1", k)
    seal = seal_audit_log(store)
    last = seal.last_event_id
    store.close()

    raw = sqlite3.connect(store_path)
    raw.execute("DELETE FROM identity_events WHERE event_id = ?", (last,))
    raw.commit()
    raw.close()

    s2 = IdentityStore(backend="sqlite", path=store_path)
    try:
        res = verify_audit_chain(s2)
        assert res.ok is False
        # the seal's boundary event_id no longer exists in the log
        assert seal.seal_id in res.missing_sealed_events
    finally:
        s2.close()


# ── dataset-scoped chains ─────────────────────────────────────────────────────


def test_dataset_scoped_seal_chain(store):
    _mk_entity(store, "e1")
    _mk_entity(store, "e2")
    _emit(store, "e1", "created", dataset="d1")
    _emit(store, "e2", "created", dataset="d2")

    s1 = seal_audit_log(store, dataset="d1")
    assert s1 is not None and s1.event_count == 1 and s1.dataset == "d1"

    res = verify_audit_chain(store, dataset="d1")
    assert res.ok is True and res.events_checked == 1 and res.seals_checked == 1
    # d2 has no seal yet but still verifies clean on content hashes
    assert verify_audit_chain(store, dataset="d2").ok is True


# ── migration: pre-hash-chain (v3) events seal via on-the-fly hashing ─────────


def test_pre_hashchain_events_seal_and_verify(tmp_path):
    """A v3 DB (no entry_hash column / audit_seals table) gains them on open;
    old rows keep entry_hash=NULL but are hashed on the fly so they can be
    sealed and verified."""
    db = str(tmp_path / "old.db")
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
            dataset TEXT, actor TEXT, trust REAL, recorded_at TIMESTAMP,
            UNIQUE(entity_id, record_a_id, record_b_id, kind, run_name));
        CREATE TABLE identity_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT, kind TEXT, payload TEXT, run_name TEXT, dataset TEXT,
            actor TEXT, trust REAL, recorded_at TIMESTAMP);
        CREATE TABLE identity_aliases (alias TEXT, entity_id TEXT, kind TEXT,
            dataset TEXT, recorded_at TIMESTAMP, PRIMARY KEY (alias, kind, dataset));
        INSERT INTO identity_events (entity_id, kind, run_name, recorded_at)
            VALUES ('e1', 'created', 'r0', '2026-01-01T00:00:00');
        PRAGMA user_version = 3;
        """
    )
    conn.commit()
    conn.close()

    s = IdentityStore(backend="sqlite", path=db)
    try:
        # old row read back with no stored hash
        [old] = s.history("e1")
        assert old.entry_hash is None
        # a new write carries a hash through the migrated schema
        s.emit_event(IdentityEvent(entity_id="e1", kind="merged_with",
                                   run_name="r1"))
        seal = seal_audit_log(s)
        assert seal is not None and seal.event_count == 2
        res = verify_audit_chain(s)
        assert res.ok is True
    finally:
        s.close()


# ── timezone normalization (Postgres TIMESTAMPTZ round-trip) ──────────────────


def test_content_hash_ignores_tzinfo_for_the_same_wall_clock():
    """A tz-AWARE ``recorded_at`` must hash identically to the naive value that
    was actually written.

    Events are hashed *before* insert from a naive ``datetime.now()``, but the
    Postgres schema stores ``recorded_at`` as ``TIMESTAMPTZ`` and reads back a
    tz-aware datetime. Without normalization every stamped event re-hashed to a
    different value and ``verify_audit_chain`` reported phantom "content
    edit(s)" on a clean in-DB store -- the `rust_pgrx`
    ``gm_identity_audit_verify`` smoke caught exactly this.
    """
    naive = datetime(2026, 7, 24, 13, 22, 25, 55123)
    aware = naive.replace(tzinfo=timezone.utc)

    def _ev(recorded_at):
        return IdentityEvent(
            entity_id="e1", kind="created", payload={"reason": "init"},
            run_name="r1", dataset="people", actor="agent:x", trust=0.5,
            recorded_at=recorded_at,
        )

    assert event_content_hash(_ev(naive)) == event_content_hash(_ev(aware))


def test_naive_content_hash_is_pinned_so_existing_seals_stay_valid():
    """Back-compat lock: the naive-datetime hash must not move. Any change to
    ``event_content_hash`` / ``_normalize_dt`` that shifts this value silently
    invalidates every seal chained over already-stored events."""
    ev = IdentityEvent(
        entity_id="e1", kind="created", payload={"reason": "init"},
        run_name="r1", dataset="people", actor="agent:x", trust=0.5,
        recorded_at=datetime(2026, 7, 24, 13, 22, 25, 55123),
    )
    assert event_content_hash(ev) == (
        "fbb6f4093d6a5e943a07c96c0e2a8f93eafaf97d4a1ddbec66ed0792c8c2292a"
    )
