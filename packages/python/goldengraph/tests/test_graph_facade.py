"""Slice 4c facade + ExecutionPlan surface (needs goldenmatch for the planner / real PyStore for e2e)."""
from __future__ import annotations

from goldengraph.graph import plan_er_execution


def test_plan_er_execution_returns_a_plan():
    plan = plan_er_execution(["a doc", "another doc"], corpus_records=1_000)
    assert hasattr(plan, "rule_name") and isinstance(plan.rule_name, str)


def test_plan_scales_with_corpus_size():
    small = plan_er_execution([], corpus_records=1_000)
    huge = plan_er_execution([], corpus_records=500_000_000)
    # the ER controller's planner must react to scale -> a constant would fail this.
    # measured: small.rule_name="plan_selected_simple", huge.rule_name="plan_selected_duckdb".
    assert (small.rule_name != huge.rule_name) or (
        getattr(small, "backend", None) != getattr(huge, "backend", None)
    )
