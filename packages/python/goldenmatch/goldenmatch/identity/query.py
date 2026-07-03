"""Read-side API for the Identity Graph.

These helpers wrap ``IdentityStore`` and return rich result objects suitable
for the CLI, REST routers, MCP tools, and the web frontend.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from goldenmatch.identity.model import (
    ClaimType,
    EdgeKind,
    EventKind,
    EvidenceEdge,
    EvidenceRef,
    IdentityEvent,
    IdentityNode,
    IdentityStatus,
    SourceRecord,
)
from goldenmatch.identity.store import IdentityStore


@dataclass
class IdentityView:
    """Aggregated read of one identity + its members + recent events."""
    node: IdentityNode
    records: list[SourceRecord]
    edges: list[EvidenceEdge]
    events: list[IdentityEvent]

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.node.entity_id,
            "status": self.node.status,
            "merged_into": self.node.merged_into,
            "golden_record": self.node.golden_record,
            "confidence": self.node.confidence,
            "dataset": self.node.dataset,
            "created_at": self.node.created_at.isoformat(),
            "updated_at": self.node.updated_at.isoformat(),
            "records": [
                {
                    "record_id": r.record_id,
                    "source": r.source,
                    "source_pk": r.source_pk,
                    "record_hash": r.record_hash,
                    "first_seen_at": r.first_seen_at.isoformat(),
                    "last_seen_at": r.last_seen_at.isoformat(),
                    "payload": r.payload,
                }
                for r in self.records
            ],
            "edges": [
                {
                    "edge_id": e.edge_id,
                    "record_a_id": e.record_a_id,
                    "record_b_id": e.record_b_id,
                    "kind": e.kind,
                    "score": e.score,
                    "matchkey_name": e.matchkey_name,
                    "run_name": e.run_name,
                    "recorded_at": e.recorded_at.isoformat(),
                    "field_scores": e.field_scores,
                    "negative_evidence": e.negative_evidence,
                    "controller_snapshot": e.controller_snapshot,
                }
                for e in self.edges
            ],
            "events": [
                {
                    "event_id": ev.event_id,
                    "kind": ev.kind,
                    "payload": ev.payload,
                    "run_name": ev.run_name,
                    "recorded_at": ev.recorded_at.isoformat(),
                }
                for ev in self.events
            ],
        }


def get_entity(
    store: IdentityStore,
    entity_id: str,
    event_limit: int = 100,
) -> IdentityView | None:
    """Fetch an aggregated view of one identity. Returns None if not found."""
    node = store.get_identity(entity_id)
    if node is None:
        return None
    records = store.get_records_for_entity(entity_id)
    edges = store.edges_for_entity(entity_id)
    events = store.history(entity_id, limit=event_limit)
    return IdentityView(node=node, records=records, edges=edges, events=events)


def find_by_record(
    store: IdentityStore, record_id: str
) -> IdentityView | None:
    """Resolve a record id to its identity view."""
    eid = store.find_entity_by_record(record_id)
    if eid is None:
        return None
    return get_entity(store, eid)


def list_entities(
    store: IdentityStore,
    dataset: str | None = None,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[dict[str, Any]]:
    nodes = store.list_identities(dataset=dataset, status=status, limit=limit, offset=offset)
    return [
        {
            "entity_id": n.entity_id,
            "status": n.status,
            "confidence": n.confidence,
            "merged_into": n.merged_into,
            "dataset": n.dataset,
            "updated_at": n.updated_at.isoformat(),
        }
        for n in nodes
    ]


def history(
    store: IdentityStore, entity_id: str, limit: int | None = None
) -> list[dict[str, Any]]:
    events = store.history(entity_id, limit=limit)
    return [
        {
            "event_id": e.event_id,
            "kind": e.kind,
            "payload": e.payload,
            "run_name": e.run_name,
            "recorded_at": e.recorded_at.isoformat(),
        }
        for e in events
    ]


def find_conflicts(
    store: IdentityStore, dataset: str | None = None
) -> list[dict[str, Any]]:
    edges = store.find_conflicts(dataset=dataset)
    return [
        {
            "edge_id": e.edge_id,
            "entity_id": e.entity_id,
            "record_a_id": e.record_a_id,
            "record_b_id": e.record_b_id,
            "score": e.score,
            "matchkey_name": e.matchkey_name,
            "run_name": e.run_name,
            "recorded_at": e.recorded_at.isoformat(),
        }
        for e in edges
    ]


def manual_merge(
    store: IdentityStore,
    keep_entity_id: str,
    absorb_entity_id: str,
    reason: str | None = None,
    run_name: str = "manual",
    actor: str | None = None,
    trust: float | None = None,
) -> dict[str, Any]:
    """Manually merge ``absorb_entity_id`` into ``keep_entity_id``.

    Reassigns every record of the absorbed entity to the kept entity and
    retires the loser. Emits ``manual_merge`` events on both sides, stamped with
    ``actor``/``trust`` provenance (#1075) so the audit log records WHO merged
    these and on what authority.
    """
    winner = store.get_identity(keep_entity_id)
    loser = store.get_identity(absorb_entity_id)
    if winner is None or loser is None:
        raise ValueError("Both entity_ids must exist")
    if winner.status != IdentityStatus.ACTIVE.value:
        raise ValueError("Winner must be active")
    for rec in store.get_records_for_entity(absorb_entity_id):
        rec.entity_id = keep_entity_id
        rec.last_seen_at = datetime.now()
        store.upsert_record(rec)
    store.retire_identity(absorb_entity_id, merged_into=keep_entity_id)
    now = datetime.now()
    store.emit_event(IdentityEvent(
        entity_id=keep_entity_id,
        kind=EventKind.MANUAL_MERGE.value,
        payload={"absorbed": absorb_entity_id, "reason": reason},
        run_name=run_name, actor=actor, trust=trust, recorded_at=now,
    ))
    store.emit_event(IdentityEvent(
        entity_id=absorb_entity_id,
        kind=EventKind.MANUAL_MERGE.value,
        payload={"merged_into": keep_entity_id, "reason": reason},
        run_name=run_name, actor=actor, trust=trust, recorded_at=now,
    ))
    return {"keep": keep_entity_id, "absorbed": absorb_entity_id, "at": now.isoformat()}


def manual_split(
    store: IdentityStore,
    entity_id: str,
    record_ids: list[str],
    reason: str | None = None,
    run_name: str = "manual",
    actor: str | None = None,
    trust: float | None = None,
) -> dict[str, Any]:
    """Detach ``record_ids`` from ``entity_id`` into a brand-new identity.

    The original identity keeps its remaining records. The new identity is
    created with no rolled-up golden record (caller can refresh by re-running
    dedupe or via a steward UI). The ``manual_split`` events carry ``actor``/
    ``trust`` provenance (#1075).
    """
    from goldenmatch.identity.store import new_entity_id

    parent = store.get_identity(entity_id)
    if parent is None:
        raise ValueError(f"Entity {entity_id} not found")
    if not record_ids:
        raise ValueError("record_ids must be non-empty")
    new_eid = new_entity_id()
    now = datetime.now()
    store.upsert_identity(IdentityNode(
        entity_id=new_eid,
        status=IdentityStatus.ACTIVE.value,
        dataset=parent.dataset,
        created_at=now,
        updated_at=now,
    ))
    moved: list[str] = []
    for rid in record_ids:
        rec = store.get_record(rid)
        if rec is None or rec.entity_id != entity_id:
            continue
        rec.entity_id = new_eid
        rec.last_seen_at = now
        store.upsert_record(rec)
        moved.append(rid)
    store.emit_event(IdentityEvent(
        entity_id=entity_id,
        kind=EventKind.MANUAL_SPLIT.value,
        payload={"split_to": new_eid, "records": moved, "reason": reason},
        run_name=run_name, actor=actor, trust=trust, recorded_at=now,
    ))
    store.emit_event(IdentityEvent(
        entity_id=new_eid,
        kind=EventKind.MANUAL_SPLIT.value,
        payload={"split_from": entity_id, "records": moved, "reason": reason},
        run_name=run_name, actor=actor, trust=trust, recorded_at=now,
    ))
    return {"new_entity_id": new_eid, "moved": moved, "at": now.isoformat()}


def claim_record(
    store: IdentityStore,
    entity_id: str,
    record_id: str,
    reason: str | None = None,
    run_name: str = "manual",
    actor: str | None = None,
    trust: float | None = None,
) -> dict[str, Any]:
    """Claim ``record_id`` into ``entity_id``, moving it out of any prior entity.

    The agent/steward-facing op for "this record belongs to that identity" -- the
    fourth identity mutation (#1075) alongside merge / split / resolve-conflict.
    Reassigns the record and emits a ``claimed`` event (with actor/trust
    provenance) on the gaining entity, plus one on the losing entity when the
    record was previously attached elsewhere.
    """
    target = store.get_identity(entity_id)
    if target is None:
        raise ValueError(f"Entity {entity_id} not found")
    if target.status != IdentityStatus.ACTIVE.value:
        raise ValueError("Target entity must be active")
    rec = store.get_record(record_id)
    if rec is None:
        raise ValueError(f"Record {record_id} not found")

    prev_entity = rec.entity_id
    now = datetime.now()
    if prev_entity == entity_id:
        return {"entity_id": entity_id, "record_id": record_id,
                "moved": False, "from_entity": prev_entity, "at": now.isoformat()}

    rec.entity_id = entity_id
    rec.last_seen_at = now
    store.upsert_record(rec)
    store.emit_event(IdentityEvent(
        entity_id=entity_id,
        kind=EventKind.CLAIMED.value,
        payload={"record_id": record_id, "from_entity": prev_entity, "reason": reason},
        run_name=run_name, actor=actor, trust=trust, recorded_at=now,
    ))
    if prev_entity:
        store.emit_event(IdentityEvent(
            entity_id=prev_entity,
            kind=EventKind.CLAIMED.value,
            payload={"record_id": record_id, "to_entity": entity_id, "reason": reason},
            run_name=run_name, actor=actor, trust=trust, recorded_at=now,
        ))
    return {"entity_id": entity_id, "record_id": record_id, "moved": True,
            "from_entity": prev_entity, "at": now.isoformat()}


# ── Claim lifecycle (#1256) ──────────────────────────────────────────────────
# promote / amend / revoke are the explicit, auditable transitions of a claim's
# authority. They emit a dedicated EventKind carrying the claim_type authority
# tier + typed evidence_ref + a previous_claim_id chain, so a compliance reviewer
# can see an agent inference *becoming* durable shared truth (promote) rather than
# that transition happening as invisible drift. Appending a fresh claim needs no
# helper -- ``store.emit_event(IdentityEvent(..., claim_type=..., evidence_ref=...))``.


def _require_active_entity(store: IdentityStore, entity_id: str) -> None:
    node = store.get_identity(entity_id)
    if node is None:
        raise ValueError(f"Entity {entity_id} not found")
    if node.status != IdentityStatus.ACTIVE.value:
        raise ValueError("Target entity must be active")


def _resolve_prior_claim(
    store: IdentityStore, entity_id: str, previous_claim_id: int | None
) -> None:
    """Validate that ``previous_claim_id`` (when given) is an event on this
    entity. Lifecycle ops are rare/manual, so a history scan is fine."""
    if previous_claim_id is None:
        return
    if not any(e.event_id == previous_claim_id for e in store.history(entity_id)):
        raise ValueError(
            f"previous_claim_id {previous_claim_id} not found on entity {entity_id}"
        )


def _emit_claim_lifecycle(
    store: IdentityStore,
    entity_id: str,
    operation: EventKind,
    *,
    previous_claim_id: int | None,
    claim_type: str | None,
    evidence_ref: str | None,
    reason: str | None,
    extra_payload: dict[str, Any] | None,
    run_name: str,
    actor: str | None,
    trust: float | None,
) -> dict[str, Any]:
    _require_active_entity(store, entity_id)
    _resolve_prior_claim(store, entity_id, previous_claim_id)
    now = datetime.now()
    payload: dict[str, Any] = {"reason": reason}
    if extra_payload:
        payload.update(extra_payload)
    ct = str(claim_type) if claim_type is not None else None
    er = str(evidence_ref) if evidence_ref is not None else None
    event_id = store.emit_event(IdentityEvent(
        entity_id=entity_id,
        kind=operation.value,
        payload=payload,
        claim_type=ct,
        evidence_ref=er,
        previous_claim_id=previous_claim_id,
        run_name=run_name,
        actor=actor,
        trust=trust,
        recorded_at=now,
    ))
    return {
        "event_id": event_id,
        "entity_id": entity_id,
        "operation": operation.value,
        "claim_type": ct,
        "evidence_ref": er,
        "previous_claim_id": previous_claim_id,
        "at": now.isoformat(),
    }


def promote_claim(
    store: IdentityStore,
    entity_id: str,
    *,
    to_claim_type: str | ClaimType,
    previous_claim_id: int | None = None,
    evidence_ref: str | EvidenceRef | None = None,
    reason: str | None = None,
    run_name: str = "manual",
    actor: str | None = None,
    trust: float | None = None,
) -> dict[str, Any]:
    """Raise a claim's authority tier (e.g. ``inference`` -> ``verified``).

    Records the transition as an explicit ``promote`` event so an agent inference
    becoming durable shared truth is auditable, not invisible drift. ``evidence_ref``
    is what justified the promotion (tool-call / source / user-confirmation / test-run).
    """
    return _emit_claim_lifecycle(
        store, entity_id, EventKind.PROMOTE,
        previous_claim_id=previous_claim_id,
        claim_type=to_claim_type, evidence_ref=evidence_ref, reason=reason,
        extra_payload=None, run_name=run_name, actor=actor, trust=trust,
    )


def amend_claim(
    store: IdentityStore,
    entity_id: str,
    *,
    previous_claim_id: int | None = None,
    claim_type: str | ClaimType | None = None,
    evidence_ref: str | EvidenceRef | None = None,
    payload: dict[str, Any] | None = None,
    reason: str | None = None,
    run_name: str = "manual",
    actor: str | None = None,
    trust: float | None = None,
) -> dict[str, Any]:
    """Supersede a prior claim's content with a corrected/updated claim.

    ``claim_type`` defaults to unchanged (None) -- pass it to also restate the tier.
    ``payload`` carries the amended claim body.
    """
    return _emit_claim_lifecycle(
        store, entity_id, EventKind.AMEND,
        previous_claim_id=previous_claim_id,
        claim_type=claim_type, evidence_ref=evidence_ref, reason=reason,
        extra_payload=payload, run_name=run_name, actor=actor, trust=trust,
    )


def revoke_claim(
    store: IdentityStore,
    entity_id: str,
    *,
    previous_claim_id: int | None = None,
    reason: str | None = None,
    run_name: str = "manual",
    actor: str | None = None,
    trust: float | None = None,
) -> dict[str, Any]:
    """Retract a claim. Emits a ``revoke`` event chained (via ``previous_claim_id``)
    to the claim it retracts; the append-only log keeps the revoked claim visible
    with its retraction recorded rather than deleting it."""
    return _emit_claim_lifecycle(
        store, entity_id, EventKind.REVOKE,
        previous_claim_id=previous_claim_id,
        claim_type=None, evidence_ref=None, reason=reason,
        extra_payload=None, run_name=run_name, actor=actor, trust=trust,
    )


__all__ = [
    "EdgeKind",
    "EventKind",
    "IdentityView",
    "find_by_record",
    "find_conflicts",
    "get_entity",
    "history",
    "list_entities",
    "manual_merge",
    "manual_split",
    "claim_record",
    "ClaimType",
    "EvidenceRef",
    "promote_claim",
    "amend_claim",
    "revoke_claim",
]
