"""bridge_recall: does the resolved+retrieved subgraph let you WALK the gold chain
end to end. Pure -- operates on a subgraph dict + a store-entity coverage map, no
store/LLM/network."""
from __future__ import annotations

from erkgbench.qa_e2e.scorecard import bridge_recall

_CHAIN = [("c0", "works_at", "c1"), ("c1", "acquired", "c2")]  # 2-hop gold chain


def _sub(edges):
    ents = sorted({e for pair in edges for e in (pair[0], pair[2])})
    return {
        "entities": [{"entity_id": e} for e in ents],
        "edges": [{"subj": s, "predicate": p, "obj": o} for (s, p, o) in edges],
    }


def test_full_chain_present_recall_one():
    sub = _sub([(0, "works_at", 1), (1, "acquired", 2)])
    cov = {0: {"c0"}, 1: {"c1"}, 2: {"c2"}}
    r = bridge_recall(_CHAIN, sub, cov)
    assert r["whole_chain"] == 1.0 and r["edge_recall"] == 1.0


def test_undermerged_bridge_breaks_walk():
    # c1's mentions split: node 1 carries c1 (reached from c0) but the c1->c2 edge
    # was authored from node 3 (the other c1 surface). Walk cannot continue.
    sub = _sub([(0, "works_at", 1), (3, "acquired", 2)])
    cov = {0: {"c0"}, 1: {"c1"}, 3: {"c1"}, 2: {"c2"}}
    r = bridge_recall(_CHAIN, sub, cov)
    assert r["whole_chain"] == 0.0
    assert r["edge_recall"] == 0.5  # first edge reachable, second not from carried node


def test_missing_from_ball_zero():
    sub = _sub([(0, "works_at", 1)])  # second edge absent entirely
    cov = {0: {"c0"}, 1: {"c1"}}
    r = bridge_recall(_CHAIN, sub, cov)
    assert r["whole_chain"] == 0.0
    assert r["edge_recall"] == 0.5
