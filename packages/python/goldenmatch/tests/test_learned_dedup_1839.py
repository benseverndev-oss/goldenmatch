"""Issue #1839 -- ``apply_learned_blocks`` must dedup overlapping rules WITHOUT
collecting every block.

The dedup loop used to materialize each candidate block just to read back its
``__row_id__`` values and hash them. At 1M rows the learned path produces ~200K
five-row blocks, so that was ~200K collects of pure overhead on top of the ~200K
LazyFrames the loop above it had already built -- while the actual scoring work
(5x5 matrices) is trivial. ``member_positions`` was already in hand, and
``__row_id__`` is a ``with_row_index()`` column, so position <-> row_id is a
bijection and ``frozenset(positions)`` is an equivalent set-identity key.

These tests pin BOTH halves of that claim: the dedup must still happen (the
cheap way to "speed this up" is to silently stop deduping), and it must happen
without materializing.
"""

from __future__ import annotations

import polars as pl
from goldenmatch.core.learned_blocking import (
    BlockingPredicate,
    BlockingRule,
    apply_learned_blocks,
)


def _rule(field: str, transform: str = "exact") -> BlockingRule:
    return BlockingRule(predicates=[BlockingPredicate(field=field, transform=transform)])


def _frame() -> pl.LazyFrame:
    # city and zone induce the SAME partition ({0,1}, {2,3}) via different fields,
    # so two overlapping rules yield identical member sets -> dedup must collapse them.
    return pl.DataFrame(
        {
            "__row_id__": [0, 1, 2, 3],
            "city": ["nyc", "nyc", "sfo", "sfo"],
            "zone": ["east", "east", "west", "west"],
        }
    ).lazy()


def _members(block) -> frozenset[int]:
    return frozenset(block.materialize().native["__row_id__"].to_list())


class TestLearnedDedup:
    def test_identical_member_sets_from_different_rules_are_deduped(self):
        """The core behavior being preserved: two rules that partition the frame
        the same way must not produce duplicate blocks."""
        blocks = apply_learned_blocks(_frame(), [_rule("city"), _rule("zone")])

        assert len(blocks) == 2, [b.block_key for b in blocks]
        assert {_members(b) for b in blocks} == {frozenset({0, 1}), frozenset({2, 3})}

    def test_first_rule_wins(self):
        """Dedup keeps the FIRST occurrence -- moving dedup earlier must not flip
        which rule's block_key survives."""
        blocks = apply_learned_blocks(_frame(), [_rule("city"), _rule("zone")])

        assert all(b.block_key.startswith("learned:city:") for b in blocks), [
            b.block_key for b in blocks
        ]

    def test_distinct_member_sets_are_all_kept(self):
        """Dedup must not over-collapse: genuinely different blocks all survive."""
        lf = pl.DataFrame(
            {
                "__row_id__": [0, 1, 2, 3],
                "city": ["nyc", "nyc", "sfo", "sfo"],   # {0,1}, {2,3}
                "zone": ["east", "west", "east", "west"],  # {0,2}, {1,3}
            }
        ).lazy()

        blocks = apply_learned_blocks(lf, [_rule("city"), _rule("zone")])

        assert {_members(b) for b in blocks} == {
            frozenset({0, 1}),
            frozenset({2, 3}),
            frozenset({0, 2}),
            frozenset({1, 3}),
        }

    def test_collects_do_not_scale_with_block_count(self):
        """The #1839 fix itself: collects must be CONSTANT in block count.

        Not "zero collects" -- there's an irreducible floor of 1 (reading the
        input frame) + 1 per rule (polars implements eager ``DataFrame.select``
        as ``lazy().select().collect()``, which the ``.to_dicts()`` line pays).
        The bug was the term that scaled: the old dedup loop collected every
        candidate block, costing ``3 + 2*n_blocks`` here and ~400K collects at
        the 1M / 200K-block shape. Measured on the pre-fix code: 7 / 103 / 1003
        collects for 2 / 50 / 500 blocks vs a flat 3 now.

        Asserting the SHAPE (flat vs linear) rather than a magic number keeps
        this honest if polars changes its internal select strategy.
        """
        counts = {}
        for n_blocks in (2, 50, 500):
            collects = 0
            original = pl.LazyFrame.collect

            def counting_collect(self, *args, **kwargs):
                nonlocal collects
                collects += 1
                return original(self, *args, **kwargs)

            n = n_blocks * 2
            lf = pl.DataFrame(
                {
                    "__row_id__": list(range(n)),
                    "city": [f"c{i // 2}" for i in range(n)],
                    "zone": [f"z{i // 2}" for i in range(n)],
                }
            ).lazy()

            pl.LazyFrame.collect = counting_collect
            try:
                blocks = apply_learned_blocks(lf, [_rule("city"), _rule("zone")])
            finally:
                pl.LazyFrame.collect = original

            assert len(blocks) == n_blocks  # dedup still collapsing the 2 rules
            counts[n_blocks] = collects

        assert len(set(counts.values())) == 1, (
            f"collects must not scale with block count, got {counts}"
        )
