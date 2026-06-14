"""Distributed-routing rule layer: a post-pass over the 7 backend rules that
decides scoring / clustering / golden routing per stage, keyed off DRIVER RAM.

Single source of truth for distributed routing. Env-var thresholds and config
pins are honored but recorded as overrides so the MCP linter can flag the ones
that force a slow path at scale.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import replace

from goldenmatch.core.cluster_profile import ClusterProfile
from goldenmatch.core.execution_plan import DistributedRoutingDecision, ExecutionPlan
from goldenmatch.core.runtime_profile import RuntimeProfile

_GIB = 1024 ** 3
# Measured against the validated 100M run (docs/quality-invariant-scale.md):
# an edge is two int32 ids + a float32 score, padded -> 16 bytes.
BYTES_PER_EDGE = 16
# Per-record materialized working set (raw fields + normalized blocking columns
# + scoring scratch) for the scoring frame and the golden survivorship build.
# Calibrated against the validated 100M runs: 100M x 512B = 51GB exceeds a 48GB
# cluster driver (so scoring/golden distribute there) but fits a 256GB single
# box (so the single-box 100M run stays in-memory). Hardware-relative by design.
BYTES_PER_ROW = 512
# Headroom: never plan to fill more than this fraction of driver RAM.
SAFETY = 0.6

ROUTING_DOC_ANCHORS: dict[str, str] = {
    "single_box": "routing-single-box",
    "cluster_present": "routing-driver-ram-projection",
    "user_override": "routing-overrides",
}

# DistributedRoutingConfig pin values -> normalized internal mode.
_PIN_TO_MODE = {
    "distributed": "distributed", "in_process": "in_memory",
    "distributed_wcc": "distributed", "in_memory_scipy": "in_memory",
    "auto": None, None: None,
}


def _driver_avail_ram_gb(runtime: RuntimeProfile | None, cluster: ClusterProfile) -> float:
    if runtime is not None:
        return runtime.available_ram_gb
    return cluster.driver_mem_gb


def _human(n: int) -> str:
    return f"{n / 1e9:.2f}GB"


def _config_pin(routing_config, stage: str):
    """Return (mode, source) for an explicit DistributedRoutingConfig pin."""
    if routing_config is None:
        return None, None
    raw = getattr(routing_config, stage, "auto")
    mode = _PIN_TO_MODE.get(raw)
    if mode is None:
        return None, None
    return mode, f"config:distributed_routing.{stage}={raw}"


def _clustering_env_override(env: Mapping[str, str], pairs: int, projection_distribute: bool):
    raw = env.get("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD")
    if raw is None:
        return None, None
    try:
        thr = int(raw)
    except ValueError:
        return None, None
    env_distribute = pairs >= thr
    if env_distribute == projection_distribute:
        return None, None  # env agrees with the projection; not an override
    mode = "distributed" if env_distribute else "in_memory"
    return mode, f"env:GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD={raw}"


def _decide(stage, *, projected_bytes, budget_bytes, cluster, override_mode, override_source):
    if override_mode is not None:
        return DistributedRoutingDecision(
            stage=stage, mode=override_mode, rule_name="user_override",
            reason=f"{stage} pinned to {override_mode} via {override_source}",
            projected_bytes=projected_bytes, budget_bytes=budget_bytes,
            overridden=True, override_source=override_source,
        )
    if not cluster.present:
        return DistributedRoutingDecision(
            stage=stage, mode="in_memory", rule_name="single_box",
            reason=f"{stage} in-memory: no cluster present",
            projected_bytes=projected_bytes, budget_bytes=budget_bytes,
            overridden=False, override_source=None,
        )
    distribute = projected_bytes > budget_bytes
    mode = "distributed" if distribute else "in_memory"
    op = ">" if distribute else "<="
    return DistributedRoutingDecision(
        stage=stage, mode=mode, rule_name="cluster_present",
        reason=(f"{stage} {mode}: projected {_human(projected_bytes)} {op} "
                f"driver budget {_human(budget_bytes)}"),
        projected_bytes=projected_bytes, budget_bytes=budget_bytes,
        overridden=False, override_source=None,
    )


def apply_distributed_routing(
    plan: ExecutionPlan,
    *,
    runtime: RuntimeProfile | None,
    cluster: ClusterProfile,
    n_rows_full: int,
    estimated_pair_count: int,
    routing_config=None,
    env: Mapping[str, str] | None = None,
) -> ExecutionPlan:
    """Return a new ExecutionPlan with per-stage routing populated."""
    env = os.environ if env is None else env
    budget = int(_driver_avail_ram_gb(runtime, cluster) * _GIB * SAFETY)
    row_bytes = n_rows_full * BYTES_PER_ROW
    edge_bytes = estimated_pair_count * BYTES_PER_EDGE

    # scoring
    s_mode, s_src = _config_pin(routing_config, "scoring")
    scoring = _decide("scoring", projected_bytes=row_bytes, budget_bytes=budget,
                      cluster=cluster, override_mode=s_mode, override_source=s_src)

    # clustering (config pin wins; else env-threshold footgun; else projection)
    c_mode, c_src = _config_pin(routing_config, "clustering")
    if c_mode is None:
        c_mode, c_src = _clustering_env_override(
            env, estimated_pair_count,
            projection_distribute=(cluster.present and edge_bytes > budget))
    clustering = _decide("clustering", projected_bytes=edge_bytes, budget_bytes=budget,
                         cluster=cluster, override_mode=c_mode, override_source=c_src)

    # golden
    g_mode, g_src = _config_pin(routing_config, "golden")
    golden = _decide("golden", projected_bytes=row_bytes, budget_bytes=budget,
                     cluster=cluster, override_mode=g_mode, override_source=g_src)

    return replace(
        plan,
        scoring_distributed=(scoring.mode == "distributed"),
        clustering_strategy=("distributed_wcc" if clustering.mode == "distributed" else "in_memory"),
        golden_distributed=(golden.mode == "distributed"),
        routing_decisions=(scoring, clustering, golden),
    )
