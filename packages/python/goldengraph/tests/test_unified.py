"""Workload-aware resolution planner (slice 4a) -- pure-Python unit tests."""
from __future__ import annotations

from goldengraph import unified

_PREDS = {"works_at", "located_in", "acquired", "authored", "part_of"}


def test_capability_workload_routes_to_fuzzy():
    queries = [
        "List all entities that Metaphone works at.",
        "As of 3, what does Soundex works at?",
    ]
    plan = unified.plan_resolution(unified.profile_workload(queries, predicates=_PREDS))
    assert plan.resolution_tier is unified.ResolutionTier.FUZZY
    assert plan.capability_fraction >= 0.5


def test_lookup_workload_routes_to_exact():
    queries = ["what is Soundex?", "what is Metaphone?", "who is Levenshtein distance?"]
    plan = unified.plan_resolution(unified.profile_workload(queries, predicates=_PREDS))
    assert plan.resolution_tier is unified.ResolutionTier.EXACT
    assert plan.capability_fraction < 0.5


def test_modes_needed_collected():
    queries = ["List all entities that Metaphone works at.", "what is Soundex?"]
    wp = unified.profile_workload(queries, predicates=_PREDS)
    assert "aggregate" in wp.retrieval_modes_needed


def test_empty_workload_defaults_to_exact():
    plan = unified.plan_resolution(unified.profile_workload([], predicates=_PREDS))
    assert plan.resolution_tier is unified.ResolutionTier.EXACT


def test_profile_workload_injected_classifier_upgrades_lookup():
    # A LOOKUP query (heuristic conf 0.5 < MIN_CONF, NON-capability) the stub upgrades to AGGREGATE;
    # without the stub the workload would route EXACT, so the stub's output is load-bearing.
    from goldengraph.route import QueryIntent, QueryProfile

    class _Stub:
        def classify(self, query, *, predicates=None):
            return QueryProfile(QueryIntent.AGGREGATE, anchor_surface="Soundex",
                                relation="works_at", confidence=1.0)

    q = ["what is Soundex?"]
    assert unified.plan_resolution(unified.profile_workload(q, predicates=_PREDS)).resolution_tier is unified.ResolutionTier.EXACT
    upgraded = unified.profile_workload(q, predicates=_PREDS, llm_classifier=_Stub())
    assert unified.plan_resolution(upgraded).resolution_tier is unified.ResolutionTier.FUZZY
