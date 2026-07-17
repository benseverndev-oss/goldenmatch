"""Every blocking strategy must treat a MISSING key as "cannot be blocked",
not "blocks with every other missing-key row".

`static`/`multi_pass` already filter invalid keys (via `build_blocks` +
`filter_valid_key`, keeping "" per #390), and the bucket scorer got the same
guard in #1857. This pins the two remaining exact-order/exact-equality
strategies that grouped missing-key rows together (part of the #1859 audit):

* sorted_neighborhood: a null `__sort_key__` used to sort adjacent and window
  together.
* learned (multi-predicate): `_compute_block_key` joined predicate parts with
  "||", so an all-missing 2+ predicate row produced "||" -- TRUTHY -- and the
  caller's `if key:` guard (which correctly drops the "" of a single missing
  predicate) leaked. All-missing rows collapsed into one shared block.

`adaptive` is intentionally NOT covered: its `_sub_block` already drops a null
sub-key (`if key_str is None: continue`), verified by reproduction.
"""

from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import BlockingConfig, SortKeyField
from goldenmatch.core.blocker import build_blocks
from goldenmatch.core.learned_blocking import (
    BlockingPredicate,
    BlockingRule,
    _compute_block_key,
    apply_learned_blocks,
)

# rows 0,1,2 have a NULL city (the block/sort key); 3,4 share a real city.
DF = pl.DataFrame(
    {
        "__row_id__": [0, 1, 2, 3, 4],
        "name": ["a", "b", "c", "d", "e"],
        "city": [None, None, None, "boston", "boston"],
    }
)
NULL_ROWS = {0, 1, 2}


def _groups_null_rows(blocks) -> bool:
    for b in blocks:
        ids = set(b.materialize().native["__row_id__"].to_list())
        if len(ids & NULL_ROWS) >= 2:
            return True
    return False


def _real_rows_still_block(blocks) -> bool:
    for b in blocks:
        ids = set(b.materialize().native["__row_id__"].to_list())
        if {3, 4} <= ids:
            return True
    return False


class TestSortedNeighborhood:
    def test_null_sort_key_rows_are_not_windowed_together(self):
        blocks = build_blocks(
            DF.lazy(),
            BlockingConfig(
                strategy="sorted_neighborhood",
                sort_key=[SortKeyField(column="city")],
                window_size=3,
            ),
        )
        assert not _groups_null_rows(blocks), (
            "null-sort-key rows were placed in a shared window -- a null key "
            "means the row cannot be ordered, not that it neighbors every other "
            "unorderable row"
        )
        assert _real_rows_still_block(blocks), "the real-city rows must still window together"


class TestLearnedMultiPredicate:
    def test_all_missing_multi_predicate_key_is_dropped(self):
        """The "||" truthiness leak, at its source: an all-missing conjunction
        must yield an empty (falsy) key so the caller drops it."""
        preds = [BlockingPredicate("city", "exact"), BlockingPredicate("name", "digits_only")]
        # both parts empty (city null -> "", name has no digits -> "")
        assert _compute_block_key({"city": None, "name": "abc"}, preds) == ""
        # a partially-populated key is still real and kept
        assert _compute_block_key({"city": "boston", "name": "abc"}, preds) != ""

    def test_all_missing_rows_do_not_share_a_block(self):
        rules = [
            BlockingRule(
                predicates=[
                    BlockingPredicate("city", "exact"),
                    BlockingPredicate("name", "digits_only"),
                ]
            )
        ]
        blocks = apply_learned_blocks(DF.lazy(), rules, max_block_size=10**9)
        assert not _groups_null_rows(blocks), (
            "all-missing rows collapsed into one learned block via the '||' key"
        )

    def test_single_predicate_all_missing_still_dropped(self):
        """Regression guard: the single-predicate case already worked ("" falsy);
        the fix must not break it."""
        assert _compute_block_key({"city": None}, [BlockingPredicate("city", "exact")]) == ""
