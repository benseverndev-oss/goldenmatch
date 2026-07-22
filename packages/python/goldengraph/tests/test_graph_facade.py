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


def test_from_store_wraps_existing_store_and_budget():
    store = _RecordingStore()
    b = Budget(total_tokens=500)
    gg = GoldenGraph.from_store(store, llm=_StubLLM(), embedder=None, budget=b)
    assert gg.store is store and gg.budget is b
    assert gg.plan is None and gg.execution_plan is None  # not built, just wrapped


# --- zero-touch hybrid: build indexes passages, ask threads them ------------------------------------


class _AxisEmbedder:
    def embed(self, texts):
        import numpy as np

        vocab = {"apple": [1.0, 0.0], "banana": [0.0, 1.0]}
        return np.array(
            [next((vocab[w] for w in str(t).lower().split() if w in vocab), [0.0, 0.0]) for t in texts],
            dtype=float,
        )


def test_build_indexes_passages_when_embedder_present():
    from goldengraph.passage_index import PassageIndex

    gg = GoldenGraph.build(
        ["apple pie", "banana bread"], workload=["what is X?"], llm=_StubLLM(),
        embedder=_AxisEmbedder(), resolver=_stub_resolver, store=_RecordingStore(),
    )
    assert isinstance(gg._passages, PassageIndex) and len(gg._passages) == 2


def test_build_skips_passage_index_without_embedder():
    # No embedder -> nothing to embed passages with -> no index (ask falls back to local), no raise.
    gg = GoldenGraph.build(
        ["only doc"], workload=["what is X?"], llm=_StubLLM(), embedder=None,
        resolver=_stub_resolver, store=_RecordingStore(),
    )
    assert gg._passages is None


def test_build_index_passages_opt_out():
    gg = GoldenGraph.build(
        ["apple pie"], workload=["what is X?"], llm=_StubLLM(), embedder=_AxisEmbedder(),
        resolver=_stub_resolver, store=_RecordingStore(), index_passages=False,
    )
    assert gg._passages is None


def test_ask_threads_build_passages_and_schema(monkeypatch):
    gg = GoldenGraph.build(
        ["apple pie", "banana bread"], workload=["what is X?"], llm=_StubLLM(),
        embedder=_AxisEmbedder(), resolver=_stub_resolver, store=_RecordingStore(),
    )
    seen = {}

    def _fake_ask(query, store, **kw):
        seen.update(kw)
        return "ok"

    monkeypatch.setattr("goldengraph.answer.ask", _fake_ask)
    gg.ask("q?", valid_t=1, tx_t=1)
    assert seen["passages"] is gg._passages  # the built index is threaded by default
    assert "query_schema" in seen


def test_ask_explicit_passages_override_wins(monkeypatch):
    gg = GoldenGraph.build(
        ["apple pie"], workload=["what is X?"], llm=_StubLLM(), embedder=_AxisEmbedder(),
        resolver=_stub_resolver, store=_RecordingStore(),
    )
    seen = {}
    monkeypatch.setattr("goldengraph.answer.ask", lambda query, store, **kw: seen.update(kw) or "ok")
    gg.ask("q?", valid_t=1, tx_t=1, passages=None)  # caller override
    assert seen["passages"] is None  # explicit kwarg beats the stored index
