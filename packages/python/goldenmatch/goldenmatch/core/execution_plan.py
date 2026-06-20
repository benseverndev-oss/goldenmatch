"""ExecutionPlan dataclass -- the six knobs the controller-v3 planner picks.

Spec §Decision space:
docs/superpowers/specs/2026-05-15-controller-v3-planner-design.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Optional

if TYPE_CHECKING:
    from goldenmatch.config.schemas import GoldenMatchConfig

BackendName = Literal["polars-direct", "chunked", "duckdb", "ray", "bucket"]
ClusteringStrategy = Literal[
    "in_memory", "partitioned_union_find", "streaming_cc", "distributed_wcc"
]
SpillThreshold = Literal["ram", "duckdb", "disk_per_worker"] | None


@dataclass(frozen=True)
class DistributedRoutingDecision:
    """One per-stage routing decision + the projection that drove it.

    ``mode`` is the normalized vocabulary "distributed" | "in_memory".
    ``projected_bytes`` is the stage's working-set estimate; ``budget_bytes``
    is the driver-RAM budget it was compared against. ``overridden`` marks a
    user/env override that the linter surfaces.
    """

    stage: str            # "scoring" | "clustering" | "golden"
    mode: str             # "distributed" | "in_memory"
    rule_name: str        # "user_override" | "single_box" | "cluster_present"
    reason: str
    projected_bytes: int
    budget_bytes: int
    overridden: bool
    override_source: str | None


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
    scoring_distributed: bool = False
    golden_distributed: bool = False
    routing_decisions: tuple[DistributedRoutingDecision, ...] = ()
    verify_mode: Literal["full", "sketch_distance"] = "full"
    sketch_bands: Optional[int] = None
    sketch_rows: Optional[int] = None
    sketch_similarity: Optional[float] = None
    sketch_metric: Optional[str] = None

    def apply_to(self, config: GoldenMatchConfig) -> None:
        """Write plan onto a GoldenMatchConfig in place.

        Only ``backend`` lives on the existing config schema today; the
        remaining knobs (chunk_size, max_workers, pair_spill_threshold,
        clustering_strategy) get added in phase 2 once GoldenMatchConfig
        is extended. For phase 1, just wires backend.
        """
        if self.backend != "polars-direct":
            config.backend = self.backend
        if self.verify_mode != "full":
            config._throughput_plan = self