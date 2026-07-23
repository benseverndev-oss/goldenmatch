"""Pure tests for the gated top-K edge rerank of the hybrid ball (no native, no LLM,
no network). `rerank_subgraph_edges` prunes a huge, noisy ball to the top-K
question-relevant edges before synthesis, ALWAYS retaining seed-incident edges. Mode
`off` (env unset) leaves the ball byte-identical; a non-int TOPK falls back to 40."""

from __future__ import annotations

import numpy as np
from goldengraph import answer as answer_mod
from goldengraph.subgraph_filter import rerank_subgraph_edges


class _KeywordEmbedder:
    """Deterministic embedder: each text is a 2-d vector [has_kw, 1.0]. The question
    embeds to [1,0], so cosine similarity is highest for edge texts containing `kw`
    and lowest (but nonzero) for those without. Batched call, no per-edge round-trip."""

    def __init__(self, kw: str):
        self.kw = kw
        self.calls: list[list[str]] = []

    def embed(self, texts):
        self.calls.append(list(texts))
        rows = []
        for t in texts:
            if t == "__QUESTION__":
                rows.append([1.0, 0.0])
            else:
                rows.append([1.0 if self.kw in t else 0.0, 1.0])
        return np.asarray(rows, dtype=float)


def _ent(i, name=None):
    return {"entity_id": i, "canonical_name": name or f"n{i}", "typ": "concept"}


def _edge(s, o, pred="rel"):
    return {"subj": s, "predicate": pred, "obj": o}


def _sub(entities, edges):
    return {"entities": entities, "edges": edges}


# 1. Mode off (env unset) -> the ball is left unchanged (edges identical, order preserved).


