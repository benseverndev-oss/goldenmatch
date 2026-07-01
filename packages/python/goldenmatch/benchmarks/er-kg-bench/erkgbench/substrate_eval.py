"""Substrate-quality scoring over a BUILT graph (pure; operates on the graph dict + gold mentions)."""
from __future__ import annotations


def _base_doc_id(ref: str) -> str:
    """A source_ref may carry a `::N` co-occurrence suffix; the base doc id is `src::rel::dst` (3 parts).
    Re-join the first three `::`-separated parts (entity ids use a single `:`, so `::` is unambiguous)."""
    parts = ref.split("::")
    return "::".join(parts[:3]) if len(parts) >= 3 else ref


def align_mentions_to_nodes(graph: dict, gold_mentions: list[tuple[str, str, str]]) -> list[list[int]]:
    """Cluster gold-mention INDICES by the built node each landed in. Exact, doc-keyed (not surface):
    each engineered doc is ONE edge `src::rel::dst`; the built edge for that doc (matched by base doc id
    in `source_refs`) gives endpoints subj=src-node, obj=dst-node. Assumption: direction-canonicalization
    OFF (subj==src). Unmatched mention (no edge for its doc) -> its own singleton (extraction miss).

    KNOWN LIMIT (documented, not fixed in v1): if the resolver merges a single doc's src+dst (distinct
    entities) into one node, the build drops the self-loop -> no edge -> both mentions become singletons,
    mislabeling a within-doc over-merge as recall misses. Does not affect the ambiguity-driven (cross-doc,
    recall-side) headline."""
    # doc base id -> the edge (subj, obj). Prefer an exact base-id match.
    by_doc: dict[str, tuple[int, int]] = {}
    for e in graph.get("edges", ()):
        for ref in e.get("source_refs", ()):
            by_doc.setdefault(_base_doc_id(ref), (e["subj"], e["obj"]))
    node_of: dict[int, int] = {}   # mention index -> node id ; unmatched -> a fresh negative id
    fresh = -1
    for i, (entity_id, _surface, doc_id) in enumerate(gold_mentions):
        edge = by_doc.get(_base_doc_id(doc_id))
        if edge is None:
            node_of[i] = fresh
            fresh -= 1
            continue
        parts = doc_id.split("::")
        src_id, dst_id = parts[0], parts[2]
        node_of[i] = edge[0] if entity_id == src_id else edge[1] if entity_id == dst_id else (fresh)
        if node_of[i] == fresh:   # entity_id matched neither endpoint (shouldn't happen) -> unmatched
            fresh -= 1
    groups: dict[int, list[int]] = {}
    for i, node in node_of.items():
        groups.setdefault(node, []).append(i)
    return [sorted(v) for v in groups.values()]


def graph_coherence(graph: dict) -> dict:
    """Connected components of the built graph (edges undirected) + largest-component fraction. A
    coherent knowledge base is few components / one dominant; the construction ceiling shows as many
    small components."""
    nodes = {e["entity_id"] for e in graph.get("entities", ())}
    parent: dict[int, int] = {n: n for n in nodes}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for e in graph.get("edges", ()):
        parent[find(e["subj"])] = find(e["obj"])
    roots = [find(n) for n in parent]
    if not roots:
        return {"components": 0, "largest_fraction": 0.0}
    from collections import Counter
    sizes = Counter(roots)
    return {"components": len(sizes), "largest_fraction": max(sizes.values()) / len(roots)}


def provenance_coverage(graph: dict) -> float:
    """Fraction of edges carrying a non-empty `source_refs` (every fact traceable to a source). ~1.0 for
    goldengraph alone (it always stamps doc ids); discriminating in the multi-engine bake-off."""
    edges = list(graph.get("edges", ()))
    if not edges:
        return 1.0
    return sum(1 for e in edges if e.get("source_refs")) / len(edges)
