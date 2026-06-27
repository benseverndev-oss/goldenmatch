"""Slice 4b tier resolvers -- EXACT grouping + resolver factory (needs goldenmatch for _record_key)."""
from __future__ import annotations

from goldengraph.extract import Mention
from goldengraph.resolve import _exact_resolve


def test_exact_resolve_merges_identical_separates_variants():
    ms = [Mention("Apple", "org"), Mention("Apple", "org"), Mention("Apple Inc", "org")]
    ents = _exact_resolve(ms)
    # exact (name,typ): {Apple:[0,1]} + {Apple Inc:[2]} -> 2 entities; the two "Apple" merge
    assert len(ents) == 2
    by_members = {tuple(e.member_idx): e for e in ents}
    assert (0, 1) in by_members and by_members[(0, 1)].surface_names == ["Apple"]
    assert (2,) in by_members and by_members[(2,)].surface_names == ["Apple Inc"]


def test_exact_resolve_empty():
    assert _exact_resolve([]) == []


from goldengraph import unified  # noqa: E402

_PREDS = {"works_at", "located_in", "acquired", "authored", "part_of"}


def test_resolver_for_tier_returns_distinct():
    assert unified.resolver_for_tier(unified.ResolutionTier.EXACT) is _exact_resolve
    f = unified.resolver_for_tier(unified.ResolutionTier.FUZZY)
    fc = unified.resolver_for_tier(unified.ResolutionTier.FUZZY_CONTEXT)
    assert callable(f) and callable(fc) and f is not fc


def test_exact_tier_resolver_groups_exactly():
    r = unified.resolver_for_tier(unified.ResolutionTier.EXACT)
    ents = r([Mention("Apple", "org"), Mention("Apple Inc", "org")])
    assert len(ents) == 2  # distinct surfaces stay separate under EXACT


def test_plan_resolver_capability_returns_fuzzy_resolver():
    plan, resolver = unified.plan_resolver(
        ["List all entities that Metaphone works at."], predicates=_PREDS
    )
    assert plan.resolution_tier is unified.ResolutionTier.FUZZY
    assert callable(resolver)


def test_plan_resolver_lookup_returns_exact_resolver():
    plan, resolver = unified.plan_resolver(["what is Soundex?"], predicates=_PREDS)
    assert plan.resolution_tier is unified.ResolutionTier.EXACT
    assert resolver is _exact_resolve
