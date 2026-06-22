"""Local-retrieval depth: the fix for the 2026-06-22 head-to-head 1->2 hop
answer-match cliff.

A single fixed-depth ball (`query(seeds, 1)`) cannot contain a k-hop answer, so
synthesis never sees it. These tests pin the contract WITHOUT native or an LLM: a
pure-Python path graph + a RecordingLLM (asserts WHAT synthesis was given). They
reproduce the cliff (shallow retrieval misses the answer) and prove the fix
(adaptive expansion reaches it, the node budget bounds it).
"""

from __future__ import annotations

from goldengraph.answer import ask
from conftest import RecordingLLM, StubEmbedder

# A 4-hop path: Start -works_at-> A -located_in-> B -part_of-> C -acquired-> Zeta
_NAMES = ["Start", "A", "B", "C", "Zeta"]
_EDGES = [
    (0, "works_at", 1),
    (1, "located_in", 2),
    (2, "part_of", 3),
    (3, "acquired", 4),
]


class _FakePathGraph:
    """Implements the slice-graph surface `ask` touches: entities() + query(seeds,
    hops) returning the BFS-ball induced subgraph (undirected reachability, directed
    edges reported)."""

    def __init__(self, names, edges):
        self._names = names
        self._edges = edges
        self._adj: dict[int, set[int]] = {i: set() for i in range(len(names))}
        for s, _p, o in edges:
            self._adj[s].add(o)
            self._adj[o].add(s)

    def _ent(self, i):
        return {"entity_id": i, "canonical_name": self._names[i], "typ": "concept"}

    def entities(self):
        return [self._ent(i) for i in range(len(self._names))]

    def query(self, seeds, hops):
        seen = set(seeds)
        frontier = set(seeds)
        for _ in range(hops):
            nxt: set[int] = set()
            for u in frontier:
                nxt |= self._adj[u]
            frontier = nxt - seen
            seen |= nxt
        ents = [self._ent(i) for i in sorted(seen)]
        edges = [
            {"subj": s, "predicate": p, "obj": o}
            for (s, p, o) in self._edges
            if s in seen and o in seen
        ]
        return {"entities": ents, "edges": edges}


class _FakeStore:
    def __init__(self, graph):
        self._graph = graph

    def as_of(self, valid_t, tx_t):  # noqa: ARG002 - single static slice
        return self._graph


def _ask(hops, node_budget=64):
    llm = RecordingLLM()
    store = _FakeStore(_FakePathGraph(_NAMES, _EDGES))
    # one-hot embedder: the query "Start" seeds exactly the Start entity
    embedder = StubEmbedder({"Start": 0, "A": 1, "B": 2, "C": 3, "Zeta": 4})
    ask(
        "Start",
        store,
        llm=llm,
        embedder=embedder,
        valid_t=1,
        tx_t=1,
        mode="local",
        k=1,
        hops=hops,
        node_budget=node_budget,
    )
    return llm.prompts[-1]


def test_shallow_retrieval_misses_the_multihop_answer():
    # hops=1: only Start + A reachable; the answer edge (C -acquired-> Zeta) is absent
    prompt = _ask(hops=1)
    assert "acquired" not in prompt
    assert "Zeta" not in prompt


def test_adaptive_expansion_reaches_the_4hop_answer():
    # hops=4: the full chain, including the terminal answer edge, is retrieved
    prompt = _ask(hops=4)
    assert "acquired" in prompt
    assert "Zeta" in prompt
    # every hop along the path is present
    for pred in ("works_at", "located_in", "part_of", "acquired"):
        assert pred in prompt


def test_node_budget_bounds_expansion():
    # a tiny budget stops the walk early, so the deep answer stays out of the prompt
    prompt = _ask(hops=4, node_budget=2)
    assert "Zeta" not in prompt
