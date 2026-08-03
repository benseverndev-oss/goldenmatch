"""Semantic-model discovery (the generative half of the semantic wedge).

Propose a semantic model from source tables where every key comes PRE-GRADED by the
certifier. Discovery is hypothesis generation; `certify_key_integrity` /
`certify_cube_joins` are the falsification test — so the proposed model is proven
against the data, not just guessed.

Design + phased plan:
`docs/superpowers/specs/2026-08-03-semantic-model-discovery-design.md`.

Phase 1 (this slice): `discover_keys` — certified single-column key discovery per
table. Later phases (entity typing, join discovery, measure/dimension proposal, the
`discover_semantic_model` orchestrator) build on this.
"""
from __future__ import annotations

from goldenmatch.semantic.discovery.keys import KeyCandidate, discover_keys

__all__ = ["KeyCandidate", "discover_keys"]
