"""Distributed execution primitives for 50M+ row deduplication.

Spec: docs/superpowers/specs/2026-05-15-distributed-plan-v1-design.md.

Component 1 (prepared-record store) is the first piece. Components 2–6
(partitioned execution, distributed scoring, streaming pair store,
distributed clustering, planner integration) ship as their own sub-projects.
"""

from goldenmatch.distributed._utils import is_ray_dataset
from goldenmatch.distributed.clustering import (
    build_clusters_distributed,
    materialize_cluster_dict,
    pairs_list_to_dataset,
)
from goldenmatch.distributed.dataset import (
    apply_transforms_distributed,
    read_csv_partitioned,
    read_parquet_partitioned,
    read_partitioned,
)
from goldenmatch.distributed.golden import (
    build_golden_records_distributed,
    build_golden_records_smart,
    materialize_golden_dataframe,
)
from goldenmatch.distributed.sample import take_sample_distributed

__all__ = [
    "apply_transforms_distributed",
    "build_clusters_distributed",
    "build_golden_records_distributed",
    "build_golden_records_smart",
    "is_ray_dataset",
    "materialize_cluster_dict",
    "materialize_golden_dataframe",
    "pairs_list_to_dataset",
    "read_csv_partitioned",
    "read_parquet_partitioned",
    "read_partitioned",
    "take_sample_distributed",
]
