"""Query-router kernel -- pure-Python unit tests (no wheel)."""
from __future__ import annotations

from goldengraph import route

_PREDS = {"works_at", "located_in", "acquired", "authored", "part_of"}


def test_classify_aggregate_intent():
    p = route.classify_query("List all entities that Metaphone works at.")
    assert p.intent is route.QueryIntent.AGGREGATE


def test_classify_count_is_aggregate():
    p = route.classify_query("How many entities does Metaphone acquired?")
    assert p.intent is route.QueryIntent.AGGREGATE


def test_classify_temporal_intent():
    p = route.classify_query("Who did X work for as of 2019?")
    assert p.intent is route.QueryIntent.TEMPORAL_ASOF


def test_classify_default_multihop():
    p = route.classify_query("How is Metaphone related to Levenshtein distance?")
    assert p.intent is route.QueryIntent.MULTI_HOP


def test_slots_extracted_with_predicates():
    p = route.classify_query("List all entities that Metaphone works at.", predicates=_PREDS)
    assert p.anchor_surface == "Metaphone"
    assert p.relation == "works_at"
    assert p.confidence >= 0.8


def test_slots_multiword_anchor():
    p = route.classify_query(
        "How many entities does Levenshtein distance located in?", predicates=_PREDS
    )
    assert p.anchor_surface == "Levenshtein distance"
    assert p.relation == "located_in"


def test_slots_without_predicates_low_confidence():
    p = route.classify_query("List all entities that Metaphone works at.")
    assert p.relation is None
    assert p.confidence < 0.8


def test_plan_aggregate_routes_to_aggregate():
    p = route.classify_query("List all entities that Metaphone works at.", predicates=_PREDS)
    assert route.plan_query(p).mode == "aggregate"


def test_plan_low_confidence_aggregate_falls_back():
    p = route.classify_query("List all entities that Metaphone works at.")
    assert route.plan_query(p).mode in ("local", "hybrid")


def test_temporal_slots_extracted():
    p = route.classify_query("As of 42, what does Metaphone works at?", predicates=_PREDS)
    assert p.intent is route.QueryIntent.TEMPORAL_ASOF
    assert p.anchor_surface == "Metaphone"
    assert p.relation == "works_at"
    assert p.as_of == "42"
    assert p.confidence >= 0.8


def test_temporal_multiword_anchor():
    p = route.classify_query(
        "As of 7, what does Levenshtein distance located in?", predicates=_PREDS
    )
    assert p.anchor_surface == "Levenshtein distance"
    assert p.relation == "located_in"
    assert p.as_of == "7"


def test_plan_temporal_routes_to_as_of():
    p = route.classify_query("As of 42, what does Metaphone works at?", predicates=_PREDS)
    plan = route.plan_query(p)
    assert plan.mode == "as_of" and plan.note is None


def test_plan_temporal_low_confidence_falls_back():
    p = route.classify_query("As of 42, what does Metaphone works at?")
    assert route.plan_query(p).mode == "local"


def test_plan_multihop_routes_hybrid():
    p = route.classify_query("How is A related to B?")
    assert route.plan_query(p).mode == "hybrid"
