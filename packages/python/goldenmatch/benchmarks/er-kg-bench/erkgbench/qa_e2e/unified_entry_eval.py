"""Slice 4c gate: the GoldenGraph facade end-to-end (program close).

Three HARD verdicts: (1) the facade composes profile->build->auto-answer -- an AGGREGATE query through
`gg.ask(mode="auto")` over the oracle store returns the exact gold member set, and the workload routes
to FUZZY; (2) ONE budget is shared across build-LLM (extraction) AND answer-LLM (synthesis); (3) the
surfaced ER ExecutionPlan SCALES with corpus size (a constant would fail). Needs goldenmatch + the
native wheel -> goldengraph-pipeline lane (the gate-shape test is wheel-free).
"""
from __future__ import annotations

from dataclasses import dataclass

ROUTED_SETF1_MIN = 0.99


class _StubLLM:
    """goldengraph LLMClient: extraction prompts (contain 'entities') get canned extraction; synthesis
    prompts get a canned answer. Mirrors the smoke stub."""

    _EXTRACTION = (
        '{"entities": [{"name": "Acme", "type": "org"}, {"name": "Ada", "type": "person"}], '
        '"relationships": [{"subj": 0, "predicate": "founded by", "obj": 1}]}'
    )

    def complete(self, prompt: str) -> str:
        return self._EXTRACTION if "entities" in prompt else "Ada"


class _StubEmbedder:
    def embed(self, texts):
        import numpy as np

        return np.ones((len(texts), 4), dtype="float64")


def _identity_resolver(mentions):
    from goldengraph import ResolvedEntity

    return [ResolvedEntity(i, m.name, m.typ, [m.name], [f"k{i}"], [i]) for i, m in enumerate(mentions)]


def _routing(*, seed: int, n_anchors: int):
    """Build the oracle store, wrap it in the facade, route each list-question through
    gg.ask(mode='auto'); return (capability_tier, routed_set_f1)."""
    from goldengraph.graph import GoldenGraph, plan_er_execution
    from goldengraph.unified import plan_resolver

    from . import ablation, dials
    from .aggregation import agg_documents_corpus, generate_aggregation, set_f1
    from .engineered import RELATION_SCHEMA, _load_entities
    from .engines.goldengraph import _AS_OF
    from .gold import GoldGraph

    docs, qs = generate_aggregation(seed=seed, n_anchors=n_anchors, ambiguity=0.0)
    corpus = agg_documents_corpus(docs)
    g = GoldGraph.from_corpus(corpus)
    store, _sg, _cov = ablation._build_store_obj(
        corpus, g, dials.oracle_keys(corpus, g), ablation._typ_of(g)
    )
    list_qs = [q for q in qs if q.kind == "list"]
    workload = [q.question for q in list_qs]
    plan, _r = plan_resolver(workload, predicates=set(RELATION_SCHEMA))
    gg = GoldenGraph.from_store(
        store, llm=_StubLLM(), embedder=_StubEmbedder(), plan=plan,
        execution_plan=plan_er_execution([], corpus_records=1_000),
    )
    by_id = {e.id: e for e in _load_entities()}
    vals = []
    for q in list_qs:
        ans = gg.ask(q.question, valid_t=_AS_OF, tx_t=_AS_OF, mode="auto")
        got = set() if ans == "(none found)" else set(ans.split(", "))
        gold_names = {by_id[m].canonical for m in q.gold_members}
        vals.append(set_f1(got, gold_names)["f1"])
    return plan.resolution_tier.value, (sum(vals) / len(vals) if vals else 0.0)


