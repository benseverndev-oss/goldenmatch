"""Tamper-evident audit log for the Identity Graph (#1078).

The identity event log is append-only, but "append-only by convention" is not
the same as "provably untampered". This module adds cryptographic
tamper-evidence in two contention-free layers:

1. **Per-event content hash** (``event_content_hash``) -- a sha256 over an
   event's own immutable fields, computed at insert time. It's a *pure*
   function (no prior-state read), so it adds no insert-time serialization
   point and works uniformly with the Postgres bulk-COPY write path. It catches
   any after-the-fact edit to a single event's content.

2. **On-demand seal chain** (``seal_audit_log`` / ``verify_audit_chain``) -- a
   periodic anchor that folds every event's content hash, in ``event_id``
   order, into a single chained root (git-/Certificate-Transparency-style).
   Each seal chains to its predecessor, so a verifier replaying the log can
   detect deletion, reordering, insertion, and content edits of any *sealed*
   event. Sealing is an explicit, infrequent operation -- never on the write
   hot path -- so the bulk/streaming ingest path is untouched.

Design note: tamper-evidence here is integrity, not secrecy. Anyone with write
access to the DB can also rewrite the seals; the value is that doing so
consistently across the event rows *and* the seal chain is detectable by an
external party who retains a prior seal root (publish it, mirror it, or store
it out-of-band). That is the same threat model as an append-only ledger.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from goldenmatch.identity.model import AuditSeal, IdentityEvent

if TYPE_CHECKING:
    from goldenmatch.identity.store import IdentityStore


def _normalize_dt(value: Any) -> str:
    """Stable string form of a timestamp, identical on the insert side (a
    ``datetime``) and the read-back side (``_to_dt`` returns a ``datetime``),
    so the content hash round-trips across a store write/read.

    **Dropping the tzinfo is load-bearing for the Postgres backend.** Events are
    created with a naive ``datetime.now()`` and hashed *before* insert, but the
    Postgres schema stores ``recorded_at`` as ``TIMESTAMPTZ`` and hands back a
    tz-AWARE datetime, whose ``isoformat()`` carries a ``+00:00`` offset the
    write-side hash never saw. Every stamped event therefore re-hashed to a
    different value and ``verify_audit_chain`` reported phantom "content
    edit(s)" on a perfectly clean in-DB store (caught by the `rust_pgrx`
    `gm_identity_audit_verify` smoke). Keeping the wall clock exactly as the
    driver returned it -- in the session timezone the naive value was written in
    -- makes the read-back string identical to the written one.

    Naive values (every SQLite event, and the write side on both backends) are
    untouched, so existing entry hashes and the seals chained over them stay
    valid -- the same back-compat constraint that governs the claim-authority
    fields in ``event_content_hash`` below.
    """
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.replace(tzinfo=None)
        return value.isoformat()
    return str(value)


def event_content_hash(event: IdentityEvent) -> str:
    """sha256 over an event's immutable content fields (everything that
    identifies *what happened*, excluding the DB-assigned ``event_id`` and the
    ``entry_hash`` itself). Deterministic across SQLite/Postgres/Mongo and
    across a write->read round-trip: ``recorded_at`` is normalized to ISO-8601,
    ``trust`` rounded, ``payload`` canonicalized via sorted-key JSON."""
    canon = {
        "entity_id": event.entity_id,
        "kind": str(event.kind),
        "payload": event.payload,
        "run_name": event.run_name,
        "dataset": event.dataset,
        "actor": event.actor,
        "trust": round(event.trust, 6) if event.trust is not None else None,
        "recorded_at": _normalize_dt(event.recorded_at),
    }
    # Claim-authority fields (#1256) are hashed only when SET, not always-None
    # like actor/trust. Adding an always-present ``"claim_type": null`` key would
    # change every pre-#1256 event's hash and break existing seals; omitting the
    # keys when None keeps old (all-None) events byte-identical while still making
    # a set claim_type/evidence_ref/previous_claim_id tamper-evident (adding or
    # removing the key changes the blob).
    if event.claim_type is not None:
        canon["claim_type"] = str(event.claim_type)
    if event.evidence_ref is not None:
        canon["evidence_ref"] = str(event.evidence_ref)
    if event.previous_claim_id is not None:
        canon["previous_claim_id"] = int(event.previous_claim_id)
    blob = json.dumps(canon, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _effective_hash(event: IdentityEvent) -> str:
    """The stored ``entry_hash`` if present, else computed on the fly. Lets the
    seal/verify path cover events written before the hash-chain column existed
    (migrated rows read back with ``entry_hash=None``)."""
    return event.entry_hash or event_content_hash(event)


def _fold_step(acc: str, entry_hash: str) -> str:
    """One left-fold step of the chain: ``acc' = sha256(acc || entry_hash)``.
    A left fold means an incremental seal (seeded by the prior root) yields the
    same root as folding the whole history from scratch."""
    return hashlib.sha256((acc + entry_hash).encode("utf-8")).hexdigest()


@dataclass
class AuditVerification:
    """Result of ``verify_audit_chain``. ``ok`` is the single bottom line; the
    lists localize *what* failed for an operator/report."""

    ok: bool
    events_checked: int
    seals_checked: int
    # event_ids whose stored entry_hash != recomputed content hash (content edit)
    content_mismatches: list[int]
    # seal_ids whose replayed root/count != stored (deletion/reorder/insertion)
    seal_mismatches: list[int]
    # seal_ids whose last_event_id no longer exists in the log (sealed event deleted)
    missing_sealed_events: list[int]

    def summary(self) -> str:
        if self.ok:
            return (
                f"audit chain intact: {self.events_checked} events, "
                f"{self.seals_checked} seals verified"
            )
        parts = []
        if self.content_mismatches:
            parts.append(f"{len(self.content_mismatches)} content edit(s)")
        if self.seal_mismatches:
            parts.append(f"{len(self.seal_mismatches)} seal mismatch(es)")
        if self.missing_sealed_events:
            parts.append(
                f"{len(self.missing_sealed_events)} sealed event(s) missing"
            )
        return "audit chain BROKEN: " + ", ".join(parts)

    def as_dict(self) -> dict[str, Any]:
        """JSON-ready verdict, the single serialization the MCP tool, the REST
        surface, and the SQL ``gm_identity_audit_verify`` bridge all emit."""
        return {
            "ok": self.ok,
            "events_checked": self.events_checked,
            "seals_checked": self.seals_checked,
            "content_mismatches": self.content_mismatches,
            "seal_mismatches": self.seal_mismatches,
            "missing_sealed_events": self.missing_sealed_events,
            "summary": self.summary(),
        }


def seal_result_dict(seal: AuditSeal | None) -> dict[str, Any]:
    """JSON-ready result for a ``seal_audit_log`` call. ``None`` (nothing new to
    seal) → ``{"sealed": False, ...}``; a fresh seal → its anchor fields. Single
    source for the MCP ``identity_audit_seal`` tool and the SQL
    ``gm_identity_audit_seal`` bridge."""
    if seal is None:
        return {"sealed": False, "reason": "no new events to seal"}
    return {
        "sealed": True,
        "seal_id": seal.seal_id,
        "root_hash": seal.root_hash,
        "event_count": seal.event_count,
        "last_event_id": seal.last_event_id,
        "dataset": seal.dataset,
        "actor": seal.actor,
    }


def seal_audit_log(
    store: IdentityStore,
    *,
    actor: str | None = None,
    dataset: str | None = None,
) -> AuditSeal | None:
    """Anchor the current event log with a new seal and return it.

    Chains onto the latest seal for ``dataset`` (``None`` = global chain),
    folding only the events appended since that seal so cost is proportional to
    new events, not the whole history. Returns ``None`` when there is nothing
    new to seal (idempotent: re-sealing an unchanged log is a no-op).
    """
    if getattr(store, "_backend", None) == "mongo":
        raise NotImplementedError(
            "seal_audit_log is not supported on the mongo backend"
        )
    prev = store.latest_seal(dataset=dataset)
    prev_root = prev.root_hash if prev else ""
    prev_last_id = prev.last_event_id if prev and prev.last_event_id is not None else -1
    prev_count = prev.event_count if prev else 0

    new_events = [
        e
        for e in store.export_audit_log(dataset=dataset)
        if e.event_id is not None and e.event_id > prev_last_id
    ]
    if not new_events:
        return None

    acc = prev_root
    for e in new_events:
        acc = _fold_step(acc, _effective_hash(e))

    seal = AuditSeal(
        root_hash=acc,
        event_count=prev_count + len(new_events),
        last_event_id=new_events[-1].event_id,
        dataset=dataset,
        prev_seal_id=prev.seal_id if prev else None,
        prev_root=prev_root or None,
        actor=actor,
    )
    seal_id = store.add_seal(seal)
    seal.seal_id = seal_id
    return seal


def verify_audit_chain(
    store: IdentityStore,
    *,
    dataset: str | None = None,
) -> AuditVerification:
    """Replay the event log against its seal chain and report integrity.

    Two independent checks:
      * **content** -- every event whose ``entry_hash`` was stored must still
        hash to it (catches an in-place edit of an event's fields);
      * **chain** -- replaying the fold over the *current* events must
        reproduce each seal's root and count at its boundary, and every sealed
        ``last_event_id`` must still exist (catches deletion, reordering, and
        insertion of sealed events).
    """
    if getattr(store, "_backend", None) == "mongo":
        raise NotImplementedError(
            "verify_audit_chain is not supported on the mongo backend"
        )
    events = store.export_audit_log(dataset=dataset)
    seals = store.list_seals(dataset=dataset)

    content_mismatches: list[int] = []
    for e in events:
        if e.entry_hash is not None and e.event_id is not None:
            if event_content_hash(e) != e.entry_hash:
                content_mismatches.append(e.event_id)

    # Single forward pass over events, checking each seal at its boundary.
    seal_mismatches: list[int] = []
    missing_sealed_events: list[int] = []
    seals_sorted = sorted(
        seals, key=lambda s: (s.last_event_id if s.last_event_id is not None else -1)
    )
    acc = ""
    seen = 0
    seal_idx = 0
    for e in events:
        acc = _fold_step(acc, _effective_hash(e))
        seen += 1
        while (
            seal_idx < len(seals_sorted)
            and seals_sorted[seal_idx].last_event_id == e.event_id
        ):
            s = seals_sorted[seal_idx]
            if acc != s.root_hash or seen != s.event_count:
                if s.seal_id is not None:
                    seal_mismatches.append(s.seal_id)
            seal_idx += 1
    # Any seal whose boundary event_id was never reached -> the sealed event was
    # deleted (or its id renumbered) out of the log.
    for s in seals_sorted[seal_idx:]:
        if s.seal_id is not None:
            missing_sealed_events.append(s.seal_id)

    ok = not (content_mismatches or seal_mismatches or missing_sealed_events)
    return AuditVerification(
        ok=ok,
        events_checked=len(events),
        seals_checked=len(seals),
        content_mismatches=content_mismatches,
        seal_mismatches=seal_mismatches,
        missing_sealed_events=missing_sealed_events,
    )


def audit_log_page(
    store: IdentityStore,
    *,
    dataset: str | None = None,
    actor: str | None = None,
    limit: int = 500,
) -> dict[str, Any]:
    """JSON-ready page of the append-only audit log for compliance export.

    Returns ``{"items": [...], "total": n}`` where ``items`` is truncated to
    ``limit`` (most recent-order preserved from ``export_audit_log``) and
    ``total`` is the full unfiltered-by-limit count. Single source for the MCP
    ``identity_audit`` tool and the SQL ``gm_identity_audit`` bridge."""
    events = store.export_audit_log(dataset=dataset, actor=actor)
    items = [
        {
            "event_id": e.event_id, "entity_id": e.entity_id, "kind": e.kind,
            "actor": e.actor, "trust": e.trust,
            "claim_type": e.claim_type, "evidence_ref": e.evidence_ref,
            "previous_claim_id": e.previous_claim_id,
            "recorded_at": e.recorded_at.isoformat() if e.recorded_at else None,
            "run_name": e.run_name, "dataset": e.dataset, "payload": e.payload,
        }
        for e in events[:limit]
    ]
    return {"items": items, "total": len(events)}
