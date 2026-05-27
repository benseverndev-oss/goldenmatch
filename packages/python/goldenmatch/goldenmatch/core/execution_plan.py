"""ExecutionPlan dataclass -- the six knobs the controller-v3 planner picks.

Spec §Decision space:
docs/superpowers/specs/2026-05-15-controller-v3-planner-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from goldenmatch.config.schemas import GoldenMatchConfig

BackendName = Literal["polars-direct", "chunked", "duckdb", "ray", "bucket"]
ClusteringStrategy = Literal["in_memory", "partitioned_union_find", "streaming_cc"]
SpillThreshold = Literal["ram", "duckdb", "disk_per_worker"] | None


@dataclass(frozen=True)
class ExecutionPlan:
    """The planner's output: backend selection + tuning knobs.

    Defaults match today's polars-direct path so an unset plan preserves
    current behavior. Frozen -- replace, don't mutate.
    """

    backend: BackendName = "polars-direct"
    chunk_size: int | None = None
    max_workers: int = 4
    pair_spill_threshold: SpillThreshold = None
    clustering_strategy: ClusteringStrategy = "in_memory"
    rule_name: str | None = None

    def apply_to(self, config: GoldenMatchConfig) -> None:
        """Write plan onto a GoldenMatchConfig in place.

        Only ``backend`` lives on the existing config schema today; the
        remaining knobs (chunk_size, max_workers, pair_spill_threshold,
        clustering_strategy) get added in phase 2 once GoldenMatchConfig
        is extended. For phase 1, just wires backend.
        """
        if self.backend != "polars-direct":
            config.backend = self.backend
