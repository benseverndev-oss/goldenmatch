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
