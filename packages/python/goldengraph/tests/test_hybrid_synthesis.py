"""Hybrid retrieval: passages (ground truth) + graph subgraph (multi-hop map).

The bench showed the triple-only KG is a LOSSY intermediate -- it lost to plain
paragraph RAG. `mode="hybrid"` layers the raw passages back into synthesis while
keeping the graph for cross-passage bridging, and frees the answer from the
entity-only constraint. These pure tests (no native, no LLM, no network) pin the
contract: synthesis sees BOTH passages and the name-keyed graph, the passages
retriever is consulted with `passage_k`, and the answer instruction is free-form.
"""

from __future__ import annotations

import pytest

from goldengraph.answer import ask
from goldengraph.synthesize import synthesize_hybrid
from conftest import RecordingLLM, StubEmbedder

_SUB = {
    "entities": [
        {"entity_id": 0, "canonical_name": "Acme", "typ": "org"},
        {"entity_id": 1, "canonical_name": "Rocket", "typ": "product"},
    ],
    "edges": [{"subj": 0, "predicate": "made", "obj": 1}],
}


def test_hybrid_prompt_carries_both_passages_and_graph():
    llm = RecordingLLM()
    synthesize_hybrid(
        "who made the rocket?",
        _SUB,
        ["Acme built the famous Rocket in 1958.", "Unrelated sky passage."],
        llm,
        seed_names=["Acme"],
    )
    prompt = llm.prompts[-1]
    # passages present (ground-truth context)...
    assert "Acme built the famous Rocket in 1958." in prompt
    assert "Passages:" in prompt
    # ...AND the name-keyed graph edge (the cross-passage multi-hop map)
    assert "Acme -[made]-> Rocket" in prompt
    assert "Anchor entities (most query-relevant): Acme" in prompt
    assert "Answer:" in prompt


def test_hybrid_answer_is_free_form_not_entity_only():
    # The hybrid path must NOT inherit synthesize_local's entity-only clause -- the
    # passages can carry date/number/phrase answers the triples drop.
    llm = RecordingLLM()
    synthesize_hybrid("q?", _SUB, ["p"], llm)
    prompt = llm.prompts[-1]
    assert "date, number, or short phrase" in prompt
    assert "ALWAYS a single entity" not in prompt  # the local-mode constraint is absent


def test_hybrid_parses_answer_line():
    llm = RecordingLLM("hop one\nhop two\nAnswer: Acme")
    assert synthesize_hybrid("q?", _SUB, ["p"], llm) == "Acme"


def test_hybrid_empty_passages_is_safe():
    llm = RecordingLLM()
    synthesize_hybrid("q?", _SUB, [], llm)
    assert "(no passages retrieved)" in llm.prompts[-1]


# --- ask(mode="hybrid") integration over a fake store ---

_NAMES = ["Start", "A", "Zeta"]
_EDGES = [(0, "works_at", 1), (1, "acquired", 2)]


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
        seen, frontier = set(seeds), set(seeds)
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


class _FakePassages:
    def __init__(self, texts):
        self._texts = texts
        self.calls: list[tuple[str, int]] = []

    def retrieve(self, query: str, k: int) -> list[str]:
        self.calls.append((query, k))
        return list(self._texts)[:k]


def test_ask_hybrid_feeds_passages_and_graph_to_synthesis():
    llm = RecordingLLM()
    store = _FakeStore(_FakeGraph(_NAMES, _EDGES))
    embedder = StubEmbedder({"Start": 0, "A": 1, "Zeta": 2})
    passages = _FakePassages(["Start works at A.", "A acquired Zeta in 1990."])
    ask(
        "Start",
        store,
        llm=llm,
        embedder=embedder,
        valid_t=1,
        tx_t=1,
        mode="hybrid",
        k=1,
        hops=4,
        node_budget=64,
        passages=passages,
        passage_k=7,
    )
    prompt = llm.prompts[-1]
    # the passage retriever was consulted with passage_k (not the graph hops/k)
    assert passages.calls == [("Start", 7)]
    # both halves reach synthesis
    assert "A acquired Zeta in 1990." in prompt
    assert "Start -[works_at]-> A" in prompt
    assert "acquired" in prompt


def test_ask_hybrid_without_retriever_degrades_to_graph_only():
    llm = RecordingLLM()
    store = _FakeStore(_FakeGraph(_NAMES, _EDGES))
    embedder = StubEmbedder({"Start": 0, "A": 1, "Zeta": 2})
    ask(
        "Start", store, llm=llm, embedder=embedder, valid_t=1, tx_t=1,
        mode="hybrid", k=1, hops=4, node_budget=64, passages=None,
    )
    assert "(no passages retrieved)" in llm.prompts[-1]


def test_ask_rejects_unknown_mode():
    store = _FakeStore(_FakeGraph(_NAMES, _EDGES))
    with pytest.raises(ValueError, match="local"):
        ask(
            "Start", store, llm=RecordingLLM(), embedder=StubEmbedder({"Start": 0}),
            valid_t=1, tx_t=1, mode="bogus",
        )
