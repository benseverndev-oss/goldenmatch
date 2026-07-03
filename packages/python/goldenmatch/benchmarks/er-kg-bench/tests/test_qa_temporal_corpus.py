"""Temporal as_of corpus: per fact a value corrected at valid-time Tc
(X-rel-A [1,Tc), X-rel-B [Tc,inf)), with past (D<Tc -> A) and current (D>=Tc -> B)
questions. Deterministic for a seed."""
from __future__ import annotations

from erkgbench.qa_e2e.temporal import T1, generate_temporal


def test_facts_have_two_windows_and_both_regime_questions():
    docs, facts, qs = generate_temporal(seed=7, n_facts=20, ambiguity=0.5)
    assert any(q.regime == "past" for q in qs) and any(q.regime == "current" for q in qs)
    fbyk = {(f.anchor_id, f.relation): f for f in facts}
    for q in qs:
        f = fbyk[(q.anchor_id, q.relation)]
        if q.regime == "past":
            assert T1 <= q.D < f.tc and q.gold_obj == f.a_id
        else:
            assert q.D >= f.tc and q.gold_obj == f.b_id
        assert q.relation.replace("_", " ") in q.question


def test_objects_disjoint_from_anchors():
    docs, facts, qs = generate_temporal(seed=7, n_facts=30, ambiguity=0.4)
    anchors = {f.anchor_id for f in facts}
    objects = {f.a_id for f in facts} | {f.b_id for f in facts}
    assert not (anchors & objects)


def test_each_regime_has_enough_questions():
    docs, facts, qs = generate_temporal(seed=7, n_facts=40, ambiguity=0.3)
    past = sum(1 for q in qs if q.regime == "past")
    current = sum(1 for q in qs if q.regime == "current")
    assert past >= 20 and current >= 20
