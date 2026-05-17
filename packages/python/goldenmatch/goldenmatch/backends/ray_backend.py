"""Ray distributed backend for large-scale entity resolution.

Replaces ThreadPoolExecutor block scoring with Ray distributed tasks.
Each block is scored as an independent Ray task, enabling parallelism
across all CPU cores (local) or a Ray cluster (distributed).

Usage:
    pip install goldenmatch[ray]
    goldenmatch dedupe huge.parquet --backend ray
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import polars as pl

logger = logging.getLogger(__name__)

_ray = None


_PAIR_BYTES_ESTIMATE = 80
"""Approx bytes per scored pair (3-tuple of int, int, float) in a flat
list. CPython tuple header ~56 bytes + ints + float. Conservative;
underestimating would let the driver-OOM guard fire late.

Used by Phase 3's incremental ray.wait gather to project cumulative
pair memory against psutil.virtual_memory().available * 0.5.
"""


@dataclass(frozen=True)
class _KeyModeBlock:
    """Minimal block shim used by the key-mode Ray task (defined inside
    score_blocks_ray).

    _score_one_block (core/scorer.py) only reads .block_key + .df +
    .pre_scored_pairs; this matches that contract without dragging in
    BlockResult's multi-pass fields (strategy, depth, parent_key)
    which key-mode v1 doesn't support. Module-level so Ray pickling
    resolves it on workers — a nested class breaks serialization.
    """
    block_key: str
    df: pl.LazyFrame
    pre_scored_pairs: list | None = None


def _ensure_ray():
    """Import and initialize Ray lazily."""
    global _ray
    if _ray is not None:
        return _ray
    try:
        import ray
        _ray = ray
        if not ray.is_initialized():
            ray.init(ignore_reinit_error=True, logging_level=logging.WARNING)
            logger.info(
                "Ray initialized: %d CPUs, %s",
                ray.cluster_resources().get("CPU", 0),
                "local" if ray.util.client.ray.is_connected() is False else "cluster",
            )
        return ray
    except ImportError:
        raise ImportError(
            "Ray backend requires ray. Install with: pip install goldenmatch[ray]"
        )


def score_blocks_ray(
    blocks: list,
    mk,  # noqa: F821  # forward ref, resolved lazily via __future__ annotations
    matched_pairs: set[tuple[int, int]],
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
    target_ids: set[int] | None = None,
    *,
    store_path: str | None = None,
    signature: str | None = None,
) -> list[tuple[int, int, float]]:
    """Score all blocks using Ray distributed tasks.

    Drop-in replacement for score_blocks_parallel. Each block is submitted
    as an independent Ray task. Ray handles scheduling across all available
    CPU cores (local mode) or cluster nodes.

    Args:
        blocks: List of BlockResult objects.
        mk: Matchkey configuration.
        matched_pairs: Set of already-matched (min_id, max_id) pairs.
        across_files_only: Filter to cross-source pairs only.
        source_lookup: Row ID to source name mapping.
        target_ids: For match mode — filter to target/ref cross pairs.
        store_path: Path to the PreparedRecordStore DuckDB file. When set
            (alongside ``signature``), activates bucket-mode dispatch: each
            Ray task loads one bucket Parquet from the sibling ``buckets_*``
            directory instead of receiving an in-memory block.
        signature: Config signature used to locate the ``buckets_<sig>``
            directory. Must be set alongside ``store_path`` to activate
            bucket-mode.

    Returns:
        All fuzzy pairs found across blocks.
    """
    # Short-circuit BEFORE _ensure_ray() so callers with no blocks (and
    # potentially no Ray install) don't trigger a lazy Ray import + init.
    if not blocks:
        return []

    ray = _ensure_ray()

    # For very small block counts, use the regular scorer (no Ray overhead)
    if len(blocks) <= 4:
        from goldenmatch.core.scorer import score_blocks_parallel
        return score_blocks_parallel(
            blocks, mk, matched_pairs,
            across_files_only=across_files_only,
            source_lookup=source_lookup,
            target_ids=target_ids,
        )

    from goldenmatch.core.scorer import _score_one_block

    # Freeze exclude pairs for immutable sharing
    frozen_exclude = frozenset(matched_pairs)

    # Put shared data in Ray object store (zero-copy for large objects)
    mk_ref = ray.put(mk)
    exclude_ref = ray.put(frozen_exclude)
    source_ref = ray.put(source_lookup) if source_lookup else None

    @ray.remote
    def _score_block_remote(block, mk_config, exclude, across_only, src_lookup):
        """Ray remote task: score one block."""
        return _score_one_block(
            block, mk_config, exclude,
            across_files_only=across_only,
            source_lookup=src_lookup,
        )

    @ray.remote(max_retries=0)  # type: ignore[misc]
    def _score_block_remote_by_bucket(
        bucket_path: str,
        mk_config,  # pyright: ignore[reportMissingParameterType]
        exclude,  # pyright: ignore[reportMissingParameterType]
        src_lookup,  # pyright: ignore[reportMissingParameterType]
        across_only: bool,
    ):
        """Component 2 v2 / Component 3: worker loads one bucket
        Parquet, recovers per-block grouping via partition_by, scores
        each block, returns concatenated pairs."""
        from pathlib import Path as _Path

        from goldenmatch.core.scorer import _score_one_block
        from goldenmatch.distributed.record_store import load_bucket

        bucket_df = load_bucket(_Path(bucket_path))
        all_pairs: list[tuple[int, int, float]] = []
        for block_key, block_df in bucket_df.partition_by(
            "__block_key__", as_dict=True,
        ).items():
            # block_key arrives as tuple under Polars >= 1.0 partition_by.
            if isinstance(block_key, tuple):
                block_key = block_key[0]
            shim = _KeyModeBlock(block_key=str(block_key), df=block_df.lazy())
            all_pairs.extend(
                _score_one_block(
                    shim, mk_config, exclude,
                    across_files_only=across_only, source_lookup=src_lookup,
                )
            )
        return all_pairs

    use_bucket_mode = store_path is not None and signature is not None

    futures = []
    if use_bucket_mode:
        from pathlib import Path as _Path

        from goldenmatch.distributed.record_store import (
            _sanitize_signature,
            iter_buckets,
        )
        sig_hash = _sanitize_signature(signature)  # type: ignore[arg-type]
        bucket_dir = _Path(store_path).parent / f"buckets_{sig_hash}"  # type: ignore[arg-type]
        for _bucket_id, bucket_path in iter_buckets(bucket_dir):
            future = _score_block_remote_by_bucket.remote(  # type: ignore[attr-defined]
                str(bucket_path),
                mk_ref, exclude_ref, source_ref,
                across_files_only,
            )
            futures.append(future)
    else:
        # df-mode unchanged from Component 3 v1.
        for block in blocks:
            if hasattr(block, 'df') and hasattr(block.df, 'collect'):
                collected_block = type(block)(
                    block_key=block.block_key,
                    df=block.df.collect().lazy(),
                    strategy=block.strategy,
                    depth=getattr(block, 'depth', 0),
                    parent_key=getattr(block, 'parent_key', None),
                    pre_scored_pairs=getattr(block, 'pre_scored_pairs', None),
                )
            else:
                collected_block = block
            future = _score_block_remote.remote(  # type: ignore[attr-defined]
                collected_block, mk_ref, exclude_ref,
                across_files_only, source_ref,
            )
            futures.append(future)

    logger.info(
        "Submitted %d %s to Ray (%s mode, %d CPUs available)",
        len(futures),
        "buckets" if use_bucket_mode else "blocks",
        "bucket" if use_bucket_mode else "df",
        int(ray.cluster_resources().get("CPU", 0)),
    )

    # Incremental gather with driver-OOM guard.
    import psutil
    budget_bytes = psutil.virtual_memory().available * 0.5
    budget_pairs = int(budget_bytes // _PAIR_BYTES_ESTIMATE)

    all_pairs: list[tuple[int, int, float]] = []
    remaining = list(futures)
    n_pairs = 0
    while remaining:
        ready, remaining = ray.wait(remaining, num_returns=1)
        block_pairs = ray.get(ready[0])
        if target_ids is not None:
            block_pairs = [
                (a, b, s) for a, b, s in block_pairs
                if (a in target_ids) != (b in target_ids)
            ]
        all_pairs.extend(block_pairs)
        for a, b, s in block_pairs:
            matched_pairs.add((min(a, b), max(a, b)))
        n_pairs += len(block_pairs)
        if n_pairs > budget_pairs:
            for f in remaining:
                try:
                    ray.cancel(f)
                except Exception:  # noqa: BLE001 -- best-effort cleanup
                    pass
            raise MemoryError(
                f"Component 3: scored pairs ({n_pairs:,}) would exceed "
                f"50% of available driver RAM "
                f"({int(budget_bytes // (1024 * 1024))} MB budget, "
                f"~{_PAIR_BYTES_ESTIMATE} bytes/pair) -- switch to "
                f"backend='chunked' or wait for Component 4 "
                f"(streaming pair store)"
            )
    return all_pairs


def shutdown_ray():
    """Shut down the Ray runtime if initialized."""
    global _ray
    if _ray is not None and _ray.is_initialized():
        _ray.shutdown()
        logger.info("Ray shut down")
    _ray = None
