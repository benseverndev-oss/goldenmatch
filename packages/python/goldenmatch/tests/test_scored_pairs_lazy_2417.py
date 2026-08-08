"""`DedupeResult.scored_pairs` is lazy over its Arrow backing (#2417).

The B2c FS path keeps the pair stream columnar through scoring and clustering,
then used to rebuild the whole `list[tuple]` post-cluster just to fill this
field. Measured, that list is ~168 B/pair resident (~192 B/pair transient) vs
~24 B/pair for the Arrow table -- and `GOLDENMATCH_FS_SCORED_PAIRS_MAX` defaults
to 50,000,000, so it permitted an ~8.4 GB allocation that the `dedupe_df` +
identity path never reads.

What these pin:

* the list is NOT built until someone asks for it, and
* when they do ask, they get exactly what they got before -- a real `list`,
  same contents, cached.

The `isinstance` / `== []` assertions are the reason this is a cached-real-list
property rather than a lazy sequence: a sequence stand-in would read `== []`
as True until first access, which is a silent wrong answer.
"""
from __future__ import annotations

import pyarrow as pa
import pytest
from goldenmatch._api import DedupeResult
from goldenmatch.core.pairs import (
    materialize_scored_pairs,
    scored_pairs_from_table,
)
from goldenmatch.core.pipeline import (
    _finalize_review_pairs,
    _finalize_review_pairs_arrow,
)

_PAIRS = [(0, 1, 0.9), (2, 3, 0.75), (4, 5, 0.6)]


def _table(pairs=_PAIRS) -> pa.Table:
    return pa.table({
        "id_a": pa.array([p[0] for p in pairs], pa.int64()),
        "id_b": pa.array([p[1] for p in pairs], pa.int64()),
        "score": pa.array([p[2] for p in pairs], pa.float64()),
    })


class TestLazyScoredPairs:
    def test_not_materialized_until_accessed(self):
        """The whole point: constructing the result must not build the list."""
        res = DedupeResult(scored_pairs=None, _scored_pairs_table=_table())
        assert res.__dict__.get("_scored_pairs") is None, (
            "the list was built at construction -- laziness is not in effect"
        )

    def test_materializes_on_access_and_caches(self):
        res = DedupeResult(scored_pairs=None, _scored_pairs_table=_table())
        first = res.scored_pairs
        assert first == _PAIRS
        assert res.scored_pairs is first, "each access rebuilt the list"

    def test_is_a_real_list(self):
        """`isinstance(..., list)` is asserted by test_api.py and relied on by
        every steward surface -- a lazy sequence stand-in would fail here."""
        res = DedupeResult(scored_pairs=None, _scored_pairs_table=_table())
        assert isinstance(res.scored_pairs, list)

    def test_empty_table_is_empty_list(self):
        res = DedupeResult(scored_pairs=None, _scored_pairs_table=_table([]))
        assert res.scored_pairs == []
        assert isinstance(res.scored_pairs, list)

    def test_shed_stays_empty_without_a_table(self):
        """Above GOLDENMATCH_FS_SCORED_PAIRS_MAX the pipeline sheds the list and
        carries NO table; `== []` must hold (test_fs_scored_pairs_shed_2006)."""
        res = DedupeResult(scored_pairs=[], scored_pairs_shed=True)
        assert res.scored_pairs == []
        assert res.scored_pairs_shed is True

    def test_eager_list_passes_through(self):
        """Non-B2c paths still hand over a real list; it must not be touched."""
        res = DedupeResult(scored_pairs=list(_PAIRS))
        assert res.scored_pairs == _PAIRS

    def test_default_construction_is_empty_list(self):
        assert DedupeResult().scored_pairs == []


class TestMaterializeHelper:
    def test_reads_the_eager_list(self):
        assert materialize_scored_pairs({"scored_pairs": list(_PAIRS)}) == _PAIRS

    def test_reads_the_lazy_table(self):
        """The case a bare `results.get("scored_pairs") or []` gets WRONG."""
        results = {"scored_pairs": None, "scored_pairs_table": _table()}
        # The trap this helper exists to close: the old idiom reads EMPTY here.
        assert (results.get("scored_pairs") or []) == []
        assert materialize_scored_pairs(results) == _PAIRS

    def test_shed_reads_empty(self):
        assert materialize_scored_pairs(
            {"scored_pairs": [], "scored_pairs_table": None}
        ) == []

    def test_missing_keys_read_empty(self):
        assert materialize_scored_pairs({}) == []

    def test_from_table_none(self):
        assert scored_pairs_from_table(None) == []


class TestReviewPairsArrowAntiJoin:
    """`_finalize_review_pairs_arrow` must match the list version exactly."""

    def test_matches_list_version_when_disjoint(self):
        review = [(6, 7, 0.4), (8, 9, 0.45)]
        assert (
            _finalize_review_pairs_arrow(review, _table())
            == _finalize_review_pairs(review, _PAIRS)
        )

    def test_drops_a_pair_that_is_also_linked(self):
        """The case that makes this filter non-skippable.

        `score_buckets_arrow` can emit the SAME pair on more than one blocking
        pass with different scores, and the dedup runs later -- so a pair can
        sit in the review band AND in the linked set. Both implementations must
        drop it.
        """
        review = [(0, 1, 0.4), (8, 9, 0.45)]  # (0,1) is linked in _PAIRS
        expected = _finalize_review_pairs(review, _PAIRS)
        assert (0, 1) not in [(a, b) for a, b, _ in expected]
        assert _finalize_review_pairs_arrow(review, _table()) == expected

    def test_non_canonical_linked_pair_still_filters(self):
        """The list version canonicalizes the linked side with (min, max); the
        Arrow version must too, or a reversed linked pair would leak through."""
        reversed_linked = _table([(1, 0, 0.9)])
        review = [(0, 1, 0.4)]
        assert _finalize_review_pairs_arrow(review, reversed_linked) == []
        assert _finalize_review_pairs(review, [(1, 0, 0.9)]) == []

    def test_empty_linked_table_returns_deduped_review(self):
        review = [(8, 9, 0.45), (6, 7, 0.4)]
        assert (
            _finalize_review_pairs_arrow(review, _table([]))
            == _finalize_review_pairs(review, [])
        )

    def test_empty_review_is_empty(self):
        assert _finalize_review_pairs_arrow([], _table()) == []

    @pytest.mark.parametrize("dupe_score", [0.41, 0.49])
    def test_review_side_is_max_deduped_like_the_list_version(self, dupe_score):
        review = [(6, 7, 0.4), (6, 7, dupe_score)]
        assert (
            _finalize_review_pairs_arrow(review, _table())
            == _finalize_review_pairs(review, _PAIRS)
        )
