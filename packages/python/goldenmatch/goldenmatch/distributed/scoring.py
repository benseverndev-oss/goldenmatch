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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pyarrow as pa
    from ray.data import Dataset

    from goldenmatch.config.schemas import GoldenMatchConfig

logger = logging.getLogger(__name__)


def score_blocks_distributed(
    df_ds: "Dataset",
    config: "GoldenMatchConfig",
) -> "Dataset":
    """Per-partition fuzzy + exact scoring via in-memory dedupe_df.

    Returns a Ray Dataset of {id_a, id_b, score} rows. Cross-partition
    collisions stay; caller invokes dedup_pairs_distributed to canonicalize.
    """
    import pyarrow as pa
    import ray  # noqa: F401

    def _score_partition(batch: "pa.Table") -> "pa.Table":
        import copy

        import polars as pl
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

    return df_ds.map_batches(_score_partition, batch_format="pyarrow")


def dedup_pairs_distributed(pairs_ds: "Dataset") -> "Dataset":
    """Cross-partition pair dedup. Canonicalizes (id_a, id_b) to (min, max)
    and keeps the maximum score per canonical pair.

    Note: Ray's groupby column-output naming varies by version. Output
    schema is {id_a, id_b, score}; normalization handled inline.
    """
    import pyarrow as pa
    import ray  # noqa: F401

    def _canonicalize(batch: "pa.Table") -> "pa.Table":
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
    def _rename(batch: "pa.Table") -> "pa.Table":
        cols = batch.column_names
        new_cols = []
        for c in cols:
            if c in ("id_a", "id_b"):
                new_cols.append(c)
            else:
                new_cols.append("score")
        return batch.rename_columns(new_cols)

    return grouped.map_batches(_rename, batch_format="pyarrow")
