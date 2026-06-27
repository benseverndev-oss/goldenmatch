"""Slice D: KG-vs-KG capability scorecard. Model each KG framework's documented ER strategy as
a record_key dial and run it through two ER-driven capability metrics (bridge-recall from slice A,
aggregation set-F1 from slice B1). Gates that goldengraph's fuzzy ER beats the exact-match
frameworks (LightRAG/MS-GraphRAG, which coincide on this single-entity-type corpus). Plus an
opt-in real-framework confirmation lane.

NO new dial: the scorecard maps framework labels to EXISTING dials.py keyfns. The deterministic
metrics + gate + render are wheel-free EXCEPT the per-dial graph helpers (they reach
ablation._build_store). The answer->set parser + gate shape are wheel-free.
"""
from __future__ import annotations

from dataclasses import dataclass

#: best -> worst ER (by merge-recall). Labels map to EXISTING dials.py keyfns in run_scorecard.
DIAL_TIERS = ("oracle", "goldengraph", "exact_match", "none")

#: Frozen from the measured grid (run_kg_scorecard). Placeholders -- TIGHTEN after measuring.
MOAT_MARGIN = 0.15   # goldengraph - exact_match must be >= this on EVERY capability
MONO_TOL = 1e-9      # tolerance for the oracle>=goldengraph>=exact_match>=none chain
EPS = 0.02           # exact_match <= none + EPS on bridge-recall (exact-match ~= no-merge)


def parse_entity_set(answer: str, s2c: dict) -> set:
    """Scan the framework's free-text answer for known surfaces; return the set of canonical ids.
    `s2c` is a FIRST-WINS scalar surface->canonical map (matches set_f1's scalar gold members)."""
    low = answer.lower()
    out: set = set()
    for surf, canon in s2c.items():
        if surf.lower() in low:
            out.add(canon)
    return out


@dataclass
class ScorecardResult:
    bridge_recall: dict   # dial -> mean whole-chain bridge-recall
    aggregation_f1: dict  # dial -> mean set-F1


def evaluate_assertions(res: ScorecardResult):
    """[(label, passed, is_hard), ...]. HARD gates; soft only warns.

    The exact_match column models the LightRAG/MS-GraphRAG ER strategy (exact-surface merge) as a
    record_key policy, NOT the full framework runtime; the real-framework confirmation is the
    opt-in lane. Claim: a store built under that ER strategy loses the capability."""
    metrics = {"bridge_recall": res.bridge_recall, "aggregation_f1": res.aggregation_f1}

    # 1. HEADLINE: fuzzy ER beats the exact-match tier on EVERY capability.
    worst_moat = min(m["goldengraph"] - m["exact_match"] for m in metrics.values())
    moat = worst_moat >= MOAT_MARGIN
    # 2. ER-quality monotonicity (merge-recall direction) per metric.
    mono = all(
        m["oracle"] + MONO_TOL >= m["goldengraph"]
        and m["goldengraph"] + MONO_TOL >= m["exact_match"]
        and m["exact_match"] + MONO_TOL >= m["none"]
        for m in metrics.values()
    )
    # 3. exact-match ER ~= no-merge on reachability (the slice-A name_only==none finding).
    exact_inert = res.bridge_recall["exact_match"] <= res.bridge_recall["none"] + EPS

    return [
        (f"goldengraph beats exact-match on every capability (worst moat {worst_moat:.3f} >= {MOAT_MARGIN})", moat, True),
        ("ER-quality monotonic per metric (oracle>=goldengraph>=exact_match>=none)", mono, True),
        (f"exact-match ~= no-merge on bridge-recall (exact <= none + {EPS})", exact_inert, True),
    ]


def gate_exit_code(res: ScorecardResult) -> int:
    hard_failed = any(is_hard and not ok for _l, ok, is_hard in evaluate_assertions(res))
    return 1 if hard_failed else 0


_LABEL = {
    "oracle": "oracle (perfect ER)",
    "goldengraph": "goldengraph (fuzzy)",
    "exact_match": "exact-match (LightRAG / MS-GraphRAG)",
    "none": "none (no merge)",
}


