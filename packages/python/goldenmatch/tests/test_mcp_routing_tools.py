from goldenmatch.mcp.routing_tools import (
    run_explain_routing,
    run_lint_routing,
    run_plan_routing,
)


def _cluster():
    return {"num_nodes": 4, "total_cpus": 80, "cluster_mem_gb": 256.0, "driver_mem_gb": 48.0}


def test_plan_routing_100m_in_memory_clustering():
    out = run_plan_routing(n_rows=100_000_000, estimated_pair_count=110_000_000,
                           cluster=_cluster())
    assert out["clustering_strategy"] == "in_memory"
    assert out["scoring_distributed"] is True


def test_explain_routing_is_human_readable():
    out = run_explain_routing(n_rows=100_000_000, estimated_pair_count=110_000_000,
                              cluster=_cluster())
    text = out["explanation"]
    assert "clustering" in text and "driver budget" in text


def test_lint_flags_threshold_zero_as_error_at_scale():
    out = run_lint_routing(
        n_rows=100_000_000, estimated_pair_count=110_000_000, cluster=_cluster(),
        env={"GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD": "0"})
    errors = [f for f in out["findings"] if f["severity"] == "ERROR"]
    assert errors and errors[0]["stage"] == "clustering"
    assert out["would_refuse"] is True


def test_lint_threshold_zero_is_advisory_below_scale():
    out = run_lint_routing(
        n_rows=1_000, estimated_pair_count=1100, cluster=_cluster(),
        env={"GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD": "0"})
    assert out["would_refuse"] is False


def test_lint_single_box_legacy_env_is_not_an_error():
    # No cluster: the legacy threshold is inert; lint must not flag/refuse it.
    out = run_lint_routing(
        n_rows=100_000_000, estimated_pair_count=110_000_000,
        env={"GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD": "0"})
    assert out["would_refuse"] is False
    assert [f for f in out["findings"] if f["severity"] == "ERROR"] == []


def test_refuse_constant_mirrors_controller():
    from goldenmatch.core.autoconfig_controller import REFUSE_AT_N
    from goldenmatch.mcp.routing_tools import _REFUSE_AT_N
    assert _REFUSE_AT_N == REFUSE_AT_N
