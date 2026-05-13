"""Identity Graph data classes."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class IdentityStatus(StrEnum):
    ACTIVE = "active"
    MERGED_INTO = "merged_into"
    SPLIT = "split"
    RETIRED = "retired"


class EdgeKind(StrEnum):
    SAME_AS = "same_as"
    POSSIBLE_SAME_AS = "possible_same_as"
    CONFLICTS_WITH = "conflicts_with"
    DERIVED_FROM = "derived_from"


class EventKind(StrEnum):
    CREATED = "created"
    ABSORBED_RECORD = "absorbed_record"
    MERGED_WITH = "merged_with"
    SPLIT_FROM = "split_from"
    RETIRED = "retired"
    MANUAL_MERGE = "manual_merge"
    MANUAL_SPLIT = "manual_split"


@dataclass
class IdentityNode:
    entity_id: str
    status: str = IdentityStatus.ACTIVE.value
    merged_into: str | None = None
    golden_record: dict[str, Any] | None = None
    confidence: float | None = None
    dataset: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)


@dataclass
class SourceRecord:
    """A single record observation. ``record_id`` is ``{source}:{source_pk}``."""

    record_id: str
    source: str
    source_pk: str
    record_hash: str
    entity_id: str | None = None
    payload: dict[str, Any] | None = None
    dataset: str | None = None
    first_seen_at: datetime = field(default_factory=datetime.now)
    last_seen_at: datetime = field(default_factory=datetime.now)


@dataclass
class EvidenceEdge:
    entity_id: str
    record_a_id: str
    record_b_id: str
    kind: str = EdgeKind.SAME_AS.value
    score: float | None = None
    matchkey_name: str | None = None
    field_scores: dict[str, Any] | None = None
    negative_evidence: dict[str, Any] | None = None
    controller_snapshot: dict[str, Any] | None = None
    run_name: str | None = None
    dataset: str | None = None
    recorded_at: datetime = field(default_factory=datetime.now)
    edge_id: int | None = None


@dataclass
class IdentityEvent:
    entity_id: str
    kind: str
    payload: dict[str, Any] | None = None
    run_name: str | None = None
    dataset: str | None = None
    recorded_at: datetime = field(default_factory=datetime.now)
    event_id: int | None = None


@dataclass
class IdentityAlias:
    alias: str
    entity_id: str
    kind: str = "external_id"
    dataset: str | None = None
    recorded_at: datetime = field(default_factory=datetime.now)


def canon_record_pair(a: str, b: str) -> tuple[str, str]:
    """Canonicalize record pair ordering to (min, max) lexicographically."""
    return (a, b) if a <= b else (b, a)
