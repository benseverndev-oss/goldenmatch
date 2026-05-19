"""Partition-aware data loader on Ray Datasets.

Phase 1 of the Splink-Spark parity roadmap. See
docs/superpowers/specs/2026-05-19-ray-splink-spark-parity-roadmap.md.

Ray is an optional dependency (`goldenmatch[ray]`). All ray + pyarrow imports
are deferred to function bodies so module-level `from goldenmatch.distributed
import ...` succeeds even when the [ray] extra isn't installed — callers that
actually invoke these functions still need ray on the path.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ray.data import Dataset


def read_csv_partitioned(
    path: str | list[str],
    n_partitions: int,
    schema: dict[str, str] | None = None,
) -> Dataset:
    """Read CSV(s) into a Ray Dataset partitioned into n_partitions blocks.

    Returns a LAZY Dataset — Ray defers execution until the caller forces it
    (via .count(), .take(), .write_*, etc). This is the load-bearing property
    of Phase 1: the driver never holds the full frame.

    schema: optional column projection. When provided, only the listed columns
    are kept (Arrow-level projection, no driver-side materialization).
    """
    import ray

    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True, log_to_driver=False)
    paths = path if isinstance(path, list) else [path]
    ds = ray.data.read_csv(paths)
    if schema is not None:
        ds = ds.select_columns(list(schema.keys()))
    return ds.repartition(n_partitions)


def _apply_plans_to_arrow_batch(batch: Any, plans: list) -> Any:
    """Convert pyarrow batch -> Polars -> apply plans -> back to pyarrow."""
    import polars as pl

    from goldenmatch.distributed.transforms import apply_plan

    df = pl.from_arrow(batch)
    for plan in plans:
        df = apply_plan(df, plan)
    return df.to_arrow()


def apply_transforms_distributed(ds: Dataset, transforms: list[object]) -> Dataset:
    """Apply a list of TransformPlans to a Ray Dataset, one batch at a time.

    Each partition is processed independently: pyarrow batch -> Polars DataFrame ->
    apply each plan -> pyarrow batch. Driver does NOT materialize.
    """
    if not transforms:
        return ds
    return ds.map_batches(
        lambda b: _apply_plans_to_arrow_batch(b, transforms),
        batch_format="pyarrow",
    )
