"""Multi-pass blocking correctness: cross-pass key collisions + field collection.

Two fixes under test:

1. ``_build_multi_pass_blocks`` must not drop a block from a later pass just
   because its block_key *value* collides with an earlier pass on a DIFFERENT
   field (block_key is value-only — soundex/substring/numeric keys share a
   namespace across fields). Dedup is by (pass signature, value), so distinct-
   field blocks survive while truly-identical blocks still dedup.
2. ``collect_blocking_fields`` gathers blocking columns from keys AND passes
   AND sub_block_keys (multi_pass keeps its keys in ``.passes``) — the FS
   pipeline needs the full set to exclude blocking fields from EM training.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.blocker import build_blocks, collect_blocking_fields


def _members(blocks):
    out = []
    for b in blocks:
        bdf = b.df.collect() if hasattr(b.df, "collect") else b.df
        out.append(frozenset(bdf["__row_id__"].to_list()))
    return out


class TestMultiPassCollision:
    def test_colliding_value_across_passes_not_dropped(self):
        # Rows 0,1 share field_a="X"; rows 2,3 share field_b="X". With a
        # value-only dedup the second "X" block is dropped and pair (2,3) is
        # lost. With pass-signature dedup both survive.
        df = pl.DataFrame({
            "__row_id__": [0, 1, 2, 3],
            "field_a": ["X", "X", "M", "N"],
            "field_b": ["P", "Q", "X", "X"],
        })
        cfg = BlockingConfig(
            strategy="multi_pass",
            passes=[
                BlockingKeyConfig(fields=["field_a"]),
                BlockingKeyConfig(fields=["field_b"]),
            ],
        )
        members = _members(build_blocks(df.lazy(), cfg))
        assert frozenset({0, 1}) in members, "field_a=X block missing"
        assert frozenset({2, 3}) in members, "field_b=X block dropped by value collision"

    def test_identical_pass_blocks_still_dedup(self):
        # Two passes on the SAME field+transform should not double the block.
        df = pl.DataFrame({
            "__row_id__": [0, 1, 2],
            "field_a": ["X", "X", "Y"],
        })
        cfg = BlockingConfig(
            strategy="multi_pass",
            passes=[
                BlockingKeyConfig(fields=["field_a"]),
                BlockingKeyConfig(fields=["field_a"]),
            ],
        )
        members = _members(build_blocks(df.lazy(), cfg))
        assert members.count(frozenset({0, 1})) == 1, "identical-pass block duplicated"


class TestCollectBlockingFields:
    def test_collects_from_keys(self):
        cfg = BlockingConfig(keys=[BlockingKeyConfig(fields=["zip", "state"])])
        assert collect_blocking_fields(cfg) == ["zip", "state"]

    def test_collects_from_passes(self):
        cfg = BlockingConfig(
            strategy="multi_pass",
            passes=[
                BlockingKeyConfig(fields=["postcode"]),
                BlockingKeyConfig(fields=["surname"]),
                BlockingKeyConfig(fields=["date_of_birth"]),
            ],
        )
        assert collect_blocking_fields(cfg) == ["postcode", "surname", "date_of_birth"]

    def test_dedups_and_preserves_order(self):
        cfg = BlockingConfig(
            strategy="multi_pass",
            keys=[BlockingKeyConfig(fields=["a"])],
            passes=[
                BlockingKeyConfig(fields=["a", "b"]),
                BlockingKeyConfig(fields=["c"]),
            ],
        )
        assert collect_blocking_fields(cfg) == ["a", "b", "c"]

    def test_includes_sub_block_keys(self):
        cfg = BlockingConfig(
            keys=[BlockingKeyConfig(fields=["zip"])],
            sub_block_keys=[BlockingKeyConfig(fields=["last_name"])],
        )
        assert collect_blocking_fields(cfg) == ["zip", "last_name"]

    def test_empty_when_no_keys(self):
        cfg = BlockingConfig(strategy="ann", ann_column="vec")
        assert collect_blocking_fields(cfg) == []
