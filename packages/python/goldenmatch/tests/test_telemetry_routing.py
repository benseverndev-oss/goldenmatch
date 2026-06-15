from types import SimpleNamespace

from goldenmatch.core.execution_plan import DistributedRoutingDecision, ExecutionPlan
from goldenmatch.web.controller_telemetry import serialize_telemetry


def test_execution_plan_includes_routing():
    dec = DistributedRoutingDecision(
        stage="clustering", mode="in_memory", rule_name="cluster_present",
        reason="edge set 1.76GB <= budget 28.8GB", projected_bytes=1_760_000_000,
        budget_bytes=28_800_000_000, overridden=False, override_source=None)
    plan = ExecutionPlan(scoring_distributed=True, routing_decisions=(dec,))
    history = SimpleNamespace(execution_plan=plan)
    body = serialize_telemetry(
        profile=None, history=history, committed_config=None,
        source=None, run_name=None, recorded_at=None)
    ep = body["execution_plan"]
    assert ep["scoring_distributed"] is True
    assert ep["routing"][0]["stage"] == "clustering"
    assert ep["routing"][0]["reason"].startswith("edge set")
