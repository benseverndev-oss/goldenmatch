"""Claim-authority tier + claim-lifecycle operations (#1256).

The provenance spine (#1078) captures who/when/why + tamper-evidence. This layer
adds the *authority of the claim*: a categorical ``claim_type`` tier (orthogonal
to numeric ``trust``), a typed ``evidence_ref``, a ``previous_claim_id`` lifecycle
chain, and the promote/amend/revoke operations that make an agent inference
becoming durable shared truth an explicit, auditable event.

These tests lock: the enums, backward-compatible + tamper-evident hashing, store
round-trip, the v4->v5 migration, and the lifecycle helpers.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

import pytest
from goldenmatch.identity import (
    ClaimType,
    EventKind,
    EvidenceRef,
    IdentityStore,
    amend_claim,
    promote_claim,
    revoke_claim,
)
from goldenmatch.identity.audit import (
    _normalize_dt,
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
    s.upsert_identity(IdentityNode(entity_id="ent1"))
    yield s
    s.close()


# ── enums ────────────────────────────────────────────────────────────────────


def test_claim_type_values():
    assert set(ClaimType) == {
        ClaimType.OBSERVATION, ClaimType.INFERENCE,
        ClaimType.VERIFIED, ClaimType.DIRECTIVE,
    }
    assert ClaimType.VERIFIED == "verified"


def test_evidence_ref_values():
    assert EvidenceRef.TOOL_CALL == "tool-call"
    assert EvidenceRef.USER_CONFIRMATION == "user-confirmation"


def test_lifecycle_event_kinds_exist():
    assert EventKind.PROMOTE == "promote"
    assert EventKind.AMEND == "amend"
    assert EventKind.REVOKE == "revoke"


# ── hashing: backward-compatible AND tamper-evident ──────────────────────────


def test_none_claim_event_hashes_identically_to_pre_1256_canon():
    """The killer backward-compat invariant: an event with no claim fields must
    hash EXACTLY as it did before #1256, or every existing seal breaks. The new
    keys are omitted from the canon when None (not always-present like actor)."""
    from datetime import datetime

    dt = datetime(2026, 7, 2, 12, 0, 0)
    ev = IdentityEvent(
        entity_id="e1", kind="created", payload={"x": 1},
        actor="pipeline", trust=0.8, recorded_at=dt,
    )
    pre_1256_canon = {
        "entity_id": "e1", "kind": "created", "payload": {"x": 1},
        "run_name": None, "dataset": None, "actor": "pipeline",
        "trust": 0.8, "recorded_at": _normalize_dt(dt),
    }
    expected = hashlib.sha256(
        json.dumps(pre_1256_canon, sort_keys=True, separators=(",", ":"),
                   default=str).encode("utf-8")
    ).hexdigest()
    assert event_content_hash(ev) == expected


def test_claim_fields_are_tamper_evident():
    from datetime import datetime

    dt = datetime(2026, 7, 2, 12, 0, 0)
    plain = IdentityEvent(entity_id="e1", kind="promote", recorded_at=dt)
    with_claim = IdentityEvent(
        entity_id="e1", kind="promote", claim_type="verified",
        evidence_ref="tool-call", previous_claim_id=7, recorded_at=dt,
    )
    # each set field perturbs the hash
    assert event_content_hash(with_claim) != event_content_hash(plain)
    only_type = IdentityEvent(entity_id="e1", kind="promote",
                              claim_type="verified", recorded_at=dt)
    only_evidence = IdentityEvent(entity_id="e1", kind="promote",
                                  evidence_ref="tool-call", recorded_at=dt)
    assert len({event_content_hash(x) for x in (plain, only_type, only_evidence)}) == 3


# ── store round-trip ─────────────────────────────────────────────────────────


def test_store_round_trips_claim_fields(store):
    eid = store.emit_event(IdentityEvent(
        entity_id="ent1", kind=EventKind.CLAIMED.value,
        claim_type=ClaimType.INFERENCE.value,
        evidence_ref=EvidenceRef.SOURCE.value,
        previous_claim_id=None,
    ))
    ev = store.history("ent1")[0]
    assert ev.event_id == eid
    assert ev.claim_type == "inference"
    assert ev.evidence_ref == "source"
    assert ev.previous_claim_id is None


def test_export_audit_log_carries_claim_fields(store):
    store.emit_event(IdentityEvent(
        entity_id="ent1", kind="claimed",
        claim_type="verified", evidence_ref="test-run",
    ))
    events = store.export_audit_log()
    assert events
    assert events[-1].claim_type == "verified"
    assert events[-1].evidence_ref == "test-run"


# ── migration v4 -> v5 ───────────────────────────────────────────────────────


def test_v4_db_migrates_and_old_rows_read_none(tmp_path):
    """A pre-#1256 (v4) DB without the claim columns is upgraded on open; its
    existing rows read back with claim_type/evidence_ref/previous_claim_id=None,
    and new inserts with claim fields work."""
    db = str(tmp_path / "old.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE identity_nodes (entity_id TEXT PRIMARY KEY, status TEXT,
            merged_into TEXT, golden_record TEXT, confidence REAL, dataset TEXT,
            created_at TIMESTAMP, updated_at TIMESTAMP);
        CREATE TABLE identity_events (
            event_id INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_id TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT,
            run_name TEXT, dataset TEXT, actor TEXT, trust REAL,
            entry_hash TEXT,
            recorded_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP);
        INSERT INTO identity_nodes (entity_id, status) VALUES ('ent1', 'active');
        INSERT INTO identity_events (entity_id, kind) VALUES ('ent1', 'created');
        PRAGMA user_version = 4;
        """
    )
    conn.commit()
    conn.close()

    # Open with the current store -> migration runs.
    s = IdentityStore(backend="sqlite", path=db)
    cols = {r[1] for r in s._conn.execute("PRAGMA table_info(identity_events)")}
    assert {"claim_type", "evidence_ref", "previous_claim_id"} <= cols

    old = s.history("ent1")[0]
    assert old.claim_type is None and old.evidence_ref is None
    assert old.previous_claim_id is None

    # New insert carrying claim fields works on the migrated DB.
    s.emit_event(IdentityEvent(entity_id="ent1", kind="promote",
                               claim_type="verified", evidence_ref="tool-call"))
    promoted = [e for e in s.history("ent1") if e.kind == "promote"][0]
    assert promoted.claim_type == "verified"
    s.close()


