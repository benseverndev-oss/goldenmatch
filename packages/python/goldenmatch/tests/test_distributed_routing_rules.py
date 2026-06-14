from goldenmatch.core.cluster_profile import ClusterProfile, capture_cluster_profile
from goldenmatch.core.distributed_routing_rules import apply_distributed_routing
from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.core.runtime_profile import RuntimeProfile


def _runtime(gb):
    return RuntimeProfile(available_ram_gb=gb, cpu_count=16, disk_free_gb=500.0)


def _cluster():
    return ClusterProfile(present=True, num_nodes=4, total_cpus=80,
                          cluster_mem_gb=256.0, driver_mem_gb=48.0, source="descriptor")


def test_single_box_keeps_everything_in_memory():
    plan = apply_distributed_routing(
        ExecutionPlan(), runtime=_runtime(48.0),
        cluster=capture_cluster_profile(descriptor=None, ray_module=None),
        n_rows_full=100_000_000, estimated_pair_count=110_000_000,
    )
    assert plan.clustering_strategy == "in_memory"
    assert plan.scoring_distributed is False
    assert plan.golden_distributed is False
    assert all(d.rule_name == "single_box" for d in plan.routing_decisions)


def test_100m_edge_set_fits_driver_ram_clustering_in_memory():
    # 110M edges * 16B = 1.76GB << 48GB driver * 0.6 budget => in-memory.
    plan = apply_distributed_routing(
        ExecutionPlan(), runtime=_runtime(48.0), cluster=_cluster(),
        n_rows_full=100_000_000, estimated_pair_count=110_000_000,
    )
    assert plan.clustering_strategy == "in_memory"
    assert plan.scoring_distributed is True  # 100M-row frame ~51GB > 48GB driver budget


def test_clustering_distributes_only_when_edges_exceed_driver_budget():
    # Force the edge set above budget: 5B edges * 16B = 80GB > 28.8GB budget.
    plan = apply_distributed_routing(
        ExecutionPlan(), runtime=_runtime(48.0), cluster=_cluster(),
        n_rows_full=100_000_000, estimated_pair_count=5_000_000_000,
    )
    assert plan.clustering_strategy == "distributed_wcc"


def test_clustering_uses_driver_ram_not_cluster_total():
    # Cluster has 256GB total, but driver only 4GB: a ~11GB edge set must
    # still distribute because it can't materialize on the driver.
    small_driver = ClusterProfile(present=True, num_nodes=4, total_cpus=80,
                                  cluster_mem_gb=256.0, driver_mem_gb=4.0, source="descriptor")
    plan = apply_distributed_routing(
        ExecutionPlan(), runtime=None, cluster=small_driver,
        n_rows_full=10_000_000, estimated_pair_count=700_000_000,  # ~11.2GB
    )
    assert plan.clustering_strategy == "distributed_wcc"


def test_clustering_env_threshold_zero_is_recorded_as_override():
    plan = apply_distributed_routing(
        ExecutionPlan(), runtime=_runtime(48.0), cluster=_cluster(),
        n_rows_full=100_000_000, estimated_pair_count=110_000_000,
        env={"GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD": "0"},
    )
    clu = [d for d in plan.routing_decisions if d.stage == "clustering"][0]
    assert plan.clustering_strategy == "distributed_wcc"  # override honored
    assert clu.overridden is True
    assert "CLUSTERING_THRESHOLD=0" in clu.override_source
