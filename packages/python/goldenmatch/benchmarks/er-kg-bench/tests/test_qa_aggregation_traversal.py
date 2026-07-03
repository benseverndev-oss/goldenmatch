"""goldengraph exact traversal over the resolved fan-out store. Needs the native
wheel -> skips locally, validates in the gate lane."""
from __future__ import annotations

import pytest

pytest.importorskip("goldengraph_native")

from erkgbench.qa_e2e import ablation, dials  # noqa: E402
from erkgbench.qa_e2e.aggregation import (  # noqa: E402
    agg_documents_corpus,
    generate_aggregation,
    goldengraph_aggregate,
)
from erkgbench.qa_e2e.gold import GoldGraph  # noqa: E402


def _build(docs):
    corpus = agg_documents_corpus(docs)
    g = GoldGraph.from_corpus(corpus)
    km = dials.oracle_keys(corpus, g)  # oracle: anchor merges across docs
    slice_graph, coverage = ablation._build_store(corpus, g, km, ablation._typ_of(g))
    return slice_graph, coverage


def test_traversal_returns_the_exact_member_set():
    docs, qs = generate_aggregation(seed=7, n_anchors=20, ambiguity=0.6,
                                    fanout_buckets=((2, 4), (11, 20)))
    slice_graph, coverage = _build(docs)
    for q in (q for q in qs if q.kind == "list"):
        got = goldengraph_aggregate(slice_graph, coverage, q.anchor_id, q.relation)
        assert got == set(q.gold_members)  # exact, size-invariant


def test_predicate_survives_the_store_round_trip():
    # At n=95 a reused src_id accumulates >=2 relations in one merged store node.
    # Traversal of rel A must return A's members and EXCLUDE B's exclusive members.
    docs, qs = generate_aggregation(seed=7, n_anchors=95, ambiguity=0.0,
                                    fanout_buckets=((3, 6),))
    slice_graph, coverage = _build(docs)
    rels_by_anchor: dict = {}
    for q in (q for q in qs if q.kind == "list"):
        rels_by_anchor.setdefault(q.anchor_id, {})[q.relation] = set(q.gold_members)
    anchor = next(a for a, rm in rels_by_anchor.items() if len(rm) >= 2)
    rel_a, rel_b = list(rels_by_anchor[anchor])[:2]
    got_a = goldengraph_aggregate(slice_graph, coverage, anchor, rel_a)
    assert got_a == rels_by_anchor[anchor][rel_a]
    assert not (got_a & (rels_by_anchor[anchor][rel_b] - rels_by_anchor[anchor][rel_a]))
