"""Distributed golden record build via repartition(keys) + map_batches.

Phase 4 of the Splink-Spark parity roadmap. See
docs/superpowers/specs/2026-05-19-phase-4-distributed-golden-design.md.

All ray imports are deferred to function bodies so module import succeeds
without the [ray] extra installed.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import polars as pl
    from ray.data import Dataset

    from goldenmatch.config.schemas import GoldenRulesConfig

logger = logging.getLogger(__name__)

_GOLDEN_CLUSTER_THRESHOLD = 5_000_000


def _golden_cluster_threshold() -> int:
    import os

    raw = os.environ.get("GOLDENMATCH_DISTRIBUTED_GOLDEN_THRESHOLD")
    if raw is None:
        return _GOLDEN_CLUSTER_THRESHOLD
    try:
        return int(raw)
    except ValueError:
        return _GOLDEN_CLUSTER_THRESHOLD


def _per_partition_golden(batch, rules, user_columns):
    """Worker-side: pyarrow batch -> Polars -> in-memory builder -> pyarrow."""
    import polars as pl
    import pyarrow as pa

    from goldenmatch.core.golden import build_golden_records_batch

    df = pl.from_arrow(batch)
    if df.height == 0:
        return pa.Table.from_pylist([])
    results = build_golden_records_batch(df, rules)
    if not results:
        return pa.Table.from_pylist([])
    return pa.Table.from_pylist(results)


def build_golden_records_distributed(
    multi_ds: "Dataset",
    rules: "GoldenRulesConfig",
    *,
    user_columns: list[str],
    max_cluster_size: int = 100,
) -> "Dataset":
    """Distributed golden via repartition(keys=["__cluster_id__"]) + map_batches.

    Co-locates each cluster's rows via hash-partitioning on __cluster_id__,
    guaranteeing all rows for a given cluster land in the same partition.
    map_batches then calls build_golden_records_batch on each partition's
    Polars slice, which groups by __cluster_id__ internally.

    Note: groupby.map_groups is NOT used — in Ray 2.54 it hangs when
    the UDF's streaming executor tries to re-enter ray.data. The
    repartition(keys=[...]) hash-shuffle achieves the same co-location
    guarantee without the re-entrant Dataset issue.
    """
    import ray  # noqa: F401 -- ensures ray.data is initialized when called

    total_rows = multi_ds.count()
    # One partition per ~max_cluster_size rows, so each partition roughly
    # holds one cluster worth of rows. Minimum 1.
    num_partitions = max(1, total_rows // max(max_cluster_size, 1))

    # Hash-partition by __cluster_id__ so all rows for a cluster co-locate.
    repartitioned = multi_ds.repartition(num_partitions, keys=["__cluster_id__"])

    def _process_partition(batch):  # batch is pyarrow.Table
        import polars as pl
        import pyarrow as pa

        from goldenmatch.core.golden import build_golden_records_batch

        df = pl.from_arrow(batch)
        if df.height == 0:
            return pa.Table.from_pylist([])
        results = build_golden_records_batch(df, rules)
        if not results:
            return pa.Table.from_pylist([])
        return pa.Table.from_pylist(results)

    return repartitioned.map_batches(_process_partition, batch_format="pyarrow")


def materialize_golden_dataframe(golden_ds: "Dataset") -> "pl.DataFrame":
    """Adapter back to pl.DataFrame for downstream stages."""
    import polars as pl
    import pyarrow as pa

    tables = list(golden_ds.iter_batches(batch_format="pyarrow"))
    if not tables:
        return pl.DataFrame()
    full = pa.concat_tables(tables)
    return pl.from_arrow(full)


def _collect_and_call_in_memory(multi_ds, rules):
    import polars as pl
    import pyarrow as pa

    from goldenmatch.core.golden import build_golden_records_batch

    tables = list(multi_ds.iter_batches(batch_format="pyarrow"))
    if not tables:
        return []
    df = pl.from_arrow(pa.concat_tables(tables))
    return build_golden_records_batch(df, rules)


def build_golden_records_smart(
    multi_ds: "Dataset",
    rules: "GoldenRulesConfig",
    *,
    user_columns: list[str],
    max_cluster_size: int = 100,
) -> list[dict]:
    """Dispatch by cluster count.

    Below threshold (default 5M clusters): in-memory builder.
    Above threshold: distributed build_golden_records_distributed.

    Custom field rules always route to in-memory (closure serialization risk).
    """
    if rules.field_rules:
        logger.info(
            "build_golden_records_smart: custom field rules configured; "
            "routing to in-memory build_golden_records_batch (Phase 4 "
            "distributes the uniform-strategy fast path only).",
        )
        return _collect_and_call_in_memory(multi_ds, rules)

    threshold = _golden_cluster_threshold()
    # Count distinct __cluster_id__ via groupby + count of resulting rows.
    cluster_count = multi_ds.groupby("__cluster_id__").count().count()

    if cluster_count < threshold:
        logger.info(
            "build_golden_records_smart: %d clusters < %d threshold; "
            "routing to in-memory build_golden_records_batch.",
            cluster_count, threshold,
        )
        return _collect_and_call_in_memory(multi_ds, rules)

    logger.info(
        "build_golden_records_smart: %d clusters >= %d threshold; "
        "routing to distributed golden build.",
        cluster_count, threshold,
    )
    distributed_ds = build_golden_records_distributed(
        multi_ds, rules,
        user_columns=user_columns, max_cluster_size=max_cluster_size,
    )
    return materialize_golden_dataframe(distributed_ds).to_dicts()
