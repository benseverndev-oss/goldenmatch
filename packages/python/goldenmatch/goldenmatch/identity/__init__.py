"""Identity Graph -- durable, queryable graph of identities, source records,
match evidence, conflicts, and versioned changes over time.

See ``docs/superpowers/specs/2026-05-12-identity-graph-design.md``.
"""
from __future__ import annotations

from goldenmatch.identity.migrate_ids import MigrationReport, migrate_record_ids
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
from goldenmatch.identity.resolve import (
    ResolveSummary,
    match_record_to_entity,
    resolve_clusters,
    resolve_record_incremental,
)
from goldenmatch.identity.stabilize import (
    ConsolidationGroup,
    OverlapCandidate,
    StabilizeReport,
    entity_version,
    find_persistent_overlaps,
    stabilize_identities,
)
from goldenmatch.identity.stitching import (
    DEFAULT_CHANNEL_TRUST,
    DEFAULT_DEVICE_KEYS,
    StitchGroup,
    StitchResult,
    adjust_score,
    channel_trust,
    classify_channel,
    cross_channel_factor,
    deterministic_stitch_pairs,
    stitch_frame,
)
from goldenmatch.identity.store import IdentityStore, new_entity_id
from goldenmatch.identity.survivorship import (
    CellProvenance,
    FieldStrategyRecommendation,
    GoldenRecordWithProvenance,
    build_golden_with_provenance,
    learn_field_survivorship,
    learned_field_strategies,
)

__all__ = [
    "ConsolidationGroup",
    "OverlapCandidate",
    "StabilizeReport",
    "entity_version",
    "find_persistent_overlaps",
    "stabilize_identities",
    "IdentityView",
    "MigrationReport",
    "ResolveSummary",
    "find_by_record",
    "find_conflicts",
    "get_entity",
    "history",
    "list_entities",
    "manual_merge",
    "manual_split",
    "match_record_to_entity",
    "migrate_record_ids",
    "resolve_clusters",
    "resolve_record_incremental",
    "DEFAULT_CHANNEL_TRUST",
    "DEFAULT_DEVICE_KEYS",
    "StitchGroup",
    "StitchResult",
    "adjust_score",
    "channel_trust",
    "classify_channel",
    "cross_channel_factor",
    "deterministic_stitch_pairs",
    "stitch_frame",
    "CellProvenance",
    "FieldStrategyRecommendation",
    "GoldenRecordWithProvenance",
    "build_golden_with_provenance",
    "learn_field_survivorship",
    "learned_field_strategies",
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
