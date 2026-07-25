"""Entity profiles + stewardship ops views -- MDM ops (#1114, epic #1108).

The final CDP/MDM phase is "scale and operate it". The distributed-resolve side
(Phase 6: ``goldenmatch.distributed.identity``, the pooled Postgres + bulk-COPY
write path) already exists; what an MDM steward still lacks is the *operate it*
surface -- a way to see, per entity, what it is and where it came from, a
graph-level health summary, and a prioritized worklist of what needs attention.

This module is that read-side surface, computed from the durable store and
exposed via MCP (``identity_profile`` / ``identity_stats`` / ``identity_worklist``):

* ``entity_profile(store, entity_id)`` -- one entity's full profile: record
  count + per-source breakdown, golden record, confidence, conflict count, a
  canonical version (count of structural events), and first/last activity.
* ``identity_summary_stats(store, dataset)`` -- graph health: entities by status,
  total records, records-per-entity distribution, conflict total, source mix,
  and the largest entities.
* ``steward_worklist(store, dataset)`` -- the prioritized queue of active
  entities that need a steward's eye (open conflicts and/or weak confidence).

Read-only; no schema migration.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from goldenmatch.identity.model import EdgeKind, EventKind, IdentityStatus

if TYPE_CHECKING:
    from goldenmatch.identity.store import IdentityStore

_PAGE = 500

# Events that advance an entity's canonical version (a structural change to its
# shape). Excludes the terminal RETIRED marker.
_STRUCTURAL_EVENTS: frozenset[str] = frozenset({
    EventKind.CREATED.value,
    EventKind.ABSORBED_RECORD.value,
    EventKind.MERGED_WITH.value,
    EventKind.SPLIT_FROM.value,
    EventKind.MANUAL_MERGE.value,
    EventKind.MANUAL_SPLIT.value,
})


# ── Per-entity profile ──────────────────────────────────────────────────────


@dataclass
class EntityProfile:
    entity_id: str
    status: str
    merged_into: str | None
    dataset: str | None
    confidence: float | None
    golden_record: dict[str, Any] | None
    record_count: int
    sources: list[str]
    source_counts: dict[str, int]
    conflict_count: int
    edge_count: int
    version: int
    created_at: datetime | None
    updated_at: datetime | None
    first_seen: datetime | None
    last_seen: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "status": self.status,
            "merged_into": self.merged_into,
            "dataset": self.dataset,
            "confidence": self.confidence,
            "golden_record": self.golden_record,
            "record_count": self.record_count,
            "sources": self.sources,
            "source_counts": self.source_counts,
            "conflict_count": self.conflict_count,
            "edge_count": self.edge_count,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }


def entity_profile(
    store: IdentityStore, entity_id: str,
) -> EntityProfile | None:
    """Full profile of one entity, or ``None`` if it doesn't exist."""
    node = store.get_identity(entity_id)
    if node is None:
        return None

    records = store.get_records_for_entity(entity_id)
    source_counts: dict[str, int] = {}
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    for rec in records:
        source_counts[rec.source] = source_counts.get(rec.source, 0) + 1
        if rec.first_seen_at and (first_seen is None or rec.first_seen_at < first_seen):
            first_seen = rec.first_seen_at
        if rec.last_seen_at and (last_seen is None or rec.last_seen_at > last_seen):
            last_seen = rec.last_seen_at

    edges = store.edges_for_entity(entity_id)
    conflict_count = sum(
        1 for e in edges if e.kind == EdgeKind.CONFLICTS_WITH.value
    )
    version = sum(
        1 for ev in store.history(entity_id) if ev.kind in _STRUCTURAL_EVENTS
    )

    return EntityProfile(
        entity_id=node.entity_id,
        status=node.status,
        merged_into=node.merged_into,
        dataset=node.dataset,
        confidence=node.confidence,
        golden_record=node.golden_record,
        record_count=len(records),
        sources=sorted(source_counts),
        source_counts=source_counts,
        conflict_count=conflict_count,
        edge_count=len(edges),
        version=version,
        created_at=node.created_at,
        updated_at=node.updated_at,
        first_seen=first_seen,
        last_seen=last_seen,
    )


# ── Graph-level summary ─────────────────────────────────────────────────────


def _iter_entities(store: IdentityStore, dataset: str | None, status: str | None):
    offset = 0
    while True:
        page = store.list_identities(
            dataset=dataset, status=status, limit=_PAGE, offset=offset,
        )
        yield from page
        if len(page) < _PAGE:
            break
        offset += _PAGE


