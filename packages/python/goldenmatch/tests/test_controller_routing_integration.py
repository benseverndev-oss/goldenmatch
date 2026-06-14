"""On a small single-box input the controller's ExecutionPlan must carry a
routing trace with all stages in-memory (no behavior change). Mirrors the
fixture + read pattern in test_planner_integration.py."""
import goldenmatch as gm
import polars as pl
import pytest


@pytest.fixture(autouse=True)
def _disable_autoconfig_memory(monkeypatch):
    """Stop a cached config from short-circuiting _initial_config."""
    monkeypatch.setenv("GOLDENMATCH_AUTOCONFIG_MEMORY", "0")


def _small_df(n: int = 80) -> pl.DataFrame:
    return pl.DataFrame({
        "name": ["alice", "alyce", "bob", "robert"] * (n // 4),
        "email": [f"u{i}@x.com" for i in range(n)],
    })


def test_controller_populates_routing_trace():
    result = gm.dedupe_df(_small_df())
    plan = result.postflight_report.controller_history.execution_plan
    assert plan is not None
    assert plan.routing_decisions != ()
    assert plan.clustering_strategy == "in_memory"
    assert {d.stage for d in plan.routing_decisions} == {"scoring", "clustering", "golden"}
    assert all(d.rule_name == "single_box" for d in plan.routing_decisions)
