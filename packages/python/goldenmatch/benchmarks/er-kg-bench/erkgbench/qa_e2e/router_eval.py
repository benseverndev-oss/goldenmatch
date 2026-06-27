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
