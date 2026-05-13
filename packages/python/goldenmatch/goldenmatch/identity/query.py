"""Read-side API for the Identity Graph.

These helpers wrap ``IdentityStore`` and return rich result objects suitable
for the CLI, REST routers, MCP tools, and the web frontend.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from goldenmatch.identity.model import (
    EdgeKind,
    EventKind,
    EvidenceEdge,
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
) -> dict[str, Any]:
    """Manually merge ``absorb_entity_id`` into ``keep_entity_id``.

    Reassigns every record of the absorbed entity to the kept entity and
    retires the loser. Emits ``manual_merge`` events on both sides.
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
        run_name=run_name, recorded_at=now,
    ))
    store.emit_event(IdentityEvent(
        entity_id=absorb_entity_id,
        kind=EventKind.MANUAL_MERGE.value,
        payload={"merged_into": keep_entity_id, "reason": reason},
        run_name=run_name, recorded_at=now,
    ))
    return {"keep": keep_entity_id, "absorbed": absorb_entity_id, "at": now.isoformat()}


def manual_split(
    store: IdentityStore,
    entity_id: str,
    record_ids: list[str],
    reason: str | None = None,
    run_name: str = "manual",
) -> dict[str, Any]:
    """Detach ``record_ids`` from ``entity_id`` into a brand-new identity.

    The original identity keeps its remaining records. The new identity is
    created with no rolled-up golden record (caller can refresh by re-running
    dedupe or via a steward UI).
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
        run_name=run_name, recorded_at=now,
    ))
    store.emit_event(IdentityEvent(
        entity_id=new_eid,
        kind=EventKind.MANUAL_SPLIT.value,
        payload={"split_from": entity_id, "records": moved, "reason": reason},
        run_name=run_name, recorded_at=now,
    ))
    return {"new_entity_id": new_eid, "moved": moved, "at": now.isoformat()}


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
]
