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

# Per-task CPU reservation for the scoring map_batches call. Defaults to 4
# because each task runs the full in-memory bucket backend over its partition,
# which allocates ~5 GB peak on a ~3M-row partition (50M dataset / 16
# partitions). On a 16-vCPU / 64 GB runner that caps concurrency at 4 tasks
# (~20 GB worker RAM) with headroom for the object store + driver. The
# 2026-05-20 simulated bench OOM'd with the default num_cpus=1 -- Ray packed
# 7 concurrent _score_partition tasks at ~5 GB each, exhausting node memory.
# Override via env var when running on different shapes.
_SCORE_NUM_CPUS = int(os.environ.get("GOLDENMATCH_DISTRIBUTED_SCORE_NUM_CPUS", "4"))


def score_blocks_distributed(
    df_ds: Dataset,
    config: GoldenMatchConfig,
) -> Dataset:
    """Per-partition fuzzy + exact scoring via in-memory dedupe_df.

    Returns a Ray Dataset of {id_a, id_b, score} rows. Cross-partition
    collisions stay; caller invokes dedup_pairs_distributed to canonicalize.
    """

    def _score_partition(batch: Any) -> Any:  # batch: pa.Table -> pa.Table
        import copy

        import polars as pl
        import pyarrow as pa

        import goldenmatch as gm

        df = pl.from_arrow(batch)
        assert isinstance(df, pl.DataFrame)
        if df.height < 2:
            return pa.table({"id_a": [], "id_b": [], "score": []})

        # Force the in-memory bucket backend so the per-partition scorer
        # doesn't recursively try to distribute.
        if hasattr(config, "model_copy"):
            local_cfg = config.model_copy()
        else:
            local_cfg = copy.deepcopy(config)
        local_cfg.backend = "bucket"

        try:
            result = gm.dedupe_df(df, config=local_cfg, confidence_required=False)
        except Exception as e:
            logger.warning("partition scoring failed: %s", e)
            return pa.table({"id_a": [], "id_b": [], "score": []})

        pairs = result.scored_pairs
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
