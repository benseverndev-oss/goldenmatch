"""Semantic-model discovery (the generative half of the semantic wedge).

Propose a semantic model from source tables where every key comes PRE-GRADED by the
certifier. Discovery is hypothesis generation; `certify_key_integrity` /
`certify_cube_joins` are the falsification test — so the proposed model is proven
against the data, not just guessed.

Design + phased plan:
`docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`.

Phase 1: `discover_keys` — certified single-column key discovery per table.
Phase 2: `discover_entity_types` — cross-table entity-type discovery.
Phase 3: `discover_joins` — certified foreign-key join discovery across tables.
Phase 4: `discover_measures` — grain-gated measure/dimension proposal per table.
Later phases (the `discover_semantic_model` orchestrator) build on these.
"""
from __future__ import annotations

from goldenmatch.semantic.discovery.entities import EntityType, discover_entity_types
from goldenmatch.semantic.discovery.joins import JoinCandidate, discover_joins
from goldenmatch.semantic.discovery.keys import KeyCandidate, discover_keys
from goldenmatch.semantic.discovery.measures import (
    Dimension,
    Measure,
    TableMeasures,
    discover_measures,
)

__all__ = [
    "Dimension",
    "EntityType",
    "JoinCandidate",
    "KeyCandidate",
    "Measure",
    "TableMeasures",
    "discover_entity_types",
    "discover_joins",
    "discover_keys",
    "discover_measures",
]
