"""Real neo4j-graphrag resolvers as benchmark adapters (in-process)."""
from __future__ import annotations

from erkgbench.adapters.base import Record
from erkgbench.real_resolvers import (
    neo4j_graphrag_exact_clusters,
    neo4j_graphrag_fuzzy_clusters,
)


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


class RealNeo4jGraphRAGExact:
    name = "neo4j-graphrag(exact)"
    defaults = (
        "SinglePropertyExactMatchResolver: exact `name` equality per entity-label, "
        "null names skipped (logic is a Cypher query in run(); no in-process decision "
        "method exists, so this is the Cypher re-expressed + confirmed -> validated)"
    )
    deterministic = True
    # NOT real-inproc: unlike FuzzyMatchResolver (compute_similarity/_consolidate_sets),
    # SinglePropertyExactMatchResolver has NO callable Python decision code -- its rule
    # lives entirely in a Cypher query. We re-express that query and confirm it against
    # source (see adapters/FIDELITY.md), which is the `validated` tier, not a real run.
    fidelity = "validated"

    def resolve(self, records: list[Record]) -> list[list[int]]:
        items = [(r.index, r.mention, r.entity_type) for r in records]
        return neo4j_graphrag_exact_clusters(items)
