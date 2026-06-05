"""Distributed execution primitives for 50M+ row deduplication.

Spec: docs/superpowers/specs/2026-05-15-distributed-plan-v1-design.md.

Component 1 (prepared-record store) is the first piece. Components 2–6
(partitioned execution, distributed scoring, streaming pair store,
distributed clustering, planner integration) ship as their own sub-projects.
"""

from goldenmatch.distributed._utils import is_ray_dataset
from goldenmatch.distributed.clustering import (
    build_clusters_distributed,
    local_cc_assignments,
    materialize_cluster_dict,
    pairs_list_to_dataset,
    two_phase_wcc,
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
from goldenmatch.distributed.identity import (
    materialize_identity_assignments,
    resolve_identities_distributed,
)
from goldenmatch.distributed.sample import take_sample_distributed
from goldenmatch.distributed.scoring import (
    dedup_pairs_distributed,
    score_blocks_distributed,
)

__all__ = [
    "apply_transforms_distributed",
    "build_clusters_distributed",
    "build_golden_records_distributed",
    "build_golden_records_smart",
    "dedup_pairs_distributed",
    "is_ray_dataset",
    "local_cc_assignments",
    "materialize_cluster_dict",
    "materialize_golden_dataframe",
    "materialize_identity_assignments",
    "pairs_list_to_dataset",
    "read_csv_partitioned",
    "read_parquet_partitioned",
    "read_partitioned",
    "resolve_identities_distributed",
    "score_blocks_distributed",
    "take_sample_distributed",
    "two_phase_wcc",
]