def test_mode_off_is_identity_and_order_preserved(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_HYBRID_FILTER", raising=False)
    # off -> _hybrid_filter_mode() is "" so neither branch fires; assert the selector.
    assert answer_mod._hybrid_filter_mode() == ""
    # And the rerank no-ops when top_k covers every edge (see test 4), so a caller that
    # never enters the rerank branch keeps the ball byte-identical.
    edges = [_edge(0, 1), _edge(1, 2), _edge(2, 3)]
    sub = _sub([_ent(i) for i in range(4)], edges)
    out = rerank_subgraph_edges(sub, [0], question="q", embedder=_KeywordEmbedder("x"), top_k=99)
    assert out is sub
    assert [e for e in out["edges"]] == edges  # identical and in original order


# 2. A known subset is most-similar -> exactly those top-K (+ seed-incident) edges kept.


def test_top_k_keeps_highest_scoring_nonseed_edges():
    # 6 non-seed edges among nodes 10..; 3 carry the keyword "match" in their predicate.
    entities = [_ent(i) for i in range(0, 20)]
    edges = [
        _edge(10, 11, "match"),   # high
        _edge(11, 12, "noise"),   # low
        _edge(12, 13, "match"),   # high
        _edge(13, 14, "noise"),   # low
        _edge(14, 15, "match"),   # high
        _edge(15, 16, "noise"),   # low
    ]
    sub = _sub(entities, edges)
    emb = _KeywordEmbedder("match")
    # no seed-incident edges here (seed 99 touches nothing); budget = top_k = 3.
    out = rerank_subgraph_edges(sub, [99], question="__QUESTION__", embedder=emb, top_k=3)
    kept = {(e["subj"], e["obj"]) for e in out["edges"]}
    assert kept == {(10, 11), (12, 13), (14, 15)}  # exactly the 3 "match" edges
    # ONE batched embed call for edge texts (+question), not one-per-edge.
    assert len(emb.calls) == 1
    assert emb.calls[0][0] == "__QUESTION__" and len(emb.calls[0]) == 7  # q + 6 edges


# 3. Seed-incident edges are retained even when their score is the lowest.


def test_seed_incident_edges_always_retained_even_when_lowest_scoring():
    entities = [_ent(i) for i in range(0, 20)]
    edges = [
        _edge(0, 1, "noise"),     # SEED-INCIDENT (seed 0), lowest score -> must survive
        _edge(10, 11, "match"),   # high-score non-seed
        _edge(12, 13, "match"),   # high-score non-seed
        _edge(14, 15, "noise"),   # low-score non-seed -> dropped
    ]
    sub = _sub(entities, edges)
    emb = _KeywordEmbedder("match")
    # top_k=3: 1 seed-incident retained + budget 2 highest non-seed.
    out = rerank_subgraph_edges(sub, [0], question="__QUESTION__", embedder=emb, top_k=3)
    kept = {(e["subj"], e["obj"]) for e in out["edges"]}
    assert (0, 1) in kept                       # seed-incident kept despite lowest score
    assert kept == {(0, 1), (10, 11), (12, 13)}  # + the 2 best non-seed; (14,15) dropped


# 4. top_k >= len(edges) -> returned subgraph equals input (no-op, same object).


def test_top_k_ge_edges_is_noop():
    edges = [_edge(0, 1), _edge(1, 2)]
    sub = _sub([_ent(i) for i in range(3)], edges)
    out = rerank_subgraph_edges(sub, [0], question="q", embedder=_KeywordEmbedder("x"), top_k=2)
    assert out is sub  # top_k == len(edges) -> unchanged
    out2 = rerank_subgraph_edges(sub, [0], question="q", embedder=_KeywordEmbedder("x"), top_k=5)
    assert out2 is sub  # top_k > len(edges) -> unchanged


# 5. entities after prune == referenced-by-kept-edges UNION seeds; no seed dropped; input
#    dict not mutated.


def test_entities_reduced_to_kept_union_seeds_and_input_not_mutated():
    entities = [_ent(i) for i in range(0, 20)]
    edges = [
        _edge(10, 11, "match"),
        _edge(12, 13, "match"),
        _edge(14, 15, "noise"),   # dropped
        _edge(16, 17, "noise"),   # dropped
    ]
    sub = _sub(entities, edges)
    orig_edges_snapshot = list(sub["edges"])
    orig_ents_snapshot = list(sub["entities"])
    emb = _KeywordEmbedder("match")
    # seed 5 is isolated (no incident edge) but must survive as an entity.
    out = rerank_subgraph_edges(sub, [5], question="__QUESTION__", embedder=emb, top_k=2)
    kept_edges = out["edges"]
    referenced = {e["subj"] for e in kept_edges} | {e["obj"] for e in kept_edges} | {5}
    kept_ids = {e["entity_id"] for e in out["entities"]}
    assert kept_ids == referenced
    assert 5 in kept_ids                       # seed never dropped even when isolated
    assert kept_ids == {10, 11, 12, 13, 5}
    # input dict untouched (new lists returned, no mutation).
    assert sub["edges"] == orig_edges_snapshot
    assert sub["entities"] == orig_ents_snapshot
    assert out is not sub


def test_missing_name_falls_back_to_id_in_edge_text():
    # An edge whose endpoints have NO entity entry -> _edge_text uses the raw id, no crash.
    edges = [_edge(100, 101, "match"), _edge(102, 103, "noise"), _edge(104, 105, "noise")]
    sub = _sub([], edges)  # no entities at all
    emb = _KeywordEmbedder("match")
    out = rerank_subgraph_edges(sub, [], question="__QUESTION__", embedder=emb, top_k=1)
    assert {(e["subj"], e["obj"]) for e in out["edges"]} == {(100, 101)}
    # question + 3 edge texts embedded; ids stringified into the text.
    assert emb.calls[0][1] == "100 match 101"


# 6. Non-int GOLDENGRAPH_HYBRID_FILTER_TOPK -> default 40.


def test_topk_reader_default_and_bad_value(monkeypatch):
    monkeypatch.delenv("GOLDENGRAPH_HYBRID_FILTER_TOPK", raising=False)
    assert answer_mod._hybrid_filter_topk() == 40
    monkeypatch.setenv("GOLDENGRAPH_HYBRID_FILTER_TOPK", "notanint")
    assert answer_mod._hybrid_filter_topk() == 40
    monkeypatch.setenv("GOLDENGRAPH_HYBRID_FILTER_TOPK", "12")
    assert answer_mod._hybrid_filter_topk() == 12
