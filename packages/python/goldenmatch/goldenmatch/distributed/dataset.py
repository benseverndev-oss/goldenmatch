"""Partition-aware data loader on Ray Datasets.

Phase 1 of the Splink-Spark parity roadmap. See
docs/superpowers/specs/2026-05-19-ray-splink-spark-parity-roadmap.md.
"""
from __future__ import annotations

import ray
from ray.data import Dataset


def read_csv_partitioned(
    path: str | list[str],
    n_partitions: int,
    schema: dict[str, str] | None = None,
) -> Dataset:
    """Read CSV(s) into a Ray Dataset partitioned into n_partitions blocks.

    Returns a MaterializedDataset so callers can inspect num_blocks() without
    triggering a second execution pass.  Ray 2.x requires materialization for
    the block count to be stable; the data is stored in the object store (not
    the driver heap), so this does NOT pull the full frame into driver memory.
    """
    if not ray.is_initialized():
        ray.init(ignore_reinit_error=True, log_to_driver=False)
    paths = path if isinstance(path, list) else [path]
    ds: Dataset = ray.data.read_csv(paths)
    ds = ds.repartition(n_partitions)
    return ds.materialize()


def apply_transforms_distributed(ds: Dataset, transforms: list[object]) -> Dataset:
    del ds, transforms
    raise NotImplementedError("Implemented in Task 7")