@dataclass
class IdentitySummary:
    dataset: str | None
    total_entities: int
    by_status: dict[str, int]
    total_records: int
    records_per_entity_avg: float
    records_per_entity_p50: float
    records_per_entity_max: int
    singleton_entities: int
    multi_record_entities: int
    total_conflicts: int
    source_breakdown: dict[str, int]
    largest_entities: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "total_entities": self.total_entities,
            "by_status": self.by_status,
            "total_records": self.total_records,
            "records_per_entity_avg": self.records_per_entity_avg,
            "records_per_entity_p50": self.records_per_entity_p50,
            "records_per_entity_max": self.records_per_entity_max,
            "singleton_entities": self.singleton_entities,
            "multi_record_entities": self.multi_record_entities,
            "total_conflicts": self.total_conflicts,
            "source_breakdown": self.source_breakdown,
            "largest_entities": self.largest_entities,
        }


def identity_summary_stats(
    store: IdentityStore, dataset: str | None = None,
) -> IdentitySummary:
    """Graph-level health summary. Records live on active entities (merges
    reassign them), so per-entity record stats are computed over active
    entities; status counts cover every status."""
    by_status: dict[str, int] = {}
    total_entities = 0
    for node in _iter_entities(store, dataset, None):
        total_entities += 1
        by_status[node.status] = by_status.get(node.status, 0) + 1

    record_counts: list[int] = []
    source_breakdown: dict[str, int] = {}
    largest: list[tuple[str, int]] = []
    for node in _iter_entities(store, dataset, IdentityStatus.ACTIVE.value):
        recs = store.get_records_for_entity(node.entity_id)
        record_counts.append(len(recs))
        for rec in recs:
            source_breakdown[rec.source] = source_breakdown.get(rec.source, 0) + 1
        largest.append((node.entity_id, len(recs)))

    total_records = sum(record_counts)
    singleton = sum(1 for c in record_counts if c == 1)
    multi = sum(1 for c in record_counts if c > 1)
    avg = (total_records / len(record_counts)) if record_counts else 0.0
    p50 = float(statistics.median(record_counts)) if record_counts else 0.0
    mx = max(record_counts) if record_counts else 0
    largest.sort(key=lambda kv: (-kv[1], kv[0]))

    return IdentitySummary(
        dataset=dataset,
        total_entities=total_entities,
        by_status=by_status,
        total_records=total_records,
        records_per_entity_avg=round(avg, 4),
        records_per_entity_p50=p50,
        records_per_entity_max=mx,
        singleton_entities=singleton,
        multi_record_entities=multi,
        total_conflicts=len(store.find_conflicts(dataset=dataset)),
        source_breakdown=source_breakdown,
        largest_entities=[
            {"entity_id": eid, "record_count": n} for eid, n in largest[:10]
        ],
    )


# ── Stewardship worklist ────────────────────────────────────────────────────


@dataclass
class WorklistItem:
    entity_id: str
    reasons: list[str] = field(default_factory=list)
    conflict_count: int = 0
    confidence: float | None = None
    record_count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "reasons": self.reasons,
            "conflict_count": self.conflict_count,
            "confidence": self.confidence,
            "record_count": self.record_count,
        }


def steward_worklist(
    store: IdentityStore,
    dataset: str | None = None,
    *,
    weak_confidence: float = 0.6,
    limit: int = 50,
) -> list[WorklistItem]:
    """Prioritized queue of active entities needing a steward's attention:
    those with open conflicts and/or confidence below ``weak_confidence``.
    Highest conflict count first, then lowest confidence."""
    # Conflict counts per entity (one query, grouped).
    conflicts_by_entity: dict[str, int] = {}
    for e in store.find_conflicts(dataset=dataset):
        conflicts_by_entity[e.entity_id] = conflicts_by_entity.get(e.entity_id, 0) + 1

    items: list[WorklistItem] = []
    for node in _iter_entities(store, dataset, IdentityStatus.ACTIVE.value):
        cc = conflicts_by_entity.get(node.entity_id, 0)
        low_conf = node.confidence is not None and node.confidence < weak_confidence
        if cc == 0 and not low_conf:
            continue
        reasons: list[str] = []
        if cc > 0:
            reasons.append("has_conflicts")
        if low_conf:
            reasons.append("low_confidence")
        items.append(WorklistItem(
            entity_id=node.entity_id,
            reasons=reasons,
            conflict_count=cc,
            confidence=node.confidence,
            record_count=len(store.get_records_for_entity(node.entity_id)),
        ))

    items.sort(key=lambda it: (
        -it.conflict_count,
        it.confidence if it.confidence is not None else 1.0,
        it.entity_id,
    ))
    return items[:limit]


def steward_worklist_page(
    store: IdentityStore,
    dataset: str | None = None,
    *,
    weak_confidence: float = 0.6,
    limit: int = 50,
) -> dict[str, Any]:
    """JSON-ready steward worklist: ``{"items": [...]}``. Single source for the
    MCP ``identity_worklist`` tool and the SQL ``gm_identity_worklist`` bridge."""
    items = steward_worklist(
        store, dataset, weak_confidence=weak_confidence, limit=limit
    )
    return {"items": [it.as_dict() for it in items]}