# ── lifecycle operations ─────────────────────────────────────────────────────


def test_promote_claim_chains_and_records_authority(store):
    base = store.emit_event(IdentityEvent(
        entity_id="ent1", kind="claimed", claim_type="inference"))
    out = promote_claim(
        store, "ent1", to_claim_type=ClaimType.VERIFIED,
        previous_claim_id=base, evidence_ref=EvidenceRef.TEST_RUN,
        reason="CI passed", actor="agent:x", trust=0.9,
    )
    assert out["operation"] == "promote"
    assert out["claim_type"] == "verified"
    assert out["previous_claim_id"] == base

    ev = [e for e in store.history("ent1") if e.kind == "promote"][0]
    assert ev.claim_type == "verified"
    assert ev.evidence_ref == "test-run"
    assert ev.previous_claim_id == base
    assert ev.actor == "agent:x" and ev.trust == 0.9
    assert ev.payload["reason"] == "CI passed"


def test_amend_claim_supersedes_content(store):
    base = store.emit_event(IdentityEvent(entity_id="ent1", kind="claimed",
                                          claim_type="observation"))
    out = amend_claim(store, "ent1", previous_claim_id=base,
                      claim_type=ClaimType.INFERENCE, payload={"note": "revised"},
                      reason="corrected")
    assert out["operation"] == "amend"
    ev = [e for e in store.history("ent1") if e.kind == "amend"][0]
    assert ev.claim_type == "inference"
    assert ev.previous_claim_id == base
    assert ev.payload["note"] == "revised"


def test_revoke_claim_records_retraction(store):
    base = store.emit_event(IdentityEvent(entity_id="ent1", kind="claimed",
                                          claim_type="inference"))
    out = revoke_claim(store, "ent1", previous_claim_id=base, reason="wrong")
    assert out["operation"] == "revoke"
    ev = [e for e in store.history("ent1") if e.kind == "revoke"][0]
    assert ev.previous_claim_id == base
    assert ev.payload["reason"] == "wrong"
    # the revoked claim is still present (append-only, not deleted)
    assert any(e.event_id == base for e in store.history("ent1"))


def test_lifecycle_rejects_unknown_entity(store):
    with pytest.raises(ValueError, match="not found"):
        promote_claim(store, "does-not-exist", to_claim_type="verified")


def test_lifecycle_rejects_bad_previous_claim_id(store):
    with pytest.raises(ValueError, match="previous_claim_id"):
        promote_claim(store, "ent1", to_claim_type="verified",
                      previous_claim_id=999999)


# ── tamper-evidence still holds with claim events ────────────────────────────


def test_seal_chain_verifies_with_claim_events(store):
    store.emit_event(IdentityEvent(entity_id="ent1", kind="claimed",
                                   claim_type="inference", evidence_ref="source"))
    promote_claim(store, "ent1", to_claim_type="verified",
                  evidence_ref=EvidenceRef.USER_CONFIRMATION)
    seal_audit_log(store, actor="steward:alice")
    assert verify_audit_chain(store).ok is True


def test_tampering_claim_type_breaks_the_seal(store, store_path):
    store.emit_event(IdentityEvent(entity_id="ent1", kind="claimed",
                                   claim_type="inference"))
    seal_audit_log(store)
    store.close()

    # Silently rewrite claim_type from inference -> verified (an inference
    # masquerading as a verified fact) without updating entry_hash.
    conn = sqlite3.connect(store_path)
    conn.execute(
        "UPDATE identity_events SET claim_type='verified' "
        "WHERE claim_type='inference'"
    )
    conn.commit()
    conn.close()

    s2 = IdentityStore(backend="sqlite", path=store_path)
    assert verify_audit_chain(s2).ok is False
    s2.close()
