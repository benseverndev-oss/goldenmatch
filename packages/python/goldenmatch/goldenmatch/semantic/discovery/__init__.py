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
Phase 5: `discover_semantic_model` — the orchestrator assembling all phases into a
    draft model, emitted + re-certified end-to-end.
Phase 7 (optional): `name_semantic_model` — advisory, self-verified LLM business
    naming, never authoritative (structural discovery is byte-deterministic without it).
"""
from __future__ import annotations

from goldenmatch.semantic.discovery.entities import EntityType, discover_entity_types
from goldenmatch.semantic.discovery.hierarchies import Hierarchy, discover_hierarchies
from goldenmatch.semantic.discovery.joins import JoinCandidate, discover_joins
from goldenmatch.semantic.discovery.keys import KeyCandidate, discover_keys
from goldenmatch.semantic.discovery.measures import (
    Dimension,
    Measure,
    TableMeasures,
    discover_measures,
)
from goldenmatch.semantic.discovery.metrics import Metric, discover_metrics
from goldenmatch.semantic.discovery.model import (
    ProposedModel,
    ProposedTable,
    discover_semantic_model,
)
from goldenmatch.semantic.discovery.namer import (
    NamerBackend,
    NameSuggestion,
    apply_names,
    name_semantic_model,
)

__all__ = [
    "Dimension",
    "EntityType",
    "Hierarchy",
    "JoinCandidate",
    "KeyCandidate",
    "Measure",
    "Metric",
    "NameSuggestion",
    "NamerBackend",
    "ProposedModel",
    "ProposedTable",
    "TableMeasures",
    "discover_entity_types",
    "discover_hierarchies",
    "discover_joins",
    "discover_keys",
    "discover_measures",
    "discover_metrics",
    "discover_semantic_model",
    "apply_names",
    "name_semantic_model",
]
