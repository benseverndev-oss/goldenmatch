"""Real neo4j-graphrag FuzzyMatchResolver as a benchmark adapter (in-process)."""
from __future__ import annotations

from erkgbench.adapters.base import Record
from erkgbench.real_resolvers import neo4j_graphrag_fuzzy_clusters


class RealNeo4jGraphRAGFuzzy:
    name = "neo4j-graphrag(fuzzy)*"
    defaults = (
        "REAL FuzzyMatchResolver: rapidfuzz WRatio/100>=0.8 per entity-label, "
        "_consolidate_sets (library decision code; Neo4j+APOC storage stubbed)"
    )
    deterministic = True
    fidelity = "real-inproc"

    def resolve(self, records: list[Record]) -> list[list[int]]:
        items = [(r.index, r.mention, r.entity_type) for r in records]
        return neo4j_graphrag_fuzzy_clusters(items)
