from goldenmatch.core.execution_plan import DistributedRoutingDecision, ExecutionPlan


def test_default_plan_is_all_in_memory():
    p = ExecutionPlan()
    assert p.scoring_distributed is False
    assert p.golden_distributed is False
    assert p.clustering_strategy == "in_memory"
    assert p.routing_decisions == ()


def test_routing_decision_shape():
    d = DistributedRoutingDecision(
        stage="clustering", mode="in_memory", rule_name="cluster_present",
        reason="edge set 1.8GB <= budget 28.8GB", projected_bytes=1_800_000_000,
        budget_bytes=28_800_000_000, overridden=False, override_source=None,
    )
    assert d.stage == "clustering"
    assert d.overridden is False


def test_plan_carries_distributed_wcc():
    p = ExecutionPlan(clustering_strategy="distributed_wcc", scoring_distributed=True)
    assert p.clustering_strategy == "distributed_wcc"
    assert p.scoring_distributed is True
