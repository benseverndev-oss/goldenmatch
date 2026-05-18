"""In-process bucketed block scorer.

Architectural pivot from the per-block LazyFrame model:

  OLD (score_blocks_parallel / score_blocks_duckdb):
    build_blocks(combined_lf, blocking) -> list[BlockResult]
      where each BlockResult.df is a `combined_lf.filter(blocking_key == K)`
      LazyFrame. At 5M rows / 1.67M blocks of 3 rows each, the LIST of
      1.67M filter-LazyFrames + any per-block `.collect()`/`.select()` chains
      explode Polars arena memory. Documented in heartbeats:
      runs 25998537828, 26000789629, 26002766443, 26004842882, 26006853280,
      26008682481, 26012579494 -- all hung at 62.99 GB RSS plateau on Linux
      without ever reaching real scoring.

  NEW (score_buckets):
    prepared_df (eager) + blocking_config -> in one Polars pass:
      with_columns(__block_key__ = key_expr, __bucket__ = hash(__block_key__) % N)
    -> partition_by("__bucket__", as_dict=True)   # ≤ N eager bucket dfs
    -> partition_by("__block_key__", as_dict=True) within each bucket
    -> _score_one_block on each per-block eager df

    No LazyFrames carrying filter expressions. No materialization of millions
    of small frames. Two partition_by operations + N rapidfuzz calls.

Hard invariant: at scale, this module must never call ``.collect()`` on a
filter-LazyFrame. The single eager materialization happens once via
``prepared_df = combined_lf.collect()`` at the pipeline call site BEFORE
this scorer runs.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import polars as pl

from goldenmatch.config.schemas import BlockingConfig, MatchkeyConfig
from goldenmatch.core.bench import record_metrics, stage
from goldenmatch.core.blocker import _build_block_key_expr
from goldenmatch.core.scorer import _score_one_block

logger = logging.getLogger(__name__)


BUCKET_HASH_SEED = 0xC2B5C0BBE7ED5E5D
"""Same constant as goldenmatch.distributed.record_store. Deterministic
xxHash seed so block_key -> bucket assignment is stable across runs."""


def _default_n_buckets() -> int:
    """Default bucket count. min(cpu_count() * 4, 1024). Same heuristic as
    Component 2 v2's materialize_bucketed_blocks."""
    return min((os.cpu_count() or 4) * 4, 1024)


class _BlockShim:
    """BlockResult-shaped wrapper around an eager per-block DataFrame.

    `_score_one_block` reads `.block_key`, `.df`, and `.pre_scored_pairs`.
    Multi-pass blocking (pre_scored_pairs) is not supported in bucket mode
    v1; pass None.
    """
    __slots__ = ("block_key", "df", "pre_scored_pairs")

    def __init__(self, block_key: str, df: pl.DataFrame):
        self.block_key = block_key
        # _score_one_block does `block.df.collect()` first; passing an
        # eager df via .lazy() makes the collect a no-op. Cheaper than
        # threading a separate eager-aware path through scorer.py.
        self.df = df.lazy()
        self.pre_scored_pairs: list[tuple[int, int, float]] | None = None


def score_buckets(
    prepared_df: pl.DataFrame,
    blocking_config: BlockingConfig,
    mk: MatchkeyConfig,
    matched_pairs: set[tuple[int, int]],
    n_buckets: int | None = None,
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
    target_ids: set[int] | None = None,
) -> list[tuple[int, int, float]]:
    """Score all blocks via hash-bucketed partition_by, no per-block LazyFrame.

    Args:
        prepared_df: Eager Polars DataFrame, already materialized. Must
            contain ``__row_id__`` and all columns referenced by ``mk`` +
            ``blocking_config``.
        blocking_config: Source for the block-key expression.
            ``keys[0]`` is used; multi-key blocking is not supported in
            bucket mode v1.
        mk: Matchkey configuration.
        matched_pairs: Set of already-matched (min_id, max_id) pairs;
            mutated in-place as new pairs are emitted (mirrors
            score_blocks_parallel's contract).
        n_buckets: Hash bucket count. None -> ``min(cpu_count() * 4, 1024)``.
        across_files_only: Filter to cross-source pairs only.
        source_lookup: Row ID -> source name mapping.
        target_ids: For match mode -- filter to target/ref cross pairs.

    Returns:
        All fuzzy pairs as (id_a, id_b, score) tuples.
    """
    if prepared_df.height == 0:
        return []
    if not blocking_config.keys:
        return []

    if n_buckets is None:
        n_buckets = _default_n_buckets()

    key_expr = _build_block_key_expr(blocking_config.keys[0])

    with stage("bucket_assign"):
        keyed = prepared_df.with_columns(key_expr)
        bucketed = keyed.with_columns(
            (pl.col("__block_key__").hash(seed=BUCKET_HASH_SEED) % n_buckets)
            .alias("__bucket__")
        )

    with stage("bucket_partition"):
        # First-level partition: N eager DataFrames keyed by bucket id.
        # Polars >= 1.0 returns tuple-keyed dict when as_dict=True with a
        # single partition column; unwrap below.
        buckets_dict: dict[Any, pl.DataFrame] = bucketed.partition_by(
            "__bucket__", as_dict=True,
        )

    frozen_exclude = frozenset(matched_pairs)
    all_pairs: list[tuple[int, int, float]] = []
    total_blocks_scored = 0
    n_non_empty_buckets = 0

    with stage("bucket_score"):
        for _bucket_key, bucket_df in buckets_dict.items():
            if bucket_df.height == 0:
                continue
            n_non_empty_buckets += 1
            # Second-level partition: per-block eager DataFrames.
            block_groups: dict[Any, pl.DataFrame] = bucket_df.partition_by(
                "__block_key__", as_dict=True,
            )
            for block_key, block_df in block_groups.items():
                if block_df.height < 2:
                    continue
                # Polars >= 1.0 partition_by with single column returns
                # tuple keys ("k",) -- unwrap.
                if isinstance(block_key, tuple):
                    block_key = block_key[0]
                shim = _BlockShim(block_key=str(block_key), df=block_df)
                pairs = _score_one_block(
                    shim, mk, frozen_exclude,
                    across_files_only=across_files_only,
                    source_lookup=source_lookup,
                )
                if target_ids is not None:
                    pairs = [
                        (a, b, s) for a, b, s in pairs
                        if (a in target_ids) != (b in target_ids)
                    ]
                all_pairs.extend(pairs)
                for a, b, _s in pairs:
                    matched_pairs.add((min(a, b), max(a, b)))
                total_blocks_scored += 1

    record_metrics({
        "bucket_count": n_non_empty_buckets,
        "bucket_n_target": n_buckets,
        "block_count_scored": total_blocks_scored,
    })
    logger.info(
        "score_buckets: %d non-empty buckets (target N=%d), %d blocks scored, %d pairs",
        n_non_empty_buckets, n_buckets, total_blocks_scored, len(all_pairs),
    )
    return all_pairs
