"""Distributed scoring via per-partition dedupe + cross-partition pair dedup.

Phase 5 of the Splink-Spark parity roadmap. See
docs/superpowers/specs/2026-05-19-phase-5-multi-node-parity-design.md.

Strategy: each partition runs the full in-memory dedupe_df pipeline up
through scoring (cheap on a small partition), and we emit the resulting
scored_pairs list as rows. Cross-partition collisions are deduped by
dedup_pairs_distributed.

This is intentionally coarse -- we don't try to distribute scoring at a
finer granularity than partition. The win at scale is that each
partition's scorer runs in parallel on a different worker.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pyarrow as pa  # noqa: F401  used in inline type annotation comments
    from ray.data import Dataset

    from goldenmatch.config.schemas import GoldenMatchConfig

logger = logging.getLogger(__name__)

# Per-task CPU reservation for the scoring map_batches call.
#
# History:
#   - num_cpus=1 (original, pre-PR #395): OOM at 50M -- 7 concurrent
#     gm.dedupe_df calls @ ~5 GB each = ~35 GB worker RAM > 64 GB cap.
#   - num_cpus=4 (PR #395): Ray reserved 4 CPU for downstream HashAggregate,
#     leaving only 4 for scoring -> 1 task at a time -> 0% progress at 52 min.
#   - num_cpus=1 + narrow kernel (PR #397, #396): OOM at 50M -- the kernel
#     stripped controller/clustering/golden but didn't touch score_buckets,
#     which still allocates ~5-9 GB cdist matrices per partition. 7 concurrent
#     * ~5 GB = ~30 GB > 64 GB - object_store(8 GB) - parquet(4.4 GB) - driver.
#   - num_cpus=2 (current): 8 free CPU / 2 = 4 concurrent * ~5 GB = ~20 GB
#     worker RAM. Fits with headroom. Trades parallelism for survival.
#
# The real fix for 50M-on-64GB is smaller n_buckets inside score_buckets so
# per-partition cdist matrices stay small. That's a separate lift (#???).
# This setting is the practical knob until then.
#
# Override via GOLDENMATCH_DISTRIBUTED_SCORE_NUM_CPUS for different shapes.
_SCORE_NUM_CPUS = int(os.environ.get("GOLDENMATCH_DISTRIBUTED_SCORE_NUM_CPUS", "2"))


def score_blocks_distributed(
    df_ds: Dataset,
    config: GoldenMatchConfig,
) -> Dataset:
    """Per-partition fuzzy + exact scoring via the narrow scoring kernel.

    Returns a Ray Dataset of {id_a, id_b, score} rows. Cross-partition
    collisions stay; caller invokes dedup_pairs_distributed to canonicalize.

    Each worker runs ``_score_partition_with_config`` -- scoring only,
    no controller, no clustering, no golden records. The driver auto-
    configures once on a sample (Phase 2) before dispatch; workers
    receive the committed config and execute the cheap scoring kernel.
    """

    def _score_partition(batch: Any) -> Any:  # batch: pa.Table -> pa.Table
        import copy

        import polars as pl
        import pyarrow as pa

        from goldenmatch.core.pipeline import _score_partition_with_config

        df = pl.from_arrow(batch)
        assert isinstance(df, pl.DataFrame)
        if df.height < 2:
            return pa.table({"id_a": [], "id_b": [], "score": []})

        # Force the in-memory bucket backend so the per-partition scorer
        # doesn't recursively try to distribute. Kernel honors this too.
        if hasattr(config, "model_copy"):
            local_cfg = config.model_copy()
        else:
            local_cfg = copy.deepcopy(config)
        local_cfg.backend = "bucket"

        try:
            pairs = _score_partition_with_config(df, local_cfg)
        except Exception as e:
            logger.warning("partition scoring failed: %s", e)
            return pa.table({"id_a": [], "id_b": [], "score": []})

        if not pairs:
            return pa.table({"id_a": [], "id_b": [], "score": []})
        return pa.table({
            "id_a":  [int(a) for a, _b, _s in pairs],
            "id_b":  [int(b) for _a, b, _s in pairs],
            "score": [float(s) for _a, _b, s in pairs],
        })

    logger.info(
        "score_blocks_distributed: dispatching with num_cpus=%d per task "
        "(GOLDENMATCH_DISTRIBUTED_SCORE_NUM_CPUS to override)",
        _SCORE_NUM_CPUS,
    )
    return df_ds.map_batches(
        _score_partition,
        batch_format="pyarrow",
        num_cpus=_SCORE_NUM_CPUS,
    )


def dedup_pairs_distributed(pairs_ds: Dataset) -> Dataset:
    """Cross-partition pair dedup. Canonicalizes (id_a, id_b) to (min, max)
    and keeps the maximum score per canonical pair.

    Note: Ray's groupby column-output naming varies by version. Output
    schema is {id_a, id_b, score}; normalization handled inline.
    """

    def _canonicalize(batch: Any) -> Any:  # batch: pa.Table -> pa.Table
        import polars as pl
        df = pl.from_arrow(batch)
        assert isinstance(df, pl.DataFrame)
        out = df.with_columns([
            pl.min_horizontal("id_a", "id_b").alias("id_a"),
            pl.max_horizontal("id_a", "id_b").alias("id_b"),
        ])
        return out.to_arrow()

    canonical = pairs_ds.map_batches(_canonicalize, batch_format="pyarrow")

    grouped = canonical.groupby(["id_a", "id_b"]).max("score")

    # Normalize "max(score)" / "score_max" -> "score".
    def _rename(batch: pa.Table) -> pa.Table:
        cols = batch.column_names
        new_cols = []
        for c in cols:
            if c in ("id_a", "id_b"):
                new_cols.append(c)
            else:
                new_cols.append("score")
        return batch.rename_columns(new_cols)

    return grouped.map_batches(_rename, batch_format="pyarrow")
