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
