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


def test_ask_hybrid_without_retriever_falls_back_to_local():
    # DEFAULT-FLIP SAFETY (2026-07-22): hybrid is now the DEFAULT mode, and its win IS
    # the passages. With NO passage retriever there is nothing to layer in, so hybrid
    # falls through to the LOCAL synthesis path -- byte-identical to the prior local
    # default for passage-less callers (NOT the old free-form "(no passages retrieved)"
    # graph-only degrade). The prompt is the local entity-answer prompt, not hybrid's.
    llm = RecordingLLM()
    store = _FakeStore(_FakeGraph(_NAMES, _EDGES))
    embedder = StubEmbedder({"Start": 0, "A": 1, "Zeta": 2})
    ask(
        "Start", store, llm=llm, embedder=embedder, valid_t=1, tx_t=1,
        mode="hybrid", k=1, hops=4, node_budget=64, passages=None,
    )
    prompt = llm.prompts[-1]
    assert "(no passages retrieved)" not in prompt  # NOT the hybrid free-form path
    assert "Passages:" not in prompt  # no hybrid passage block
    # the local entity-answer clause is what ran
    assert "single entity that appears in the Entities list" in prompt


def test_ask_rejects_unknown_mode():
    store = _FakeStore(_FakeGraph(_NAMES, _EDGES))
    with pytest.raises(ValueError, match="local"):
        ask(
            "Start", store, llm=RecordingLLM(), embedder=StubEmbedder({"Start": 0}),
            valid_t=1, tx_t=1, mode="bogus",
        )


# --- ask(mode="hybrid") path-filter (GOLDENGRAPH_HYBRID_FILTER), default off ---


def test_ask_hybrid_filter_path_prunes_offtopic_from_synthesis(monkeypatch):
    # Graph: Start(0)-works_at->A(1)-acquired->Zeta(2) chain + off-topic leaf
    # Noise(3) hanging off ZETA (a NON-seed node), NOT off a seed -- so it sits
    # outside every seed's 1-hop halo and the filter drops it. (If Noise hung off
    # the seed A instead, halo=1 would legitimately KEEP it -- see review note.)
    names = ["Start", "A", "Zeta", "Noise"]
    edges = [(0, "works_at", 1), (1, "acquired", 2), (2, "mentions", 3)]
    llm = RecordingLLM()
    store = _FakeStore(_FakeGraph(names, edges))
    embedder = StubEmbedder({"Start": 0, "A": 1, "Zeta": 2, "Noise": 3})
    passages = _FakePassages(["Start works at A.", "A acquired Zeta in 1990."])
    monkeypatch.setenv("GOLDENGRAPH_HYBRID_FILTER", "path")
    ask(
        "Start", store, llm=llm, embedder=embedder, valid_t=1, tx_t=1,
        mode="hybrid", k=2, hops=4, node_budget=64,
        passages=passages, passage_k=7,
    )
    prompt = llm.prompts[-1]
    # seeds = top-2 by cosine = Start(0), A(1) (one-hot). Production default halo=1:
    # path 0-1 keeps {Start,A}; A's halo keeps Zeta(2); Noise(3) hangs off the
    # non-seed Zeta, beyond any seed's 1-hop halo -> DROPPED.
    assert "Start -[works_at]-> A" in prompt
    assert "Noise" not in prompt
    # passages are untouched by the filter (ground-truth context stays whole)
    assert "A acquired Zeta in 1990." in prompt


def test_ask_hybrid_filter_off_keeps_full_ball(monkeypatch):
    names = ["Start", "A", "Zeta", "Noise"]
    edges = [(0, "works_at", 1), (1, "acquired", 2), (2, "mentions", 3)]
    llm = RecordingLLM()
    store = _FakeStore(_FakeGraph(names, edges))
    embedder = StubEmbedder({"Start": 0, "A": 1, "Zeta": 2, "Noise": 3})
    monkeypatch.delenv("GOLDENGRAPH_HYBRID_FILTER", raising=False)
    ask(
        "Start", store, llm=llm, embedder=embedder, valid_t=1, tx_t=1,
        mode="hybrid", k=2, hops=4, node_budget=64, passages=None,
    )
    # control: with the flag off the off-topic leaf is still present (current 0.420)
    assert "Noise" in llm.prompts[-1]


def test_ask_hybrid_filter_rerank_prunes_ball_to_budget(monkeypatch):
    # Start(0)-works_at->A(1)-acquired->Zeta(2)-mentions->Noise(3). Seed = Start(0).
    # TOPK=2: seed-incident {works_at} always kept; budget 1 fills with the next edge
    # in original order (acquired); the tail (mentions->Noise) is pruned out of synthesis.
    names = ["Start", "A", "Zeta", "Noise"]
    edges = [(0, "works_at", 1), (1, "acquired", 2), (2, "mentions", 3)]
    llm = RecordingLLM()
    store = _FakeStore(_FakeGraph(names, edges))
    embedder = StubEmbedder({"Start": 0, "A": 1, "Zeta": 2, "Noise": 3})
    passages = _FakePassages(["Start works at A.", "A acquired Zeta in 1990."])
    monkeypatch.setenv("GOLDENGRAPH_HYBRID_FILTER", "rerank")
    monkeypatch.setenv("GOLDENGRAPH_HYBRID_FILTER_TOPK", "2")
    ask(
        "Start", store, llm=llm, embedder=embedder, valid_t=1, tx_t=1,
        mode="hybrid", k=1, hops=4, node_budget=64,
        passages=passages, passage_k=7,
    )
    prompt = llm.prompts[-1]
    assert "Start -[works_at]-> A" in prompt   # seed-incident edge always kept
    assert "Noise" not in prompt               # pruned beyond the top-K budget
    # passages are untouched by the filter (ground-truth context stays whole)
    assert "A acquired Zeta in 1990." in prompt


def test_ask_local_mode_ignores_filter_flag(monkeypatch):
    # The filter must touch hybrid ONLY -- local stays byte-identical.
    names = ["Start", "A", "Zeta", "Noise"]
    edges = [(0, "works_at", 1), (1, "acquired", 2), (2, "mentions", 3)]
    llm = RecordingLLM()
    store = _FakeStore(_FakeGraph(names, edges))
    embedder = StubEmbedder({"Start": 0, "A": 1, "Zeta": 2, "Noise": 3})
    monkeypatch.setenv("GOLDENGRAPH_HYBRID_FILTER", "path")
    ask(
        "Start", store, llm=llm, embedder=embedder, valid_t=1, tx_t=1,
        mode="local", k=2, hops=4, node_budget=64,
    )
    assert "Noise" in llm.prompts[-1]  # local ball is unfiltered regardless of flag


def test_ask_default_mode_is_hybrid():
    # Ship 2026-07-22: hybrid is the DEFAULT answer mode (measured +169% am / +143%
    # judge over local on the same graph). Passage-less callers fall back to local
    # (test above), so the flip is safe by default.
    import inspect

    assert inspect.signature(ask).parameters["mode"].default == "hybrid"
