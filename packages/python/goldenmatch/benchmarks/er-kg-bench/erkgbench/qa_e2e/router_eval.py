"""Slice-1 router gate over the B1 aggregation corpus. classifier_accuracy is wheel-free;
run_routed_correctness needs the goldengraph_native wheel (builds the oracle store at
ambiguity=0.0 and calls the engine aggregate_members). Compares in NAME space vs name-projected
gold (see the design's 'Why ambiguity=0.0').
"""
from __future__ import annotations

from dataclasses import dataclass

from goldengraph.route import QueryIntent, classify_query

from .aggregation import generate_aggregation
from .engineered import RELATION_SCHEMA, _load_entities


def classifier_accuracy(*, seed: int, n_anchors: int, ambiguity: float) -> dict:
    _docs, qs = generate_aggregation(seed=seed, n_anchors=n_anchors, ambiguity=ambiguity)
    by_id = {e.id: e for e in _load_entities()}
    preds = set(RELATION_SCHEMA)
    list_qs = [q for q in qs if q.kind == "list"]
    agg_hits = slot_hits = 0
    for q in list_qs:
        p = classify_query(q.question, predicates=preds)
        if p.intent is QueryIntent.AGGREGATE:
            agg_hits += 1
        if p.anchor_surface == by_id[q.anchor_id].canonical and p.relation == q.relation:
            slot_hits += 1
    n = len(list_qs) or 1
    return {"aggregate_recall": agg_hits / n, "slot_accuracy": slot_hits / n}


@dataclass
class RouterResult:
    aggregate_recall: float
    slot_accuracy: float
    routed_setf1: float


# frozen from the first measured run (verify-then-freeze)
AGG_RECALL_MIN = 0.99
SLOT_ACC_MIN = 0.99
ROUTED_SETF1_MIN = 0.99


def evaluate_assertions(res: RouterResult):
    return [
        (f"classifier routes list-questions to AGGREGATE (recall {res.aggregate_recall:.3f} >= {AGG_RECALL_MIN})",
         res.aggregate_recall >= AGG_RECALL_MIN, True),
        (f"anchor/relation slots correct (acc {res.slot_accuracy:.3f} >= {SLOT_ACC_MIN})",
         res.slot_accuracy >= SLOT_ACC_MIN, True),
        (f"routed aggregate set-F1 at ambiguity=0.0 (got {res.routed_setf1:.3f} >= {ROUTED_SETF1_MIN})",
         res.routed_setf1 >= ROUTED_SETF1_MIN, True),
    ]


def gate_exit_code(res: RouterResult) -> int:
    return 1 if any(h and not ok for _l, ok, h in evaluate_assertions(res)) else 0


def run_routed_correctness(*, seed: int, n_anchors: int) -> float:
    """Build the B1 oracle store at ambiguity=0.0, route each list-question through
    classify_query -> aggregate_members, score set-F1 vs NAME-PROJECTED gold. Needs the wheel."""
    from goldengraph.answer import aggregate_members

    from . import ablation, dials
    from .aggregation import agg_documents_corpus, set_f1
    from .gold import GoldGraph

    docs, qs = generate_aggregation(seed=seed, n_anchors=n_anchors, ambiguity=0.0)
    corpus = agg_documents_corpus(docs)
    g = GoldGraph.from_corpus(corpus)
    slice_graph, _cov = ablation._build_store(
        corpus, g, dials.oracle_keys(corpus, g), ablation._typ_of(g)
    )
    by_id = {e.id: e for e in _load_entities()}
    preds = set(RELATION_SCHEMA)
    vals = []
    for q in (q for q in qs if q.kind == "list"):
        p = classify_query(q.question, predicates=preds)
        got = (
            aggregate_members(slice_graph, p.anchor_surface, p.relation)
            if (p.anchor_surface and p.relation)
            else set()
        )
        gold_names = {by_id[m].canonical for m in q.gold_members}
        vals.append(set_f1(got, gold_names)["f1"])
    return (sum(vals) / len(vals)) if vals else 0.0


def run_router_deterministic(*, seed: int, n_anchors: int) -> RouterResult:
    acc = classifier_accuracy(seed=seed, n_anchors=n_anchors, ambiguity=0.0)
    routed = run_routed_correctness(seed=seed, n_anchors=n_anchors)
    return RouterResult(
        aggregate_recall=acc["aggregate_recall"],
        slot_accuracy=acc["slot_accuracy"],
        routed_setf1=routed,
    )


