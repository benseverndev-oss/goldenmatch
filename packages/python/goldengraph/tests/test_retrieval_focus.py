"""Relation-aware retrieval focus: the precision pass on top of adaptive depth.

A wide ball reaches multi-hop answers but drags in distractor branches that bury the
answer chain (the 2026-06-22 probe: hops=4 recovered 2-hop 0.03->0.12 but the whole
-graph blob slowed it and dinged 1-hop). Focusing the retrieved subgraph to the
relations the query NAMED strips those distractors while keeping the chain.

Native-free + LLM-free: a path graph with extra branches + a RecordingLLM. The query
states two relations; focus must keep the 2-hop chain and drop everything else, and
fall back to the full ball when the query names no relation.
"""

from __future__ import annotations

from goldengraph.answer import ask
from conftest import RecordingLLM, StubEmbedder

# Answer chain: Start -works_at-> Mid -located_in-> Zeta   (the 2-hop answer is Zeta)
# Distractors hanging off the same nodes via OTHER predicates:
#   Start -founded_by-> NoiseA ;  Mid -acquired-> NoiseB
_NAMES = ["Start", "Mid", "Zeta", "NoiseA", "NoiseB"]
_EDGES = [
    (0, "works_at", 1),
    (1, "located_in", 2),
    (0, "founded_by", 3),
    (1, "acquired", 4),
]


class _FakeGraph:
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


def _ask(query):
    llm = RecordingLLM()
    store = _FakeStore(_FakeGraph(_NAMES, _EDGES))
    embedder = StubEmbedder(
        {"Start": 0, "Mid": 1, "Zeta": 2, "NoiseA": 3, "NoiseB": 4}
    )
    ask(
        query,
        store,
        llm=llm,
        embedder=embedder,
        valid_t=1,
        tx_t=1,
        mode="local",
        k=1,
        hops=4,
    )
    return llm.prompts[-1]


def test_focus_keeps_chain_and_strips_distractors():
    # the question names works_at + located_in -> focus to that chain
    prompt = _ask("Starting from Start, follow works at, then located in. Which entity?")
    # answer chain is present ...
    assert "Zeta" in prompt and "works_at" in prompt and "located_in" in prompt
    assert "Mid" in prompt
    # ... and the distractor branches reachable in the ball are gone
    assert "NoiseA" not in prompt
    assert "NoiseB" not in prompt
    assert "founded_by" not in prompt
    assert "acquired" not in prompt


def test_falls_back_to_full_ball_when_no_relation_named():
    # a query that names no graph relation -> keep the full neighborhood (the distractor
    # edges reappear), so focus never strands a non-path question
    prompt = _ask("Tell me about Start")
    assert "NoiseA" in prompt or "founded_by" in prompt
