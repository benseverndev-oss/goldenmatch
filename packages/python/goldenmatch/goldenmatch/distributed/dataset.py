"""Partition-aware data loader on Ray Datasets.

Phase 1 of the Splink-Spark parity roadmap. See
docs/superpowers/specs/2026-05-19-ray-splink-spark-parity-roadmap.md.
"""
from __future__ import annotations


def read_csv_partitioned(
    path: str | list[str],
    n_partitions: int,
    schema: dict[str, str] | None = None,
) -> object:
    del path, n_partitions, schema
    raise NotImplementedError("Implemented in Task 2")


def apply_transforms_distributed(ds: object, transforms: list[object]) -> object:
    del ds, transforms
    raise NotImplementedError("Implemented in Task 7")