def render_scorecard_md(res: ScorecardResult) -> str:
    lines = [
        "# GoldenGraph KG-vs-KG capability scorecard (ER dial x capability, no LLM)",
        "",
        "Each KG framework's documented ER strategy as a record_key dial, run through two",
        "ER-driven capabilities. Does weak (exact-match) ER cost the frameworks vs goldengraph's",
        "fuzzy ER? The exact-match column models the LightRAG/MS-GraphRAG ER STRATEGY, not the full",
        "framework runtime (the opt-in real lane is the faithfulness check).",
        "",
        "| ER tier | bridge_recall | aggregation_setF1 |",
        "|---|---|---|",
    ]
    for d in DIAL_TIERS:
        lines.append(f"| {_LABEL[d]} | {res.bridge_recall[d]:.3f} | {res.aggregation_f1[d]:.3f} |")
    lines += ["", "## verdicts", "",
              "(assertions are measurement-frozen empirical gates, not structural guarantees)"]
    for label, passed, is_hard in evaluate_assertions(res):
        tag = "PASS" if passed else ("FAIL" if is_hard else "WARN")
        lines.append(f"- [{tag}] {label}{'' if is_hard else ' (soft)'}")
    return "\n".join(lines) + "\n"


# --- per-dial graph metrics + orchestrator (NEEDS the goldengraph_native wheel) ---


def _bridge_recall_for_dial(corpus, g, typ_of, chains, km) -> float:
    """Mean whole-chain bridge-recall over the engineered corpus under one dial's km. Mirrors the
    ablation.run_ablation per-dial loop exactly. Needs the wheel."""
    from goldengraph.answer import _retrieve_local

    from . import ablation
    from .engines.goldengraph import _NODE_BUDGET, _RETRIEVAL_HOPS
    from .scorecard import bridge_recall

    slice_graph, coverage = ablation._build_store(corpus, g, km, typ_of)
    seed_of: dict[str, int] = {}
    for nid in sorted(coverage):  # ascending id => deterministic tie-break
        for c in coverage[nid]:
            seed_of.setdefault(c, nid)
    vals: list[float] = []
    for qa in corpus.questions:
        sn = seed_of.get(qa.start_entity_id)
        if sn is None:
            vals.append(0.0)
            continue
        sub = _retrieve_local(slice_graph, [sn], max_hops=_RETRIEVAL_HOPS, node_budget=_NODE_BUDGET)
        vals.append(bridge_recall(chains[qa.id], sub, coverage)["whole_chain"])
    return (sum(vals) / len(vals)) if vals else 0.0


def _aggregation_f1_for_dial(corpus, qs, g, typ_of, km) -> float:
    """Mean set-F1 over the fan-out list-questions under one dial's km. Reuses
    aggregation.goldengraph_aggregate + set_f1. Needs the wheel."""
    from . import ablation
    from .aggregation import goldengraph_aggregate, set_f1

    slice_graph, coverage = ablation._build_store(corpus, g, km, typ_of)
    vals: list[float] = []
    for q in (q for q in qs if q.kind == "list"):
        got = goldengraph_aggregate(slice_graph, coverage, q.anchor_id, q.relation)
        vals.append(set_f1(got, set(q.gold_members))["f1"])
    return (sum(vals) / len(vals)) if vals else 0.0


def run_scorecard_deterministic(*, seed: int, n_questions: int, n_anchors: int,
                                ambiguity: float, max_hops: int = 4) -> ScorecardResult:
    """Build BOTH capability corpora, run every ER tier through both metrics. NEEDS the wheel."""
    from . import ablation, dials
    from .aggregation import agg_documents_corpus, generate_aggregation
    from .engineered import generate_engineered
    from .gold import GoldGraph, gold_chain

    keyfn = {
        "oracle": dials.oracle_keys,
        "goldengraph": dials.goldengraph_keys,
        "exact_match": dials.name_only_keys,   # LightRAG / MS-GraphRAG (coincide on single-type corpus)
        "none": dials.none_keys,
    }

    eng = generate_engineered(seed=seed, n_questions=n_questions, ambiguity=ambiguity, max_hops=max_hops)
    g_e = GoldGraph.from_corpus(eng)
    typ_e = ablation._typ_of(g_e)
    chains = {qa.id: gold_chain(g_e, qa) for qa in eng.questions}

    docs, qs = generate_aggregation(seed=seed, n_anchors=n_anchors, ambiguity=ambiguity)
    agg = agg_documents_corpus(docs)
    g_a = GoldGraph.from_corpus(agg)
    typ_a = ablation._typ_of(g_a)

    bridge: dict = {}
    aggf1: dict = {}
    for dial in DIAL_TIERS:
        bridge[dial] = _bridge_recall_for_dial(eng, g_e, typ_e, chains, keyfn[dial](eng, g_e))
        aggf1[dial] = _aggregation_f1_for_dial(agg, qs, g_a, typ_a, keyfn[dial](agg, g_a))
    return ScorecardResult(bridge_recall=bridge, aggregation_f1=aggf1)


