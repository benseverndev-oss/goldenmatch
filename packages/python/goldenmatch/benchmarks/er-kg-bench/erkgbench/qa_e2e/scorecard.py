"""Per-stage scorecard metrics.

bridge_recall = does the resolved + retrieved subgraph let you WALK the gold chain
end to end? This is the `(ER_accuracy)^hops` thesis measured at the RETRIEVAL layer
-- no LLM. Under-merge (a bridge entity split across resolved nodes) strands the
walk; perfect resolution keeps it whole.
"""
from __future__ import annotations


def _nodes_covering(coverage: dict, canon: str) -> set:
    return {nid for nid, cset in coverage.items() if canon in cset}


def bridge_recall(gold_chain, subgraph: dict, coverage: dict) -> dict:
    """gold_chain: [(src_canon, rel, dst_canon), ...]. coverage: store entity_id ->
    set(canonical_ids it carries). Returns {"whole_chain": 0/1, "edge_recall": frac}.

    Walk: start from the store nodes covering the first src canonical; for each gold
    edge, can we step (via a subgraph edge -- predicate ignored, gold predicates come
    from the same triples) from a carried node to a node covering the next canonical?
    Carry that node set forward. An edge that strands (no such step) ends the walk;
    remaining edges are unreachable. Edges are undirected (synthesis walks either
    way)."""
    if not gold_chain:
        return {"whole_chain": 1.0, "edge_recall": 0.0}
    adj: dict = {}
    for e in subgraph.get("edges", ()):
        adj.setdefault(e["subj"], set()).add(e["obj"])
        adj.setdefault(e["obj"], set()).add(e["subj"])
    carried = _nodes_covering(coverage, gold_chain[0][0])
    edges_hit = 0
    for (_src, _rel, dst) in gold_chain:
        targets = _nodes_covering(coverage, dst)
        reachable = {t for c in carried for t in adj.get(c, ()) if t in targets}
        if not reachable:
            break
        edges_hit += 1
        carried = reachable
    return {
        "whole_chain": 1.0 if edges_hit == len(gold_chain) else 0.0,
        "edge_recall": edges_hit / len(gold_chain),
    }
