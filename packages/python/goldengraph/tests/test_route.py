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


class _StubClassifier:
    """Deterministic tier-2: returns a pre-set high-confidence profile for a known query."""
    def __init__(self, mapping):
        self.mapping = mapping  # query -> QueryProfile

    def classify(self, query, *, predicates=None):
        return self.mapping.get(query, route.QueryProfile(route.QueryIntent.MULTI_HOP, confidence=0.0))


def test_resolve_profile_no_classifier_is_heuristic():
    p = route.resolve_profile("List all entities that Metaphone works at.", predicates=_PREDS)
    assert p.intent is route.QueryIntent.AGGREGATE and p.relation == "works_at"


def test_resolve_profile_canonical_never_escalates():
    oracle = route.QueryProfile(route.QueryIntent.AGGREGATE, anchor_surface="WRONG", relation="works_at", confidence=1.0)
    stub = _StubClassifier({"List all entities that Metaphone works at.": oracle})
    p = route.resolve_profile("List all entities that Metaphone works at.", predicates=_PREDS, llm_classifier=stub)
    assert p.anchor_surface == "Metaphone"  # heuristic kept (0.9 >= MIN_CONF), stub NOT consulted


def test_resolve_profile_low_conf_escalates_to_classifier():
    q = "who all does Metaphone work with?"
    oracle = route.QueryProfile(route.QueryIntent.AGGREGATE, anchor_surface="Metaphone", relation="works_at", confidence=1.0)
    stub = _StubClassifier({q: oracle})
    p = route.resolve_profile(q, predicates=_PREDS, llm_classifier=stub)
    assert p.intent is route.QueryIntent.AGGREGATE and p.anchor_surface == "Metaphone" and p.relation == "works_at"


def test_resolve_profile_classifier_abstain_keeps_heuristic():
    q = "ramble ramble nonsense"
    stub = _StubClassifier({})
    p = route.resolve_profile(q, predicates=_PREDS, llm_classifier=stub)
    assert p.intent is route.QueryIntent.MULTI_HOP  # heuristic 0.3 kept (0.0 not > 0.3)


class _StubLLM:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def complete(self, prompt):
        self.calls += 1
        return self.response


def test_llm_classifier_parses_json_to_profile():
    llm = _StubLLM('{"intent": "aggregate", "anchor": "Metaphone", "relation": "works_at", "as_of": null}')
    c = route.LLMQueryClassifier(llm)
    p = c.classify("who all does Metaphone work with?", predicates=_PREDS)
    assert p.intent is route.QueryIntent.AGGREGATE
    assert p.anchor_surface == "Metaphone" and p.relation == "works_at"
    assert p.confidence >= 0.8


def test_llm_classifier_strips_fence():
    llm = _StubLLM('```json\n{"intent":"temporal_asof","anchor":"X","relation":"works_at","as_of":"3"}\n```')
    p = route.LLMQueryClassifier(llm).classify("...", predicates=_PREDS)
    assert p.intent is route.QueryIntent.TEMPORAL_ASOF and p.as_of == "3"


def test_llm_classifier_out_of_vocab_relation_abstains():
    llm = _StubLLM('{"intent":"aggregate","anchor":"X","relation":"NOT_A_PREDICATE","as_of":null}')
    p = route.LLMQueryClassifier(llm).classify("...", predicates=_PREDS)
    assert p.confidence == 0.0


def test_llm_classifier_bad_json_abstains():
    p = route.LLMQueryClassifier(_StubLLM("not json at all")).classify("...", predicates=_PREDS)
    assert p.intent is route.QueryIntent.MULTI_HOP and p.confidence == 0.0


def test_llm_classifier_budget_cap():
    llm = _StubLLM('{"intent":"lookup","anchor":null,"relation":null,"as_of":null}')
    c = route.LLMQueryClassifier(llm, max_calls=2)
    c.classify("a", predicates=_PREDS)
    c.classify("b", predicates=_PREDS)
    p = c.classify("c", predicates=_PREDS)
    assert p.confidence == 0.0 and llm.calls == 2


def test_plan_multihop_routes_hybrid():
    p = route.classify_query("How is A related to B?")
    assert route.plan_query(p).mode == "hybrid"
