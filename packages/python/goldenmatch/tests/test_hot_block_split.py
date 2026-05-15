"""Tests for hot-block auto-split in static blocking.

Background: at 100K zero-config, `fuzzy_score_blocks` is 94% of wall —
the per-block cdist's quadratic in block size dominates. A single hot
block of 1K+ records can outweigh hundreds of small blocks. Static
blocking used to silently drop these (when `skip_oversized=True` and
no ANN column) which both lost recall and ate the wall savings.

The fix: try `_auto_split_block` before skipping. These tests:
  1. confirm the prior behavior is preserved when auto-split can't help
     (no column has cardinality > 1 within the block);
  2. lock in the new behavior when auto-split reduces a hot block to
     smaller pieces;
  3. ensure the bench harness records the split count.
"""
from __future__ import annotations

import polars as pl
from goldenmatch.config.schemas import BlockingConfig, BlockingKeyConfig
from goldenmatch.core.bench import bench_capture
from goldenmatch.core.blocker import build_blocks


class TestHotBlockSplit:
    def test_oversized_block_with_splittable_column_is_split(self):
        """A hot block with a high-cardinality column should split into sub-blocks."""
        config = BlockingConfig(
            keys=[BlockingKeyConfig(fields=["zip"], transforms=[])],
            max_block_size=3,
            skip_oversized=True,
        )
        # 8 rows in zip 19382 with 4 distinct cities → splittable.
        # Add a separate 2-row block to confirm coexistence.
        df = pl.DataFrame({
            "id": list(range(10)),
            "zip": ["19382"] * 8 + ["10001"] * 2,
            "city": ["A", "A", "B", "B", "C", "C", "D", "D"] + ["X", "X"],
        })
        results = build_blocks(df.lazy(), config)

        # The 10001 block stays. The 19382 block splits into 4 city
        # sub-blocks of size 2 each (all under max_block_size=3).
        block_keys = sorted(r.block_key for r in results)
        # 10001 block + 4 city sub-blocks from 19382 (named "19382||A" etc).
        assert "10001" in block_keys
        split_subs = [k for k in block_keys if k.startswith("19382||")]
        assert len(split_subs) == 4, (
            f"Expected 4 sub-blocks from hot 19382 block; got {split_subs}"
        )
        # No sub-block should exceed max_block_size.
        for r in results:
            size = r.df.collect().height
            assert size <= 3, f"Block {r.block_key} has {size} rows > max=3"

    def test_oversized_block_with_no_splittable_column_is_skipped(self):
        """When no in-block column varies, skip-behavior is preserved."""
        config = BlockingConfig(
            keys=[BlockingKeyConfig(fields=["zip"], transforms=[])],
            max_block_size=2,
            skip_oversized=True,
        )
        # 3 rows all sharing every column value — auto-split can't help.
        df = pl.DataFrame({
            "id": [1, 2, 3],
            "zip": ["19382", "19382", "19382"],
            "city": ["Westfield", "Westfield", "Westfield"],
        })
        results = build_blocks(df.lazy(), config)
        # All three rows in the same hot block, no split possible → drop.
        assert results == [], (
            f"Hot block with no splittable column should be skipped, "
            f"got {[r.block_key for r in results]}"
        )

    def test_skip_oversized_false_still_processes_anyway(self):
        """`skip_oversized=False` is an explicit "keep big blocks" mode.

        Auto-split should NOT fire when the user has opted into processing
        oversized blocks intact (preserves the prior semantic).
        """
        config = BlockingConfig(
            keys=[BlockingKeyConfig(fields=["zip"], transforms=[])],
            max_block_size=3,
            skip_oversized=False,
        )
        df = pl.DataFrame({
            "id": list(range(8)),
            "zip": ["19382"] * 8,
            "city": ["A", "A", "B", "B", "C", "C", "D", "D"],
        })
        results = build_blocks(df.lazy(), config)
        # One single 8-row block, intact.
        assert len(results) == 1
        assert results[0].block_key == "19382"
        assert results[0].df.collect().height == 8

    def test_bench_records_split_count(self):
        """`hot_blocks_split_count` should land on the active recorder."""
        config = BlockingConfig(
            keys=[BlockingKeyConfig(fields=["zip"], transforms=[])],
            max_block_size=3,
            skip_oversized=True,
        )
        df = pl.DataFrame({
            "id": list(range(8)),
            "zip": ["19382"] * 8,
            "city": ["A", "A", "B", "B", "C", "C", "D", "D"],
        })
        with bench_capture() as rec:
            build_blocks(df.lazy(), config)
        assert rec.metrics.get("hot_blocks_split_count") == 1, rec.metrics

    def test_bench_records_skipped_count(self):
        """`hot_blocks_skipped_count` should land when no split is possible."""
        config = BlockingConfig(
            keys=[BlockingKeyConfig(fields=["zip"], transforms=[])],
            max_block_size=2,
            skip_oversized=True,
        )
        df = pl.DataFrame({
            "id": [1, 2, 3],
            "zip": ["19382", "19382", "19382"],
            "city": ["Westfield", "Westfield", "Westfield"],
        })
        with bench_capture() as rec:
            build_blocks(df.lazy(), config)
        assert rec.metrics.get("hot_blocks_skipped_count") == 1, rec.metrics

    def test_ann_column_path_unchanged(self):
        """When `ann_column` is set, ANN fallback fires before auto-split.

        Without ANN deps, the ANN call fails-and-continues; auto-split
        should NOT kick in because that would be a behavioral regression
        for users who configured ANN. They asked for ANN; respect it.
        """
        # Skip when sentence-transformers isn't installed — ANN path
        # depends on the embedder.
        try:
            import sentence_transformers  # noqa: F401
        except ImportError:
            import pytest
            pytest.skip("sentence-transformers not installed")
        config = BlockingConfig(
            keys=[BlockingKeyConfig(fields=["zip"], transforms=[])],
            max_block_size=3,
            skip_oversized=True,
            ann_column="city",
        )
        df = pl.DataFrame({
            "id": list(range(8)),
            "zip": ["19382"] * 8,
            "city": ["A", "A", "B", "B", "C", "C", "D", "D"],
        })
        with bench_capture() as rec:
            build_blocks(df.lazy(), config)
        # ANN path takes the block; auto-split metric should NOT appear.
        assert rec.metrics.get("hot_blocks_split_count") is None
