"""Identity Graph -- durable, queryable graph of identities, source records,
match evidence, conflicts, and versioned changes over time.

See ``docs/superpowers/specs/2026-05-12-identity-graph-design.md``.
"""
from __future__ import annotations

from goldenmatch.identity.model import (
    EdgeKind,
    EventKind,
    EvidenceEdge,
    IdentityAlias,
    IdentityEvent,
    IdentityNode,
    IdentityStatus,
    SourceRecord,
)
from goldenmatch.identity.query import (
    IdentityView,
    find_by_record,
    find_conflicts,
    get_entity,
    history,
    list_entities,
    manual_merge,
    manual_split,
)
from goldenmatch.identity.resolve import ResolveSummary, resolve_clusters
from goldenmatch.identity.store import IdentityStore, new_entity_id

__all__ = [
    "IdentityView",
    "ResolveSummary",
    "find_by_record",
    "find_conflicts",
    "get_entity",
    "history",
    "list_entities",
    "manual_merge",
    "manual_split",
    "resolve_clusters",
    "EdgeKind",
    "EventKind",
    "EvidenceEdge",
    "IdentityAlias",
    "IdentityEvent",
    "IdentityNode",
    "IdentityStatus",
    "IdentityStore",
    "SourceRecord",
    "new_entity_id",
]
