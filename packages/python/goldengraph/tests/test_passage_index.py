"""PassageIndex: the zero-config passage store that makes `ask(mode="hybrid")` work
out of the box (box-safe, numpy ANN fallback). Embed-once at build, retrieve top-k
texts per query, and -- unlike EntityIndex -- HOLD the embedder so `retrieve(query, k)`
matches the `passages` protocol `ask` consumes."""
from __future__ import annotations

import goldenmatch.core.ann_blocker as _ab
import numpy as np
import pytest
from goldengraph.passage_index import PassageIndex


@pytest.fixture(autouse=True)
def _force_numpy_fallback(monkeypatch):
    # Hermetic: never depend on faiss being installed; numpy fallback gives the same neighbor set.
    monkeypatch.setattr(_ab, "_HAS_FAISS", False)


_VECS = {"apple": [1.0, 0.0, 0.0], "banana": [0.0, 1.0, 0.0], "cherry": [0.0, 0.0, 1.0]}


class _StubEmbedder:
    """Deterministic: a passage/query maps to an axis vector keyed by its FIRST known word,
    unknown -> zero. Counts embed() calls so we can assert embed-once."""

    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        out = []
        for t in texts:
            vec = [0.0, 0.0, 0.0]
            for w in str(t).lower().split():
                if w in _VECS:
                    vec = _VECS[w]
                    break
            out.append(vec)
        return np.array(out, dtype=float)


_IDS = ["d1", "d2", "d3"]
_TEXTS = ["apple pie recipe", "banana bread", "cherry cobbler"]


# --- build + retrieve ---------------------------------------------------------------------------------
def test_build_and_retrieve_topk():
    idx = PassageIndex.build(_IDS, _TEXTS, _StubEmbedder())
    assert idx.retrieve("banana", k=1) == ["banana bread"]
    assert idx.retrieve("apple", k=1) == ["apple pie recipe"]


def test_retrieve_returns_texts_not_ids():
    idx = PassageIndex.build(_IDS, _TEXTS, _StubEmbedder())
    assert idx.retrieve("cherry", k=1) == ["cherry cobbler"]


def test_build_drops_empty_passages_and_stays_aligned():
    ids = ["d1", "d2", "d3"]
    texts = ["apple pie", "   ", "cherry cobbler"]
    idx = PassageIndex.build(ids, texts, _StubEmbedder())
    assert len(idx) == 2                       # the whitespace passage was dropped
    assert idx.retrieve("cherry", k=1) == ["cherry cobbler"]
    assert idx.retrieve("apple", k=1) == ["apple pie"]


def test_retrieve_embeds_query_only():
    idx = PassageIndex.build(_IDS, _TEXTS, _StubEmbedder())
    emb = idx._embedder
    calls_after_build = emb.calls          # one batched embed of the corpus at build
    idx.retrieve("apple", k=1)
    assert emb.calls == calls_after_build + 1   # ONE embed (the query), NOT N -- the anti-regression
    idx.retrieve("banana", k=1)
    assert emb.calls == calls_after_build + 2


def test_retrieve_k_above_capacity_is_clamped_not_raised():
    # A retriever inside ask() must degrade, never raise. k>top_k returns the whole (small) index.
    idx = PassageIndex.build(_IDS, _TEXTS, _StubEmbedder(), top_k=50)
    got = idx.retrieve("apple", k=100)
    assert isinstance(got, list) and len(got) <= 3


def test_empty_index_returns_empty():
    idx = PassageIndex.build([], [], _StubEmbedder())
    assert len(idx) == 0 and idx.retrieve("apple", k=5) == []


# --- save / load --------------------------------------------------------------------------------------
def test_save_load_roundtrip(tmp_path):
    idx = PassageIndex.build(_IDS, _TEXTS, _StubEmbedder())
    idx.save(str(tmp_path / "idx"))
    loaded = PassageIndex.load(str(tmp_path / "idx"), _StubEmbedder())
    assert len(loaded) == 3
    assert loaded.retrieve("cherry", k=1) == ["cherry cobbler"]


# --- ask(mode="hybrid") seam --------------------------------------------------------------------------
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


def test_passage_index_is_a_valid_ask_hybrid_retriever(monkeypatch):
    # The whole point of A: a library caller builds a PassageIndex and hands it straight
    # to ask(mode="hybrid"); the retrieved passage text reaches synthesis.
    from conftest import RecordingLLM, StubEmbedder
    from goldengraph.answer import ask

    # A one-hot embedder covering both the graph names AND the passage words, so seeding
    # and passage retrieval are both deterministic.
    vocab = {"Start": 0, "A": 1, "Zeta": 2, "acquired": 3}
    graph_emb = StubEmbedder(vocab)
    passages = PassageIndex.build(
        ["p1", "p2"],
        ["Start works at A.", "A acquired Zeta in 1990."],
        StubEmbedder({"acquired": 3, "Zeta": 2, "A": 1, "Start": 0}),
    )
    llm = RecordingLLM()
    store = _FakeStore(_FakeGraph(_NAMES, _EDGES))
    ask(
        "acquired", store, llm=llm, embedder=graph_emb, valid_t=1, tx_t=1,
        mode="hybrid", k=1, hops=4, node_budget=64, passages=passages, passage_k=5,
    )
    prompt = llm.prompts[-1]
    assert "Passages:" in prompt                      # hybrid path ran (passages present)
    assert "A acquired Zeta in 1990." in prompt        # the retrieved passage reached synthesis
