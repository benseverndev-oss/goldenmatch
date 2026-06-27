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


from goldengraph.budget import Budget  # noqa: E402
from goldengraph.graph import GoldenGraph  # noqa: E402
from goldengraph.resolve import ResolvedEntity  # noqa: E402


class _StubLLM:
    def complete(self, prompt: str) -> str:
        # minimal valid extraction JSON so _prepare_doc parses + one llm.complete charges the budget
        return '{"entities": [], "relationships": []}'


class _RecordingStore:
    """Default ingest path calls only store.append(json) -> wheel-free build."""

    def __init__(self):
        self.appends = 0

    def append(self, _batch_json):
        self.appends += 1


def _stub_resolver(mentions):
    return [ResolvedEntity(i, m.name, m.typ, [m.name], [f"k{i}"], [i]) for i, m in enumerate(mentions)]


def test_build_composes_and_shares_one_budget():
    b = Budget(total_tokens=100_000)
    store = _RecordingStore()
    gg = GoldenGraph.build(
        ["doc one", "doc two"],
        workload=["List all entities that X works at."],
        llm=_StubLLM(), embedder=None, predicates={"works_at"},
        budget=b, resolver=_stub_resolver, store=store, corpus_records=1_000,
    )
    assert gg.budget is b
    # gate (verdict 1) is the real routing enforcer; here we only smoke the composition
    assert gg.plan.resolution_tier.value in {"fuzzy", "exact"}
    assert hasattr(gg.execution_plan, "rule_name")
    assert gg.store is store and store.appends == 2  # both docs committed
    assert b.spent_tokens > 0  # build drew from the budget (extraction llm.complete)


def test_build_unbounded_budget_default():
    gg = GoldenGraph.build(
        ["only doc"], workload=["what is X?"], llm=_StubLLM(), embedder=None,
        resolver=_stub_resolver, store=_RecordingStore(),
    )
    assert gg.budget.total_tokens is None and gg.budget.spent_tokens > 0
