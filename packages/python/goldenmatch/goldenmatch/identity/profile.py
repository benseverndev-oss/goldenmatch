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


def _summary_from_counts(
    store: IdentityStore,
    dataset: str | None,
    by_status: dict[str, int],
    per_entity: dict[str, int],
    source_breakdown: dict[str, int],
) -> IdentitySummary:
    record_counts = list(per_entity.values())
    total_records = sum(record_counts)
    singleton = sum(1 for c in record_counts if c == 1)
    multi = sum(1 for c in record_counts if c > 1)
    avg = (total_records / len(record_counts)) if record_counts else 0.0
    p50 = float(statistics.median(record_counts)) if record_counts else 0.0
    mx = max(record_counts) if record_counts else 0
    largest = sorted(per_entity.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    return IdentitySummary(
        dataset=dataset,
        total_entities=sum(by_status.values()),
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
            {"entity_id": eid, "record_count": n} for eid, n in largest
        ],
    )


def _summary_stats_scan(
    store: IdentityStore, dataset: str | None,
) -> IdentitySummary:
    """Per-entity fallback for stores without the grouped aggregates (mongo)."""
    by_status: dict[str, int] = {}
    for node in _iter_entities(store, dataset, None):
        by_status[node.status] = by_status.get(node.status, 0) + 1

    per_entity: dict[str, int] = {}
    source_breakdown: dict[str, int] = {}
    for node in _iter_entities(store, dataset, IdentityStatus.ACTIVE.value):
        recs = store.get_records_for_entity(node.entity_id)
        per_entity[node.entity_id] = len(recs)
        for rec in recs:
            source_breakdown[rec.source] = source_breakdown.get(rec.source, 0) + 1
    return _summary_from_counts(store, dataset, by_status, per_entity, source_breakdown)


