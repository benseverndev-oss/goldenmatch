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
