"""Fan-out aggregation corpus: one source entity, many objects per relation, one
edge-doc per (X, rel, member). The gold member SET is emitted directly (not
re-derived from doc-id parsing). Deterministic for a seed."""
from __future__ import annotations

from erkgbench.qa_e2e.aggregation import generate_aggregation, size_bucket


def test_emits_list_and_count_questions_with_gold_sets():
    docs, qs = generate_aggregation(seed=7, n_anchors=12, ambiguity=0.5,
                                    fanout_buckets=((2, 4), (5, 10), (11, 20)))
    assert any(q.kind == "list" for q in qs) and any(q.kind == "count" for q in qs)
    for q in qs:
        assert q.gold_count == len(q.gold_members)
        assert q.relation
        assert q.relation.replace("_", " ") in q.question
        assert q.anchor_id


def test_edge_docs_are_3part_ids_with_populated_surfaces():
    docs, qs = generate_aggregation(seed=7, n_anchors=8, ambiguity=0.6,
                                    fanout_buckets=((2, 4), (11, 20)))
    for d in docs:
        assert len(d.id.split("::")) == 3
        assert d.src_surface and d.dst_surface


def test_gold_members_match_the_emitted_edges_for_an_anchor():
    docs, qs = generate_aggregation(seed=3, n_anchors=6, ambiguity=0.0,
                                    fanout_buckets=((3, 6),))
    by_anchor: dict = {}
    for d in docs:
        s, rel, o = d.id.split("::")
        by_anchor.setdefault((s, rel), set()).add(o)
    for q in (q for q in qs if q.kind == "list"):
        assert set(q.gold_members) == by_anchor[(q.anchor_id, q.relation)]


def test_buckets_each_get_enough_questions():
    docs, qs = generate_aggregation(seed=7, n_anchors=60, ambiguity=0.3,
                                    fanout_buckets=((2, 4), (5, 10), (11, 20)))
    counts: dict = {}
    for q in qs:
        b = size_bucket(q.gold_count)
        counts[b] = counts.get(b, 0) + 1
    assert all(c >= 20 for c in counts.values())
