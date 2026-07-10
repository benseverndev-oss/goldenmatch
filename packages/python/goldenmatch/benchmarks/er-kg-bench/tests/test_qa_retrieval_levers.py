"""Tests for the Lever-A measurement harness. `_apply` is pure; the end-to-end smoke is
wheel-gated + LLM-FREE (recall guard only), so no network."""
from __future__ import annotations

import pytest
from erkgbench.qa_e2e.retrieval_levers import _DIALS, _apply, measure_lever


def _sub():
    return {
        "entities": [{"entity_id": i, "canonical_name": f"n{i}", "typ": "concept"} for i in range(4)],
        "edges": [{"subj": 0, "predicate": "r", "obj": 1}, {"subj": 1, "predicate": "r", "obj": 2},
                  {"subj": 0, "predicate": "r", "obj": 3}],
    }


def test_apply_none_is_identity():
    sub = _sub()
    assert _apply("none", sub, [0, 2], halo=0) is sub


def test_apply_filter_path_prunes():
    from goldengraph.subgraph_filter import filter_subgraph_to_paths

    sub = _sub()
    assert _apply("filter_path", sub, [0, 2], halo=0) == filter_subgraph_to_paths(sub, [0, 2], halo=0)


def test_apply_unknown_raises():
    with pytest.raises(ValueError):
        _apply("nope", _sub(), [0], halo=1)


def test_apply_candidate_matches_primitive():
    import numpy as np
    from goldengraph.retrieve_paths import prune_to_candidate_paths

    class _Emb:
        def embed(self, texts):
            return np.asarray([[1.0] if t in ("q", "n2") else [0.0] for t in texts], dtype=float)

    sub = _sub()
    emb = _Emb()
    got = _apply("candidate", sub, [0], halo=0, question="q", embedder=emb, k_hops=4, top_c=1)
    assert got == prune_to_candidate_paths(sub, [0], "q", emb, k_hops=4, top_c=1, halo=0)
    assert sorted(e["entity_id"] for e in got["entities"]) == [0, 1, 2]  # chain kept, leaf 3 dropped


def test_measure_lever_recall_guard_smoke():
    """End-to-end, LLM-free: recall computed on the POST-lever subgraph, per dial. With ALL
    entities seeded the filter prunes nothing, so filter_path recall == none recall (proves the
    harness applies the lever + measures post-lever recall, no synthesis cost)."""
    pytest.importorskip("goldengraph_native")
    from erkgbench.qa_e2e.ablation import _typ_of
    from erkgbench.qa_e2e.engineered import generate_engineered
    from erkgbench.qa_e2e.gold import GoldGraph

    corpus = generate_engineered(seed=7, n_questions=6, ambiguity=0.0, max_hops=4)
    g = GoldGraph.from_corpus(corpus)
    typ_of = _typ_of(g)
    all_seeds = lambda sg, q: [e["entity_id"] for e in sg.entities()]  # noqa: E731

    none = measure_lever(corpus, g, typ_of, lever="none", seeds_fn=all_seeds)
    filt = measure_lever(corpus, g, typ_of, lever="filter_path", seeds_fn=all_seeds, halo=1)
    for d in _DIALS:
        assert 0.0 <= none.bridge_recall[d] <= 1.0
        # every node is a seed -> filter keeps all -> recall identical (guard-object correctness)
        assert filt.bridge_recall[d] == none.bridge_recall[d]
    assert none.answer_match == {}  # llm=None -> recall-guard only, no paid answer-match
