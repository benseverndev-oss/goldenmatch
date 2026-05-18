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
from concurrent.futures import ThreadPoolExecutor
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
    non_empty_buckets = [b for b in buckets_dict.values() if b.height > 0]
    n_non_empty_buckets = len(non_empty_buckets)

    def _score_one_bucket(bucket_df: pl.DataFrame) -> tuple[list[tuple[int, int, float]], int]:
        # Sort once, slice per block. Avoids partition_by's millions-of-tiny-
        # eager-frames allocation that fragments glibc's malloc arena on Linux
        # (RSS climbed 1.4 GB / 30s on 5M runs through inner partition_by).
        # `slice` is a zero-copy view over the sorted parent.
        sorted_df = bucket_df.sort("__block_key__")
        sizes = (
            sorted_df.lazy()
            .group_by("__block_key__", maintain_order=True)
            .agg(pl.len().alias("__size__"))
            .collect()
        )
        if sizes.height == 0:
            return [], 0
        size_col = sizes["__size__"]
        key_col = sizes["__block_key__"]
        local_pairs: list[tuple[int, int, float]] = []
        local_blocks = 0
        offset = 0
        for i in range(sizes.height):
            size = int(size_col[i])
            if size >= 2:
                block_df = sorted_df.slice(offset, size)
                block_key = key_col[i]
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
                local_pairs.extend(pairs)
                local_blocks += 1
            offset += size
        return local_pairs, local_blocks

    all_pairs: list[tuple[int, int, float]] = []
    total_blocks_scored = 0

    with stage("bucket_score"):
        # rapidfuzz.cdist releases the GIL inside _score_one_block, so threads
        # give real parallelism. Mirror score_blocks_parallel's worker cap.
        max_workers = min(n_non_empty_buckets, os.cpu_count() or 4)
        if max_workers <= 1 or n_non_empty_buckets <= 2:
            for bucket_df in non_empty_buckets:
                pairs, n = _score_one_bucket(bucket_df)
                all_pairs.extend(pairs)
                total_blocks_scored += n
        else:
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                for pairs, n in pool.map(_score_one_bucket, non_empty_buckets):
                    all_pairs.extend(pairs)
                    total_blocks_scored += n
        for a, b, _s in all_pairs:
            matched_pairs.add((min(a, b), max(a, b)))

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
