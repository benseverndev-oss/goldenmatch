"""Relation-guided multi-hop retrieval: router chain-routing + trace_chain walk (wheel-free)."""
from __future__ import annotations

from goldengraph.answer import _rel_match, trace_chain
from goldengraph.route import QueryIntent, classify_query, plan_query

_Q = ("Starting from reranking, follow the relation acquired, then part of. "
      "What entity do you reach? Give its canonical name.")


def test_router_extracts_chain_and_routes_to_chain():
    p = classify_query(_Q)
    assert p.intent is QueryIntent.MULTI_HOP
    assert p.anchor_surface == "reranking"
    assert p.relation_chain == ("acquired", "part of")
    assert p.confidence >= 0.8
    assert plan_query(p).mode == "chain"


def test_single_relation_chain():
    p = classify_query("Starting from X, follow the relation works at. What entity do you reach?")
    assert p.anchor_surface == "X" and p.relation_chain == ("works at",)
    assert plan_query(p).mode == "chain"


def test_rel_match_lenient():
    assert _rel_match("works_at", "works at")        # underscore <-> space
    assert _rel_match("was acquired by", "acquired")  # substring
    assert not _rel_match("located in", "acquired")


class _StubGraph:
    def __init__(self, entities, edges):
        self._ents = entities
        self._edges = edges
        self._byname: dict = {}
        for e in entities:
            self._byname.setdefault(e["canonical_name"], []).append(e["entity_id"])

    def seeds_by_name(self, name):
        return list(self._byname.get(name, []))

    def query(self, ids, hops):
        ids = set(ids)
        edges = [e for e in self._edges if e["subj"] in ids or e["obj"] in ids]
        keep = ids | {e["subj"] for e in edges} | {e["obj"] for e in edges}
        return {"entities": [e for e in self._ents if e["entity_id"] in keep], "edges": edges}


def _graph():
    ents = [{"entity_id": i, "canonical_name": n} for i, n in
            enumerate(["A", "B", "C", "Z"])]
    edges = [
        {"subj": 0, "predicate": "acquired", "obj": 1},   # A -acquired-> B
        {"subj": 1, "predicate": "part_of", "obj": 2},    # B -part_of-> C
        {"subj": 0, "predicate": "works_at", "obj": 3},   # A -works_at-> Z (distractor)
    ]
    return _StubGraph(ents, edges)


def test_trace_chain_walks_to_answer():
    # A -acquired-> B -part of-> C ; lenient predicate match (part_of vs "part of")
    assert trace_chain(_graph(), "A", ("acquired", "part of")) == "C"


def test_trace_chain_single_hop():
    assert trace_chain(_graph(), "A", ("acquired",)) == "B"


def test_trace_chain_missing_edge_returns_none():
    assert trace_chain(_graph(), "A", ("acquired", "located in")) is None  # no such 2nd edge


def test_trace_chain_unknown_anchor():
    assert trace_chain(_graph(), "Nope", ("acquired",)) is None
