"""Pure routing-projection helpers behind the plan/explain/lint MCP tools.

These consume numeric profile inputs (n_rows, estimated_pair_count, an optional
cluster descriptor + driver RAM) rather than re-running the controller --
``auto_configure`` remains the sample->plan entry; these expose the routing
decision / explanation / lint standalone over the same rule layer.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from mcp.types import Tool

from goldenmatch.core.cluster_profile import capture_cluster_profile
from goldenmatch.core.distributed_routing_rules import (
    _projection_mode,
    apply_distributed_routing,
    slow_path_overrides,
)
from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.core.runtime_profile import RuntimeProfile

_REFUSE_AT_N = 100_000  # mirrors core.autoconfig_controller.REFUSE_AT_N (asserted in tests)


def _build_plan(n_rows, estimated_pair_count, cluster, driver_mem_gb, config, env):
    cluster_profile = capture_cluster_profile(descriptor=cluster, ray_module=None)
    runtime = (
        RuntimeProfile(available_ram_gb=driver_mem_gb, cpu_count=1, disk_free_gb=0.0)
        if driver_mem_gb is not None else None
    )
    return apply_distributed_routing(
        ExecutionPlan(), runtime=runtime, cluster=cluster_profile,
        n_rows_full=n_rows, estimated_pair_count=estimated_pair_count,
        routing_config=config, env=env or {},
    )


def run_plan_routing(*, n_rows, estimated_pair_count, cluster=None,
                     driver_mem_gb=None, config=None, env=None) -> dict[str, Any]:
    plan = _build_plan(n_rows, estimated_pair_count, cluster, driver_mem_gb, config, env)
    return {
        "clustering_strategy": plan.clustering_strategy,
        "scoring_distributed": plan.scoring_distributed,
        "golden_distributed": plan.golden_distributed,
        "routing": [asdict(d) for d in plan.routing_decisions],
    }


def run_explain_routing(*, n_rows, estimated_pair_count, cluster=None,
                        driver_mem_gb=None, config=None, env=None) -> dict[str, Any]:
    plan = _build_plan(n_rows, estimated_pair_count, cluster, driver_mem_gb, config, env)
    lines = [f"- {d.reason} (rule {d.rule_name})" for d in plan.routing_decisions]
    return {"explanation": "\n".join(lines),
            "routing": [asdict(d) for d in plan.routing_decisions]}


def run_lint_routing(*, n_rows, estimated_pair_count, cluster=None,
                     driver_mem_gb=None, config=None, env=None) -> dict[str, Any]:
    plan = _build_plan(n_rows, estimated_pair_count, cluster, driver_mem_gb, config, env)
    at_scale = n_rows >= _REFUSE_AT_N
    findings = []
    for d in plan.routing_decisions:
        if not d.overridden:
            continue
        slow = d.mode != _projection_mode(d)
        sev = "ERROR" if (slow and at_scale) else "WARN" if slow else "INFO"
        findings.append({
            "stage": d.stage, "severity": sev, "mode": d.mode,
            "projection": _projection_mode(d), "source": d.override_source,
            "message": (f"{d.stage} forced {d.mode} via {d.override_source}; "
                        f"projection says {_projection_mode(d)}."),
        })
    would_refuse = at_scale and bool(slow_path_overrides(plan))
    return {"findings": findings, "would_refuse": would_refuse}


_CLUSTER_SCHEMA = {
    "type": "object",
    "properties": {
        "num_nodes": {"type": "integer"}, "total_cpus": {"type": "integer"},
        "cluster_mem_gb": {"type": "number"}, "driver_mem_gb": {"type": "number"},
    },
}
_BASE_PROPS = {
    "n_rows": {"type": "integer"},
    "estimated_pair_count": {"type": "integer"},
    "cluster": _CLUSTER_SCHEMA,
    "driver_mem_gb": {"type": "number"},
}

ROUTING_TOOLS = [
    Tool(name="plan_routing",
         description="Project per-stage distributed routing (scoring/clustering/golden) "
                     "for a given data shape + cluster. Pure; no controller run.",
         inputSchema={"type": "object", "properties": _BASE_PROPS,
                      "required": ["n_rows", "estimated_pair_count"]}),
    Tool(name="explain_routing",
         description="Human-readable explanation of why each stage is routed the way it is, "
                     "with the driver-RAM projection that drove it.",
         inputSchema={"type": "object", "properties": _BASE_PROPS,
                      "required": ["n_rows", "estimated_pair_count"]}),
    Tool(name="lint_routing",
         description="Flag config/env overrides that force a slow path (e.g. "
                     "CLUSTERING_THRESHOLD=0 when the edge set fits driver RAM). "
                     "ERROR at scale; would_refuse mirrors the runtime guard.",
         inputSchema={"type": "object",
                      "properties": {**_BASE_PROPS, "env": {"type": "object"}},
                      "required": ["n_rows", "estimated_pair_count"]}),
]

_DISPATCH = {
    "plan_routing": run_plan_routing,
    "explain_routing": run_explain_routing,
    "lint_routing": run_lint_routing,
}

ROUTING_TOOL_NAMES = frozenset(_DISPATCH)


def handle_routing_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _DISPATCH[name](**arguments)