def _budget_shared() -> bool:
    """ONE Budget across build-LLM (extraction) AND answer-LLM (synthesis): build a tiny store via the
    facade (extraction spends), then a local ask (synthesis spends) on the SAME budget -> spent grows.

    This probe measures the SYNTHESIS path's budget plumbing, so LLM-free chain routing is disabled for
    the ask (`GOLDENGRAPH_QA_LOCAL_CHAIN=0`). Otherwise a chain-routable question like "Who founded
    Acme?" now answers deterministically with ZERO synthesis spend -- which would read False here for a
    reason orthogonal to budget sharing (the default-path chain routing, not a plumbing break)."""
    import os

    from goldengraph.budget import Budget
    from goldengraph.graph import GoldenGraph
    from goldengraph_native import _native as ggn

    from .engines.goldengraph import _AS_OF

    b = Budget(total_tokens=10_000_000)
    gg = GoldenGraph.build(
        ["Acme was founded by Ada."], workload=["what is Acme?"],
        llm=_StubLLM(), embedder=_StubEmbedder(), resolver=_identity_resolver,
        budget=b, store=ggn.PyStore(), corpus_records=1_000,
    )
    after_build = b.spent_tokens
    prev = os.environ.get("GOLDENGRAPH_QA_LOCAL_CHAIN")
    os.environ["GOLDENGRAPH_QA_LOCAL_CHAIN"] = "0"  # force synthesis so the budget probe is meaningful
    try:
        gg.ask("Who founded Acme?", valid_t=_AS_OF, tx_t=_AS_OF, mode="local")
    finally:
        if prev is None:
            os.environ.pop("GOLDENGRAPH_QA_LOCAL_CHAIN", None)
        else:
            os.environ["GOLDENGRAPH_QA_LOCAL_CHAIN"] = prev
    after_ask = b.spent_tokens
    return after_ask > after_build > 0


def _plan_scales() -> tuple[bool, str, str]:
    from goldengraph.graph import plan_er_execution

    small = plan_er_execution([], corpus_records=1_000).rule_name
    huge = plan_er_execution([], corpus_records=500_000_000).rule_name
    return small != huge, small, huge


@dataclass
class UnifiedEntryResult:
    capability_tier: str
    routed_set_f1: float
    budget_shared: bool
    plan_scales: bool
    small_rule: str
    huge_rule: str


def evaluate_assertions(res: UnifiedEntryResult):
    """Returns [(label, ok, hard)]; `hard` rows fail the gate."""
    return [
        (
            f"facade routes capability workload to FUZZY + aggregate set-F1 {res.routed_set_f1:.3f} "
            f">= {ROUTED_SETF1_MIN} (tier={res.capability_tier})",
            res.capability_tier == "fuzzy" and res.routed_set_f1 >= ROUTED_SETF1_MIN,
            True,
        ),
        (
            "ONE budget shared across build-LLM (extraction) and answer-LLM (synthesis)",
            res.budget_shared,
            True,
        ),
        (
            f"surfaced ER ExecutionPlan scales with corpus size ({res.small_rule} -> {res.huge_rule})",
            res.plan_scales,
            True,
        ),
    ]


def gate_exit_code(res: UnifiedEntryResult) -> int:
    return 1 if any(hard and not ok for _l, ok, hard in evaluate_assertions(res)) else 0


def run_unified_entry_deterministic(*, seed: int, n_anchors: int) -> UnifiedEntryResult:
    tier, f1 = _routing(seed=seed, n_anchors=n_anchors)
    scales, small, huge = _plan_scales()
    return UnifiedEntryResult(
        capability_tier=tier, routed_set_f1=f1, budget_shared=_budget_shared(),
        plan_scales=scales, small_rule=small, huge_rule=huge,
    )


def render_unified_entry_md(res: UnifiedEntryResult) -> str:
    lines = [
        "# GoldenGraph unified entry-point gate (slice 4c, no LLM, program close)",
        "",
        "The GoldenGraph facade run end-to-end: profile a workload -> tier-resolver build (4a/4b) ->",
        "auto-routed answers (slices 1/2/3), with the ER scale plan surfaced and ONE budget across",
        "build + answer LLM work. This closes the KG/RAG query-routing controller program.",
        "",
        f"- capability workload routes to: {res.capability_tier}",
        f"- routed aggregate set-F1 (gg.ask mode=auto): {res.routed_set_f1:.3f}",
        f"- one budget shared across build + answer: {res.budget_shared}",
        f"- ER ExecutionPlan scales with corpus: {res.small_rule} (1k) -> {res.huge_rule} (500M)",
        "",
        "## verdicts",
        "",
    ]
    for label, ok, _h in evaluate_assertions(res):
        lines.append(f"- [{'PASS' if ok else 'FAIL'}] {label}")
    return "\n".join(lines) + "\n"
