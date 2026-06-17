"""Graphiti real deterministic dedup floor as a benchmark adapter (in-process)."""
from __future__ import annotations

from erkgbench.adapters.base import Record
from erkgbench.real_resolvers import graphiti_clusters


class RealGraphiti:
    name = "graphiti*"
    defaults = (
        "REAL deterministic floor: exact normalized-name (lower+ws-collapse) OR "
        "MinHash/Jaccard>=0.9, with a low-entropy/short-name gate "
        "(_resolve_with_similarity + _build_candidate_indexes, dedup_helpers.py); "
        "sequential ingestion vs the growing existing set. DETERMINISTIC FLOOR ONLY "
        "-- the full default path escalates unresolved nodes to an LLM (out of scope)"
    )
    deterministic = True
    # real-inproc: Graphiti's real resolution DECISION code runs in-process. Honest
    # scope (FIDELITY.md): no LLM/embedder, so unresolved nodes become new entities
    # (the floor's end state); the full existing set is fed as candidates (the real
    # flow prunes via an embedder first -> this is an upper bound on the floor);
    # label-agnostic (no label gate). Only the LLM fallback + graph persistence are
    # elided. This row is the FLOOR, not Graphiti's full LLM-backed default.
    fidelity = "real-inproc"

    def resolve(self, records: list[Record]) -> list[list[int]]:
        items = [(r.index, r.mention, r.entity_type) for r in records]
        return graphiti_clusters(items)