def render_router_md(res: RouterResult) -> str:
    lines = [
        "# GoldenGraph query-router gate (slice 1, no LLM)",
        "",
        "Heuristic classify_query routes B1 list-questions to the aggregate lever; the engine-native",
        "aggregate_members traversal returns the exact member set (name space, ambiguity=0.0).",
        "",
        f"- aggregate_recall: {res.aggregate_recall:.3f}",
        f"- slot_accuracy:    {res.slot_accuracy:.3f}",
        f"- routed_setF1:     {res.routed_setf1:.3f}",
        "",
        "## verdicts",
        "",
    ]
    for label, ok, _h in evaluate_assertions(res):
        lines.append(f"- [{'PASS' if ok else 'FAIL'}] {label}")
    return "\n".join(lines) + "\n"


# --- opt-in real-LLM auto-vs-local row (ungated; real LLM + infra) ---


@dataclass
class RouterLLMResult:
    auto_setf1: float | None
    local_setf1: float | None
    budget_exhausted: bool


def answer_setf1(answer_text: str, gold_names: set, universe: set) -> float:
    """Parse a free-text answer into the set of universe names it mentions, set-F1 vs gold."""
    from .aggregation import set_f1

    if not gold_names:
        return 0.0
    low = answer_text.lower()
    pred = {n for n in universe if n.lower() in low}
    return set_f1(pred, gold_names)["f1"]


def run_router_llm(*, seed: int, n_anchors: int, tracker) -> RouterLLMResult:
    """Real-LLM auto-vs-local: build a store over the B1 docs, and for each list-question compare
    ask(mode='auto') (routes to aggregate) vs ask(mode='local'). Heavy / real-LLM / infra; any
    failure (missing key, 429, build error) -> None rows, never raises. NOT unit-tested beyond
    answer_setf1."""
    _BIG = 10**9
    try:
        from goldengraph.answer import ask

        from .aggregation import agg_documents_corpus, generate_aggregation
        from .run_qa_e2e import _build_engine

        docs, qs = generate_aggregation(seed=seed, n_anchors=n_anchors, ambiguity=0.6)
        corpus = agg_documents_corpus(docs)
        engine = _build_engine("goldengraph")
        build = engine.build_kg(corpus)
        store = build.handle
        by_id = {e.id: e for e in _load_entities()}
        universe = {e.canonical for e in _load_entities()}
        list_qs = [q for q in qs if q.kind == "list"]

        auto_vals, local_vals = [], []
        for q in list_qs:
            if tracker.budget_exhausted:
                break
            gold = {by_id[m].canonical for m in q.gold_members}
            a = ask(q.question, store, llm=engine._llm, embedder=engine._embedder,
                    valid_t=_BIG, tx_t=_BIG, mode="auto")
            ln = ask(q.question, store, llm=engine._llm, embedder=engine._embedder,
                     valid_t=_BIG, tx_t=_BIG, mode="local")
            auto_vals.append(answer_setf1(a, gold, universe))
            local_vals.append(answer_setf1(ln, gold, universe))
        return RouterLLMResult(
            auto_setf1=(sum(auto_vals) / len(auto_vals)) if auto_vals else None,
            local_setf1=(sum(local_vals) / len(local_vals)) if local_vals else None,
            budget_exhausted=tracker.budget_exhausted,
        )
    except Exception:
        return RouterLLMResult(auto_setf1=None, local_setf1=None, budget_exhausted=tracker.budget_exhausted)


def render_router_llm_md(res: RouterLLMResult) -> str:
    def _f(v):
        return "n/a" if v is None else f"{v:.3f}"

    return (
        "# Query-router auto-vs-local (real LLM, opt-in, UNGATED)\n\n"
        "Does routing an aggregation query to the aggregate lever (auto) beat the general local\n"
        "mode on answer-set-F1? n/a = the run failed to build (missing key / 429 / infra).\n\n"
        f"budget_exhausted: {res.budget_exhausted}\n\n"
        "| mode | answer_setF1 |\n|---|---|\n"
        f"| auto (routed->aggregate) | {_f(res.auto_setf1)} |\n"
        f"| local (general) | {_f(res.local_setf1)} |\n"
    )
