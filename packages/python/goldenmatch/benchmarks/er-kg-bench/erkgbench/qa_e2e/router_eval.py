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
    # temporal (slice 2); defaults keep slice-1 constructions valid (1.0 == not-failing)
    temporal_recall: float = 1.0
    temporal_slot_acc: float = 1.0
    temporal_past_acc: float = 1.0
    temporal_current_acc: float = 1.0
    # NL paraphrase / LLM-tier (slice 3); defaults keep slice-1/2 constructions valid
    heuristic_paraphrase_acc: float = 0.0
    stub_escalation_acc: float = 1.0


# frozen from the first measured run (verify-then-freeze)
AGG_RECALL_MIN = 0.99
SLOT_ACC_MIN = 0.99
ROUTED_SETF1_MIN = 0.99
TEMPORAL_RECALL_MIN = 0.99
TEMPORAL_SLOT_MIN = 0.99
TEMPORAL_ACC_MIN = 0.99
HEURISTIC_PARAPHRASE_CEIL = 0.2   # heuristic must route <= this fraction of paraphrases correctly
STUB_ESCALATION_MIN = 0.99        # an oracle tier-2 must recover all paraphrases


def evaluate_assertions(res: RouterResult):
    return [
        (f"classifier routes list-questions to AGGREGATE (recall {res.aggregate_recall:.3f} >= {AGG_RECALL_MIN})",
         res.aggregate_recall >= AGG_RECALL_MIN, True),
        (f"anchor/relation slots correct (acc {res.slot_accuracy:.3f} >= {SLOT_ACC_MIN})",
         res.slot_accuracy >= SLOT_ACC_MIN, True),
        (f"routed aggregate set-F1 at ambiguity=0.0 (got {res.routed_setf1:.3f} >= {ROUTED_SETF1_MIN})",
         res.routed_setf1 >= ROUTED_SETF1_MIN, True),
        (f"classifier routes temporal questions to TEMPORAL_ASOF (recall {res.temporal_recall:.3f} >= {TEMPORAL_RECALL_MIN})",
         res.temporal_recall >= TEMPORAL_RECALL_MIN, True),
        (f"temporal slots (anchor/relation/date) correct (acc {res.temporal_slot_acc:.3f} >= {TEMPORAL_SLOT_MIN})",
         res.temporal_slot_acc >= TEMPORAL_SLOT_MIN, True),
        (f"routed as-of-accuracy past={res.temporal_past_acc:.3f} current={res.temporal_current_acc:.3f} (both >= {TEMPORAL_ACC_MIN})",
         res.temporal_past_acc >= TEMPORAL_ACC_MIN and res.temporal_current_acc >= TEMPORAL_ACC_MIN, True),
        (f"heuristic MISSES paraphrases (acc {res.heuristic_paraphrase_acc:.3f} <= {HEURISTIC_PARAPHRASE_CEIL})",
         res.heuristic_paraphrase_acc <= HEURISTIC_PARAPHRASE_CEIL, True),
        (f"stub tier-2 RECOVERS paraphrases (acc {res.stub_escalation_acc:.3f} >= {STUB_ESCALATION_MIN})",
         res.stub_escalation_acc >= STUB_ESCALATION_MIN, True),
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


# --- temporal as-of routing (slice 2) ---


def temporal_classifier_accuracy(*, seed: int, n_facts: int, ambiguity: float) -> dict:
    from goldengraph.route import QueryIntent

    from .temporal import generate_temporal

    _docs, _facts, qs = generate_temporal(seed=seed, n_facts=n_facts, ambiguity=ambiguity)
    by_id = {e.id: e for e in _load_entities()}
    preds = set(RELATION_SCHEMA)
    rec = slot = 0
    for q in qs:
        p = classify_query(q.question, predicates=preds)
        if p.intent is QueryIntent.TEMPORAL_ASOF:
            rec += 1
        ok_date = p.as_of is not None and p.as_of.isdigit() and int(p.as_of) == q.D
        if p.anchor_surface == by_id[q.anchor_id].canonical and p.relation == q.relation and ok_date:
            slot += 1
    n = len(qs) or 1
    return {"temporal_recall": rec / n, "temporal_slot_acc": slot / n}


def _build_concept_named_temporal_store(facts, by_id):
    """Mirror temporal.build_temporal_store but name nodes by CONCEPT SURFACE (so question-text
    seeds_by_name resolves) while keeping oracle merge on the QID record_keys. Needs the wheel."""
    import json

    from goldengraph_native import _native as ggn

    from .temporal import T1

    def ent(local, _id):
        return {"local_id": local, "canonical_name": by_id[_id].canonical, "typ": "concept",
                "surface_names": [by_id[_id].canonical], "record_keys": [_id]}

    store = ggn.PyStore()
    for f in facts:
        batch = {
            "entities": [ent(0, f.anchor_id), ent(1, f.a_id), ent(2, f.b_id)],
            "edges": [
                {"subj_local": 0, "predicate": f.relation, "obj_local": 1,
                 "valid_from": T1, "valid_to": f.tc, "source_refs": []},
                {"subj_local": 0, "predicate": f.relation, "obj_local": 2,
                 "valid_from": f.tc, "valid_to": None, "source_refs": []},
            ],
            "ingested_at": 1,
        }
        store.append(json.dumps(batch))
    return store


def run_temporal_routed_accuracy(*, seed: int, n_facts: int, ambiguity: float) -> dict:
    """Route each B2 question through classify_query -> store.as_of(D) -> asof_object; as-of-accuracy
    by regime vs name-projected gold. Needs the wheel."""
    from goldengraph.answer import asof_object

    from .temporal import _BIG_TX, as_of_accuracy, generate_temporal

    _docs, facts, qs = generate_temporal(seed=seed, n_facts=n_facts, ambiguity=ambiguity)
    by_id = {e.id: e for e in _load_entities()}
    store = _build_concept_named_temporal_store(facts, by_id)
    preds = set(RELATION_SCHEMA)
    acc: dict = {"past": [], "current": []}
    for q in qs:
        p = classify_query(q.question, predicates=preds)
        got = None
        if p.anchor_surface and p.relation and p.as_of and p.as_of.isdigit():
            got = asof_object(store.as_of(int(p.as_of), _BIG_TX), p.anchor_surface, p.relation)
        gold_name = by_id[q.gold_obj].canonical
        acc[q.regime].append(as_of_accuracy(got, gold_name))
    return {r: (sum(v) / len(v) if v else 0.0) for r, v in acc.items()}


def first_known_name(text: str, universe: set) -> str | None:
    """First universe name appearing in `text` (the single-object analog of parse_entity_set)."""
    low = text.lower()
    hits = [(low.index(n.lower()), n) for n in universe if n.lower() in low]
    return min(hits)[1] if hits else None


# --- NL paraphrase / LLM-tier escalation (slice 3) ---


class StubClassifier:
    """Deterministic tier-2 ORACLE: paraphrase question -> a high-confidence QueryProfile from its
    gold slots. confidence=1.0 is REQUIRED so resolve_profile/plan_query accept it."""

    def __init__(self, paraphrases):
        from goldengraph.route import QueryProfile

        self._m = {
            pp.question: QueryProfile(intent=pp.intent, anchor_surface=pp.anchor_surface,
                                      relation=pp.relation, as_of=pp.as_of, confidence=1.0)
            for pp in paraphrases
        }

    def classify(self, query, *, predicates=None):
        from goldengraph.route import QueryIntent, QueryProfile

        return self._m.get(query, QueryProfile(QueryIntent.MULTI_HOP, confidence=0.0))


def _profile_matches(p, pp) -> bool:
    return (p.intent is pp.intent and p.anchor_surface == pp.anchor_surface
            and p.relation == pp.relation and (p.as_of or None) == (pp.as_of or None))


def heuristic_paraphrase_accuracy() -> float:
    from goldengraph.route import classify_query

    from .router_paraphrases import PARAPHRASES

    preds = set(RELATION_SCHEMA)
    hits = sum(_profile_matches(classify_query(pp.question, predicates=preds), pp) for pp in PARAPHRASES)
    return hits / (len(PARAPHRASES) or 1)


def stub_escalation_accuracy() -> float:
    from goldengraph.route import plan_query, resolve_profile

    from .router_paraphrases import PARAPHRASES

    preds = set(RELATION_SCHEMA)
    stub = StubClassifier(PARAPHRASES)
    hits = 0
    for pp in PARAPHRASES:
        p = resolve_profile(pp.question, predicates=preds, llm_classifier=stub)
        want = "aggregate" if pp.intent.name == "AGGREGATE" else "as_of"
        if _profile_matches(p, pp) and plan_query(p).mode == want:
            hits += 1
    return hits / (len(PARAPHRASES) or 1)


def llm_classifier_accuracy(paraphrases, llm) -> dict:
    """Run the REAL LLMQueryClassifier over the paraphrases; intent-accuracy + slot-accuracy vs gold.
    Opt-in (real LLM). The classifier itself is fail-open (abstain on any failure)."""
    from goldengraph.route import LLMQueryClassifier

    c = LLMQueryClassifier(llm, max_calls=len(paraphrases) + 1)
    preds = set(RELATION_SCHEMA)
    intent_hits = slot_hits = 0
    for pp in paraphrases:
        p = c.classify(pp.question, predicates=preds)
        if p.intent is pp.intent:
            intent_hits += 1
        if _profile_matches(p, pp):
            slot_hits += 1
    n = len(paraphrases) or 1
    return {"intent_acc": intent_hits / n, "slot_acc": slot_hits / n}


def run_router_deterministic(*, seed: int, n_anchors: int) -> RouterResult:
    acc = classifier_accuracy(seed=seed, n_anchors=n_anchors, ambiguity=0.0)
    routed = run_routed_correctness(seed=seed, n_anchors=n_anchors)
    # Temporal: B2 store is oracle-keyed so ER is perfect regardless of ambiguity (it only affects
    # rendered doc surfaces, which the gate does not read). NOTE: at n_facts==_N_ANCHORS the
    # generator uses a single RELATION_SCHEMA[0] relation -- lower relation diversity, exact by
    # construction.
    tacc = temporal_classifier_accuracy(seed=seed, n_facts=n_anchors, ambiguity=0.6)
    tr = run_temporal_routed_accuracy(seed=seed, n_facts=n_anchors, ambiguity=0.6)
    return RouterResult(
        aggregate_recall=acc["aggregate_recall"],
        slot_accuracy=acc["slot_accuracy"],
        routed_setf1=routed,
        temporal_recall=tacc["temporal_recall"],
        temporal_slot_acc=tacc["temporal_slot_acc"],
        temporal_past_acc=tr.get("past", 0.0),
        temporal_current_acc=tr.get("current", 0.0),
        heuristic_paraphrase_acc=heuristic_paraphrase_accuracy(),
        stub_escalation_acc=stub_escalation_accuracy(),
    )


def render_router_md(res: RouterResult) -> str:
    lines = [
        "# GoldenGraph query-router gate (slices 1-2, no LLM)",
        "",
        "Heuristic classify_query routes B1 list-questions to the aggregate lever and B2 temporal",
        "questions to the as-of lever; the engine-native traversals return the exact answer.",
        "",
        f"- aggregate_recall:     {res.aggregate_recall:.3f}",
        f"- slot_accuracy:        {res.slot_accuracy:.3f}",
        f"- routed_setF1:         {res.routed_setf1:.3f}",
        f"- temporal_recall:      {res.temporal_recall:.3f}",
        f"- temporal_slot_acc:    {res.temporal_slot_acc:.3f}",
        f"- temporal_past_acc:    {res.temporal_past_acc:.3f}",
        f"- temporal_current_acc: {res.temporal_current_acc:.3f}",
        f"- heuristic_paraphrase_acc: {res.heuristic_paraphrase_acc:.3f} (heuristic on NL paraphrases -- expected LOW)",
        f"- stub_escalation_acc:      {res.stub_escalation_acc:.3f} (ORACLE tier-2 recovery -- proves the MECHANISM, not real-LLM accuracy)",
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
    temporal_auto_acc: float | None = None
    temporal_local_acc: float | None = None
    paraphrase_intent_acc: float | None = None
    paraphrase_slot_acc: float | None = None


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
        t_auto, t_local = _temporal_auto_vs_local(seed, n_anchors, engine, tracker, _BIG)
        p_intent = p_slot = None
        try:
            from .router_paraphrases import PARAPHRASES
            pa = llm_classifier_accuracy(PARAPHRASES, engine._llm)
            p_intent, p_slot = pa["intent_acc"], pa["slot_acc"]
        except Exception:
            pass
        return RouterLLMResult(
            auto_setf1=(sum(auto_vals) / len(auto_vals)) if auto_vals else None,
            local_setf1=(sum(local_vals) / len(local_vals)) if local_vals else None,
            budget_exhausted=tracker.budget_exhausted,
            temporal_auto_acc=t_auto,
            temporal_local_acc=t_local,
            paraphrase_intent_acc=p_intent,
            paraphrase_slot_acc=p_slot,
        )
    except Exception:
        return RouterLLMResult(auto_setf1=None, local_setf1=None, budget_exhausted=tracker.budget_exhausted)


def _temporal_auto_vs_local(seed, n_anchors, engine, tracker, big):
    """Past-regime temporal auto-vs-local over the concept-named WINDOWED store (NOT engine.build_kg:
    real ingest doesn't parse the engineered date phrasing into valid_to windows). Returns
    (auto_acc, local_acc) or (None, None) on failure. Uses the engine's real llm/embedder."""
    try:
        from goldengraph.answer import ask

        from .temporal import as_of_accuracy, generate_temporal

        _docs, facts, qs = generate_temporal(seed=seed, n_facts=n_anchors, ambiguity=0.6)
        by_id = {e.id: e for e in _load_entities()}
        universe = {e.canonical for e in _load_entities()}
        store = _build_concept_named_temporal_store(facts, by_id)
        a_vals, l_vals = [], []
        for q in (q for q in qs if q.regime == "past"):
            if tracker.budget_exhausted:
                break
            gold = by_id[q.gold_obj].canonical
            a = ask(q.question, store, llm=engine._llm, embedder=engine._embedder,
                    valid_t=big, tx_t=big, mode="auto")
            ln = ask(q.question, store, llm=engine._llm, embedder=engine._embedder,
                     valid_t=big, tx_t=big, mode="local")
            a_vals.append(as_of_accuracy(first_known_name(a, universe), gold))
            l_vals.append(as_of_accuracy(first_known_name(ln, universe), gold))
        return (
            (sum(a_vals) / len(a_vals)) if a_vals else None,
            (sum(l_vals) / len(l_vals)) if l_vals else None,
        )
    except Exception:
        return None, None


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
        f"| local (general) | {_f(res.local_setf1)} |\n\n"
        "## temporal past-regime (as-of-accuracy on a corrected-away value)\n\n"
        "| mode | as_of_accuracy |\n|---|---|\n"
        f"| auto (routed->as_of, slices at D) | {_f(res.temporal_auto_acc)} |\n"
        f"| local (general, no temporal slice) | {_f(res.temporal_local_acc)} |\n\n"
        "## LLM classifier on NL paraphrases (the heuristic misses these)\n\n"
        "| metric | accuracy |\n|---|---|\n"
        f"| intent_acc | {_f(res.paraphrase_intent_acc)} |\n"
        f"| slot_acc | {_f(res.paraphrase_slot_acc)} |\n"
    )
