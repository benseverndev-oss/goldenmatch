"""Faithful exact-key reproductions of framework dedup rules (validated tier).

GraphRAG and Cognee both decide entity identity by an exact key with NO separable
resolver decision object to run in-process (GraphRAG: a `seen_titles` set + a
pandas `df.merge(on="title")` inside the LLM-driven pipeline; Cognee: a pure
`uuid5` collision). So unlike the neo4j-graphrag `*` rows (which run real library
decision code -> `real-inproc`), the honest tier here is `validated`: we reproduce
the merge KEY verbatim and cite source (the same posture as `RealNeo4jGraphRAGExact`,
whose rule is a Cypher query with no callable Python decision method).
"""
from __future__ import annotations

from erkgbench.adapters.base import Record
from erkgbench.real_resolvers import cognee_clusters, graphrag_clusters


class RealGraphRAG:
    name = "MS-GraphRAG"
    defaults = (
        "exact title-set merge, GLOBAL (not per-label): "
        "title=clean_str(name.upper()) -> upper + edge-strip + html-unescape + "
        "control-char strip, NO internal-ws collapse "
        "(finalize_entities seen_titles + graph_extractor + utils/string.py; "
        "Standard pipeline has no ER step -> validated reproduction, no separable resolver)"
    )
    deterministic = True
    # validated, NOT real-inproc: GraphRAG has no callable resolver decision object
    # -- the merge is `df.merge(on="title")` + a `seen_titles` set woven into the
    # LLM-driven Standard pipeline. We reproduce the title key verbatim + cite source
    # (FIDELITY.md), which is the `validated` bar (same as neo4j-graphrag(exact)).
    fidelity = "validated"

    def resolve(self, records: list[Record]) -> list[list[int]]:
        items = [(r.index, r.mention, r.entity_type) for r in records]
        return graphrag_clusters(items)


class RealCognee:
    name = "Cognee"
    defaults = (
        "exact merge on generate_node_id = "
        "uuid5(NAMESPACE_OID, name.lower().replace(' ','_').replace(\"'\",'')), "
        "GLOBAL (not per-label); default ontology empty so the difflib cutoff never "
        "fires (generate_node_id.py + deduplicate_nodes_and_edges.py -> validated)"
    )
    deterministic = True
    # validated: the decision is a pure stdlib uuid5 1-liner; reproducing it verbatim
    # + citing source is faithful (importing the heavy `cognee` package to call a
    # uuid5 buys zero fidelity). NOTE the real merge key is generate_node_ID, NOT the
    # generate_node_NAME display helper the Phase-1 model wrongly cited (they differ
    # in the `" " -> "_"` step). See FIDELITY.md.
    fidelity = "validated"

    def resolve(self, records: list[Record]) -> list[list[int]]:
        items = [(r.index, r.mention, r.entity_type) for r in records]
        return cognee_clusters(items)