def identity_summary_stats(
    store: IdentityStore, dataset: str | None = None,
) -> IdentitySummary:
    """Graph-level health summary. Records live on active entities (merges
    reassign them), so per-entity record stats are computed over active
    entities; status counts cover every status.

    On SQL backends this is a handful of grouped queries (#2198). It used to
    call ``get_records_for_entity`` once per entity -- O(entities) round-trips
    that dominated wall-clock at scale -- so it now asks the store for the same
    tallies in two ``GROUP BY`` queries, falling back to the per-entity scan for
    backends that don't implement them."""
    try:
        by_status = store.status_counts(dataset)
        per_entity, source_breakdown = store.active_record_stats(dataset)
    except (AttributeError, NotImplementedError):
        return _summary_stats_scan(store, dataset)
    return _summary_from_counts(store, dataset, by_status, per_entity, source_breakdown)


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

    # Record counts for every active entity in one grouped query, so the loop
    # below doesn't issue a get_records_for_entity per flagged entity (#2198).
    try:
        record_counts, _ = store.active_record_stats(dataset)
    except (AttributeError, NotImplementedError):
        record_counts = None

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
        rc = (record_counts.get(node.entity_id, 0) if record_counts is not None
              else len(store.get_records_for_entity(node.entity_id)))
        items.append(WorklistItem(
            entity_id=node.entity_id,
            reasons=reasons,
            conflict_count=cc,
            confidence=node.confidence,
            record_count=rc,
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


# ── Customer 360 (unified serving read) ──────────────────────────────────────
#
# One read that composes the durable spine into the whole picture of a customer:
# golden record + per-field source provenance + every linked source record +
# the event timeline + the federated relationship overlay. Everything below is
# derived from PERSISTED store state (no live frame, no migration) -- the serving
# realization of decision 0049 (IdentityStore is the spine; the relationship
# overlay is read from the store's own ``identity_relationships`` edges) and the
# design ``context-network/architecture/customer-360-data-connection.md`` (D1).


def _norm(value: Any) -> str | None:
    """Normalize a payload/golden cell for provenance comparison: ``None`` stays
    ``None``; everything else is ``str``-cast and stripped, so a golden ``42``
    matches a source payload ``"42"`` (payloads round-trip through JSON) without
    treating an empty string as a real value."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None


@dataclass
class FieldContribution:
    source: str
    record_id: str
    source_pk: str
    value: Any
    last_seen: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "record_id": self.record_id,
            "source_pk": self.source_pk,
            "value": self.value,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
        }


@dataclass
class FieldProvenance:
    """Why one golden field holds its value: the source record that wins it, the
    other records that agree, and any competing values that were overridden.

    Store-derived -- attributes the persisted golden value back to the persisted
    source records that carry it. This is the durable, always-available answer to
    "where did this come from"; the richer resolve-time ``CellProvenance`` (merge
    strategy + confidence) is emitted by ``survivorship.build_golden_with_provenance``
    at write time and is a superset when a caller has the live frame."""

    field: str
    value: Any
    winning_source: str | None
    winning_record_id: str | None
    contributors: list[FieldContribution]
    conflicting_values: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "value": self.value,
            "winning_source": self.winning_source,
            "winning_record_id": self.winning_record_id,
            "contributors": [c.as_dict() for c in self.contributors],
            "conflicting_values": self.conflicting_values,
        }


def _field_provenance(
    golden_record: dict[str, Any] | None,
    records: list[Any],
) -> list[FieldProvenance]:
    """Attribute each golden field to the source records that carry its value.

    Winner = the contributing record with the most recent ``last_seen_at`` (ties
    broken by ``record_id`` for determinism). ``conflicting_values`` lists the
    distinct competing values other records held for the field, so an overridden
    value stays visible rather than silently dropped."""
    if not golden_record:
        return []
    out: list[FieldProvenance] = []
    for fname, gvalue in golden_record.items():
        gnorm = _norm(gvalue)
        contributors: list[FieldContribution] = []
        conflicts: dict[str, dict[str, Any]] = {}
        for rec in records:
            payload = rec.payload or {}
            if fname not in payload:
                continue
            rnorm = _norm(payload[fname])
            if rnorm is not None and rnorm == gnorm:
                contributors.append(FieldContribution(
                    source=rec.source,
                    record_id=rec.record_id,
                    source_pk=rec.source_pk,
                    value=payload[fname],
                    last_seen=getattr(rec, "last_seen_at", None),
                ))
            elif rnorm is not None:
                # A different, non-empty value for the same field: an override.
                conflicts.setdefault(rnorm, {
                    "value": payload[fname],
                    "source": rec.source,
                    "record_id": rec.record_id,
                })
        contributors.sort(
            key=lambda c: (
                c.last_seen or datetime.min,
                c.record_id,
            ),
            reverse=True,
        )
        winner = contributors[0] if contributors else None
        out.append(FieldProvenance(
            field=fname,
            value=gvalue,
            winning_source=winner.source if winner else None,
            winning_record_id=winner.record_id if winner else None,
            contributors=contributors,
            conflicting_values=sorted(
                conflicts.values(), key=lambda d: str(d["value"])
            ),
        ))
    return out


def _record_as_dict(rec: Any) -> dict[str, Any]:
    return {
        "record_id": rec.record_id,
        "source": rec.source,
        "source_pk": rec.source_pk,
        "dataset": rec.dataset,
        "first_seen": rec.first_seen_at.isoformat() if rec.first_seen_at else None,
        "last_seen": rec.last_seen_at.isoformat() if rec.last_seen_at else None,
        "payload": rec.payload,
    }


def _event_as_dict(ev: Any) -> dict[str, Any]:
    reason = None
    if isinstance(ev.payload, dict):
        reason = ev.payload.get("reason")
    return {
        "event_id": ev.event_id,
        "kind": ev.kind,
        "actor": ev.actor,
        "trust": ev.trust,
        "run_name": ev.run_name,
        "dataset": ev.dataset,
        "reason": reason,
        "recorded_at": ev.recorded_at.isoformat() if ev.recorded_at else None,
    }


@dataclass
class Customer360:
    """The unified serving view of one customer: the golden record and its
    per-field source provenance, every linked source record, the event timeline,
    and the federated relationship neighborhood. All fields are JSON-ready via
    ``as_dict()``."""

    entity_id: str
    profile: EntityProfile
    golden_record: dict[str, Any] | None
    field_provenance: list[FieldProvenance]
    source_records: list[dict[str, Any]]
    timeline: list[dict[str, Any]]
    relationships: list[dict[str, Any]]

    def as_dict(self) -> dict[str, Any]:
        p = self.profile
        return {
            "entity_id": self.entity_id,
            "status": p.status,
            "merged_into": p.merged_into,
            "dataset": p.dataset,
            "confidence": p.confidence,
            "version": p.version,
            "record_count": p.record_count,
            "sources": p.sources,
            "source_counts": p.source_counts,
            "conflict_count": p.conflict_count,
            "first_seen": p.first_seen.isoformat() if p.first_seen else None,
            "last_seen": p.last_seen.isoformat() if p.last_seen else None,
            "golden_record": self.golden_record,
            "field_provenance": [fp.as_dict() for fp in self.field_provenance],
            "source_records": self.source_records,
            "timeline": self.timeline,
            "relationships": self.relationships,
        }


def customer_360(
    store: IdentityStore,
    entity_id: str,
    *,
    include_relationships: bool = True,
    timeline_limit: int | None = None,
) -> Customer360 | None:
    """The single Customer 360 serving read: everything known about one entity,
    composed from the durable store in one call. Returns ``None`` if the entity
    does not exist -- it calls ``entity_profile`` directly for that check and
    returns its ``None`` through, so this is not a separate implementation
    that could disagree with it.

    Composes, all from persisted state (no live frame, no migration):

    * **profile** -- ``entity_profile`` (status, confidence, source mix, version,
      activity window, conflict count);
    * **golden_record + field_provenance** -- the golden values plus, per field,
      the source record(s) that carry each value and any overridden competing
      values (``_field_provenance``);
    * **source_records** -- every linked source record with its payload;
    * **timeline** -- the append-only ``IdentityEvent`` log (the audit trail);
    * **relationships** -- the entity's neighborhood from the store's own
      relationship overlay (decision 0049). If the backend doesn't support the
      relationship read (e.g. mongo), it degrades to an empty list rather than
      failing the whole 360.

    ``include_relationships=False`` skips the relationship read; ``timeline_limit``
    caps the number of (most-recent-first is the store's order) events."""
    profile = entity_profile(store, entity_id)
    if profile is None:
        return None

    records = store.get_records_for_entity(entity_id)
    field_prov = _field_provenance(profile.golden_record, records)

    relationships: list[dict[str, Any]] = []
    if include_relationships:
        try:
            relationships = store.get_relationships(entity_id)
        except (AttributeError, NotImplementedError):
            relationships = []

    events = store.history(entity_id, limit=timeline_limit)

    return Customer360(
        entity_id=entity_id,
        profile=profile,
        golden_record=profile.golden_record,
        field_provenance=field_prov,
        source_records=[_record_as_dict(r) for r in records],
        timeline=[_event_as_dict(e) for e in events],
        relationships=relationships,
    )


def customer_360_page(
    store: IdentityStore,
    entity_id: str,
    *,
    include_relationships: bool = True,
    timeline_limit: int | None = None,
) -> dict[str, Any] | None:
    """JSON-ready Customer 360: the ``as_dict()`` of :func:`customer_360`, or
    ``None`` if the entity doesn't exist. This is the single serialization the
    serving surfaces (future MCP ``customer_360`` tool / REST
    ``/api/v1/identities/{id}/360``) share, so every surface returns the same
    360 shape (North Star: shared capabilities conform)."""
    result = customer_360(
        store, entity_id,
        include_relationships=include_relationships,
        timeline_limit=timeline_limit,
    )
    return result.as_dict() if result is not None else None
