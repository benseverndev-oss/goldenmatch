import pytest
from goldenmatch.core.cluster_profile import ClusterProfile
from goldenmatch.core.distributed_routing_rules import (
    SlowPathRefusedError,
    apply_distributed_routing,
    enforce_routing,
)
from goldenmatch.core.execution_plan import ExecutionPlan
from goldenmatch.core.runtime_profile import RuntimeProfile


def _plan_with_threshold_zero():
    cluster = ClusterProfile(present=True, num_nodes=4, total_cpus=80,
                             cluster_mem_gb=256.0, driver_mem_gb=48.0, source="descriptor")
    return apply_distributed_routing(
        ExecutionPlan(), runtime=RuntimeProfile(48.0, 16, 500.0), cluster=cluster,
        n_rows_full=100_000_000, estimated_pair_count=110_000_000,
        env={"GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD": "0"},
    )


def test_refuses_slow_override_at_scale():
    plan = _plan_with_threshold_zero()
    with pytest.raises(SlowPathRefusedError):
        enforce_routing(plan, n_rows=100_000_000, allow_slow_path=False)


def test_advisory_below_scale():
    plan = _plan_with_threshold_zero()
    enforce_routing(plan, n_rows=1_000, allow_slow_path=False)  # no raise


def test_allow_slow_path_acks():
    plan = _plan_with_threshold_zero()
    enforce_routing(plan, n_rows=100_000_000, allow_slow_path=True)  # no raise


def test_clean_plan_never_refuses():
    cluster = ClusterProfile(present=True, num_nodes=4, total_cpus=80,
                             cluster_mem_gb=256.0, driver_mem_gb=48.0, source="descriptor")
    plan = apply_distributed_routing(
        ExecutionPlan(), runtime=RuntimeProfile(48.0, 16, 500.0), cluster=cluster,
        n_rows_full=100_000_000, estimated_pair_count=110_000_000,
    )
    enforce_routing(plan, n_rows=100_000_000, allow_slow_path=False)  # no raise
