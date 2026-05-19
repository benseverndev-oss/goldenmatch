"""Distributed execution primitives for 50M+ row deduplication.

Spec: docs/superpowers/specs/2026-05-15-distributed-plan-v1-design.md.

Component 1 (prepared-record store) is the first piece. Components 2–6
(partitioned execution, distributed scoring, streaming pair store,
distributed clustering, planner integration) ship as their own sub-projects.
"""

from goldenmatch.distributed.dataset import (
    apply_transforms_distributed,
    read_csv_partitioned,
    read_parquet_partitioned,
    read_partitioned,
)

__all__ = [
    "apply_transforms_distributed",
    "read_csv_partitioned",
    "read_parquet_partitioned",
    "read_partitioned",
]