# --- opt-in real-framework confirmation lane (ungated; real LLM + infra) ---


@dataclass
class FrameworkResult:
    set_f1: dict          # engine name -> mean set-F1 (or None if the engine failed/skipped)
    budget_exhausted: bool


def framework_set_f1(answers, golds, s2c) -> float:
    """Mean set-F1 of parsed framework answers vs gold member sets (reuses aggregation.set_f1)."""
    from .aggregation import set_f1

    if not golds:
        return 0.0
    vals = [set_f1(parse_entity_set(a, s2c), g)["f1"] for a, g in zip(answers, golds)]
    return sum(vals) / len(vals)


def framework_aggregation_f1(*, seed: int, n_anchors: int, ambiguity: float, tracker) -> FrameworkResult:
    """Drive each real engine over the aggregation list-questions; mean set-F1 per engine. Heavy /
    real-LLM / infra-dependent: a per-engine failure (missing extra, infra, 429) -> None, never
    raises. Reuses the canonical `run_qa_e2e._build_engine` constructor (engines own their LLM +
    cost seam). `tracker` is a coarse budget guard (engines self-manage cost, so enforcement is
    best-effort). NOT unit-tested (the pure scoring is framework_set_f1)."""
    from . import dials
    from .aggregation import agg_documents_corpus, generate_aggregation
    from .gold import GoldGraph
    from .run_qa_e2e import _build_engine

    docs, qs = generate_aggregation(seed=seed, n_anchors=n_anchors, ambiguity=ambiguity)
    corpus = agg_documents_corpus(docs)
    g = GoldGraph.from_corpus(corpus)
    s2c: dict = {}
    for eid, surf, _typ in dials._entity_surfaces(g):
        s2c.setdefault(surf, eid)  # first-wins scalar
    list_qs = [q for q in qs if q.kind == "list"]
    golds = [set(q.gold_members) for q in list_qs]

    out: dict = {}
    for name in ("lightrag", "ms_graphrag", "graphiti"):
        if tracker.budget_exhausted:
            out[name] = None
            continue
        try:
            engine = _build_engine(name)
            build = engine.build_kg(corpus)
            answers = [engine.answer(build.handle, q.question).text for q in list_qs]
            out[name] = framework_set_f1(answers, golds, s2c)
        except Exception:  # missing infra / 429 / version drift -> skip this engine
            out[name] = None
    return FrameworkResult(set_f1=out, budget_exhausted=tracker.budget_exhausted)


def render_framework_md(res: FrameworkResult) -> str:
    lines = [
        "# KG-vs-KG real-framework aggregation confirmation (real LLM, opt-in, UNGATED)",
        "",
        "Real LightRAG/MS-GraphRAG/Graphiti over the aggregation list-questions. Confirms the",
        "exact-match dial model (real frameworks under-aggregate) and gives Graphiti a real",
        "semantic-ER number. A n/a row = the engine failed to build (missing infra / 429).",
        "",
        f"budget_exhausted: {res.budget_exhausted}",
        "",
        "| framework | aggregation_setF1 |",
        "|---|---|",
    ]
    for name, v in res.set_f1.items():
        lines.append(f"| {name} | {'n/a' if v is None else f'{v:.3f}'} |")
    return "\n".join(lines) + "\n"
