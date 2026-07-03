"""Slice C: ambiguity x passage_k crossover. A free, deterministic recall-crossover
gate (graph reachability flat in passage_k vs lexical passage-recall decay) plus an
opt-in real-LLM answer-match crossover headline. Self-contained -- no #1270 hybrid dep.

The deterministic recall surfaces + gate + render are wheel-free; only graph_recall_at
(reused slice-A store build) needs the goldengraph_native wheel.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: 5 x 4 sweep grid (spec).
AMBIGUITY_GRID = (0.0, 0.25, 0.5, 0.75, 1.0)
PASSAGE_K_GRID = (10, 5, 3, 1)

_TOKEN = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def lexical_retrieve(docs, query_terms, passage_k: int) -> list[str]:
    """Deterministic term-overlap retriever. Rank docs by count of query-term tokens
    present in doc.text; ties broken by doc.id ascending. Returns the top-passage_k ids.
    The rank is a single fixed total order independent of k, so top-k is a nested prefix
    of top-(k+1) -- this is what makes passage-recall monotone in k (gate assertion 2)."""
    qt = set(query_terms)
    scored = []
    for d in docs:
        toks = set(_tokens(d.text))
        overlap = len(qt & toks)
        scored.append((-overlap, d.id))
    scored.sort()
    return [doc_id for _neg, doc_id in scored[:passage_k]]


def query_terms_for(qa, g) -> list[str]:
    """Tokens a naive retriever would key on: the start-entity surface + the relation
    chain. Intermediate-hop entity surfaces are intentionally absent (the multi-hop RAG
    problem -- later edges retrieved on relation overlap alone)."""
    terms = list(_tokens(g.canonical_name(qa.start_entity_id)))
    for rel in qa.relation_chain:
        terms.extend(rel.lower().split("_"))
    return terms


def passage_recall(qa, topk_ids) -> float:
    gold = set(qa.gold_supporting_fact_ids)
    if not gold:
        return 0.0
    return len(set(topk_ids) & gold) / len(gold)


def graph_recall_at(corpus, g, *, max_hops: int) -> float:
    """Whole-chain bridge-recall under the goldengraph resolution dial -- the slice-A
    number, used here as the passage_k-INVARIANT graph surface. Needs the wheel."""
    from goldengraph.answer import _retrieve_local

    from .ablation import _KEYFN, _build_store, _typ_of
    from .engines.goldengraph import _NODE_BUDGET, _RETRIEVAL_HOPS
    from .gold import gold_chain
    from .scorecard import bridge_recall

    typ_of = _typ_of(g)
    km = _KEYFN["goldengraph"](corpus, g)
    slice_graph, coverage = _build_store(corpus, g, km, typ_of)

    seed_of: dict[str, int] = {}
    for nid in sorted(coverage):  # ascending id => deterministic tie-break (matches ablation)
        for c in coverage[nid]:
            seed_of.setdefault(c, nid)

    chains = {qa.id: gold_chain(g, qa) for qa in corpus.questions}
    vals: list[float] = []
    for qa in corpus.questions:
        seed_node = seed_of.get(qa.start_entity_id)
        if seed_node is None:
            vals.append(0.0)
            continue
        subgraph = _retrieve_local(
            slice_graph, [seed_node], max_hops=_RETRIEVAL_HOPS, node_budget=_NODE_BUDGET
        )
        vals.append(bridge_recall(chains[qa.id], subgraph, coverage)["whole_chain"])
    return (sum(vals) / len(vals)) if vals else 0.0


#: Frozen from the measured grid (goldengraph-pipeline run 28294346180, seed 7, n=80).
#: The lexical multi-hop floor tops out at ~0.5 recall even at passage_k=10 (later-hop
#: chain docs don't mention the start entity; only ~5 relation types) -- so there is no
#: "RAG starts high then starves below graph" crossover. The honest finding is STRONGER:
#: graph reachability DOMINATES the floor at every cell. Margins set below the measured
#: tightest values with headroom (min domination 0.121 @ amb0.5/k10; min starvation drop
#: 0.169 @ amb1.0).
DOMINATION_MARGIN = 0.08  # graph - rag must be at least this at EVERY (ambiguity, passage_k)
STARVATION_DROP = 0.10    # rag@kmax - rag@kmin must be at least this per ambiguity row


@dataclass
class CrossoverResult:
    graph: dict  # ambiguity -> recall (passage_k-invariant)
    rag: dict    # ambiguity -> {passage_k -> recall}


def recall_crossover_grid(*, seed: int, n_questions: int, max_hops: int = 4) -> CrossoverResult:
    """The 5x4 deterministic surfaces. NEEDS the wheel (graph_recall_at)."""
    from .engineered import generate_engineered
    from .gold import GoldGraph

    graph: dict = {}
    rag: dict = {}
    for a in AMBIGUITY_GRID:
        corpus = generate_engineered(seed=seed, n_questions=n_questions, ambiguity=a, max_hops=max_hops)
        g = GoldGraph.from_corpus(corpus)
        graph[a] = graph_recall_at(corpus, g, max_hops=max_hops)
        rag[a] = {}
        for k in PASSAGE_K_GRID:
            vals = []
            for qa in corpus.questions:
                if not qa.gold_supporting_fact_ids:
                    continue
                topk = lexical_retrieve(corpus.documents, query_terms_for(qa, g), k)
                vals.append(passage_recall(qa, topk))
            rag[a][k] = (sum(vals) / len(vals)) if vals else 0.0
    return CrossoverResult(graph=graph, rag=rag)


def evaluate_assertions(res: CrossoverResult):
    """[(label, passed, is_hard), ...]. HARD gates; soft only warns.

    The measured lexical floor never leads graph (it tops out ~0.5 even at passage_k=10),
    so this is NOT a starvation crossover -- it is graph reachability DOMINATING the floor
    everywhere. The gate asserts that domination + that passage_k starvation actually bites
    the floor. Whether the reachability advantage converts to ANSWERS is the opt-in
    answer-match arm's question (and the prior head-to-head says it does not -- reachability
    != answerability)."""
    ks_desc = sorted(PASSAGE_K_GRID, reverse=True)  # 10,5,3,1
    kmax, kmin = max(PASSAGE_K_GRID), min(PASSAGE_K_GRID)

    # 1. by-construction: graph is stored per-ambiguity scalar => flat across passage_k.
    graph_flat = all(isinstance(res.graph[a], (int, float)) for a in res.graph)
    # 2. by-construction: RAG monotone non-increasing as passage_k shrinks.
    rag_monotone = all(
        res.rag[a][ks_desc[i]] + 1e-12 >= res.rag[a][ks_desc[i + 1]]
        for a in res.rag
        for i in range(len(ks_desc) - 1)
    )
    # 3. measurement-frozen HEADLINE: graph dominates the floor at EVERY cell.
    worst_margin = min(res.graph[a] - res.rag[a][k] for a in res.rag for k in PASSAGE_K_GRID)
    graph_dominates = worst_margin >= DOMINATION_MARGIN
    # 4. measurement-frozen: passage_k starvation actually bites the floor (per row).
    smallest_drop = min(res.rag[a][kmax] - res.rag[a][kmin] for a in res.rag)
    floor_starves = smallest_drop >= STARVATION_DROP

    return [
        ("graph reachability flat across passage_k (does not read passages)", graph_flat, True),
        ("RAG passage-recall monotone non-increasing as passage_k shrinks", rag_monotone, True),
        (f"graph dominates the lexical floor at every cell (worst margin {worst_margin:.3f} >= {DOMINATION_MARGIN})", graph_dominates, True),
        (f"passage_k starvation bites the floor (smallest rag@{kmax}-rag@{kmin} drop {smallest_drop:.3f} >= {STARVATION_DROP})", floor_starves, True),
    ]


def gate_exit_code(res: CrossoverResult) -> int:
    hard_failed = any(is_hard and not ok for _l, ok, is_hard in evaluate_assertions(res))
    return 1 if hard_failed else 0


def render_crossover_md(res: CrossoverResult) -> str:
    ks = list(PASSAGE_K_GRID)
    lines = [
        "# GoldenGraph reachability vs lexical floor -- ambiguity x passage_k (recall, no LLM)",
        "",
        "graph = whole-chain bridge-recall (passage_k-invariant). rag = lexical top-k",
        "passage-recall vs the gold answer-chain docs. The lexical multi-hop floor tops out",
        "at ~0.5 even at passage_k=10 (later-hop docs don't mention the start entity), so",
        "graph reachability DOMINATES it at every cell -- there is no starvation crossover.",
        "Whether that reachability advantage converts to ANSWERS is the opt-in answer-match",
        "arm's question (reachability != answerability).",
        "",
        "| ambiguity | graph | " + " | ".join(f"rag@{k}" for k in ks) + " |",
        "|---|---|" + "---|" * len(ks),
    ]
    for a in AMBIGUITY_GRID:
        cells = " | ".join(f"{res.rag[a][k]:.3f}" for k in ks)
        lines.append(f"| {a:.2f} | {res.graph[a]:.3f} | {cells} |")
    lines += ["", "## verdicts", "",
              "(assertions 3-4 are measurement-frozen empirical gates, not structural guarantees)"]
    for label, passed, is_hard in evaluate_assertions(res):
        tag = "PASS" if passed else ("FAIL" if is_hard else "WARN")
        lines.append(f"- [{tag}] {label}{'' if is_hard else ' (soft)'}")
    return "\n".join(lines) + "\n"


# --- opt-in real-LLM answer arms (ungated) ---


def _map_answer_to_canon(text: str, surface_to_canon: dict) -> str | None:
    """Longest-surface-first match of any known surface appearing in the model's text;
    return one canonical id (sorted for determinism) or None."""
    low = text.lower()
    for surf in sorted(surface_to_canon, key=len, reverse=True):
        if surf.lower() in low:
            cands = surface_to_canon[surf]
            return sorted(cands)[0] if cands else None
    return None


def llm_answer_rag(passages, question: str, llm, *, surface_to_canon: dict) -> str | None:
    """RAG arm: answer the question from the retrieved passages; map to a canonical id."""
    ctx = "\n".join(f"- {p}" for p in passages)
    prompt = (
        "Answer the question using ONLY these passages. Reply with the entity name only.\n\n"
        f"Passages:\n{ctx}\n\nQuestion: {question}\nAnswer:"
    )
    out = llm.complete(prompt) or ""
    return _map_answer_to_canon(out, surface_to_canon)


def llm_answer_graph(triples, question: str, llm, *, surface_to_canon: dict) -> str | None:
    """Graph arm: answer the question from resolved-subgraph triples; map to a canonical."""
    ctx = "\n".join(f"- {s} {p} {o}" for (s, p, o) in triples)
    prompt = (
        "Answer the question using ONLY these facts. Reply with the entity name only.\n\n"
        f"Facts:\n{ctx}\n\nQuestion: {question}\nAnswer:"
    )
    out = llm.complete(prompt) or ""
    return _map_answer_to_canon(out, surface_to_canon)


@dataclass
class AnswerMatchResult:
    graph: dict  # ambiguity -> {passage_k -> accuracy}
    rag: dict    # ambiguity -> {passage_k -> accuracy}
    budget_exhausted: bool


def answer_match_accuracy(preds, gold) -> float:
    if not gold:
        return 0.0
    hits = sum(1 for p, gd in zip(preds, gold) if p is not None and p == gd)
    return hits / len(gold)


def _question_text(qa, g) -> str:
    start = g.canonical_name(qa.start_entity_id)
    chain = " then ".join(qa.relation_chain)
    return f"Starting from {start}, follow {chain}. What is the final entity?"


def answer_match_grid(*, seed: int, n_questions: int, max_hops: int, llm) -> AnswerMatchResult:
    """Opt-in real-LLM answer-match crossover. NEEDS the wheel (graph arm). Honors
    llm.exhausted (duck-typed; short-circuits remaining cells)."""
    from goldengraph.answer import _retrieve_local

    from . import dials
    from .ablation import _KEYFN, _build_store, _typ_of
    from .engineered import generate_engineered
    from .engines.goldengraph import _NODE_BUDGET, _RETRIEVAL_HOPS
    from .gold import GoldGraph, gold_chain

    graph: dict = {}
    rag: dict = {}
    exhausted = False
    for a in AMBIGUITY_GRID:
        corpus = generate_engineered(seed=seed, n_questions=n_questions, ambiguity=a, max_hops=max_hops)
        g = GoldGraph.from_corpus(corpus)
        s2c = dials.surface_to_canon(g)
        typ_of = _typ_of(g)
        km = _KEYFN["goldengraph"](corpus, g)
        slice_graph, coverage = _build_store(corpus, g, km, typ_of)
        seed_of: dict[str, int] = {}
        for nid in sorted(coverage):
            for c in coverage[nid]:
                seed_of.setdefault(c, nid)
        chains = {qa.id: gold_chain(g, qa) for qa in corpus.questions}
        gold = {qa.id: (chains[qa.id][-1][2] if chains[qa.id] else None) for qa in corpus.questions}
        questions = {qa.id: _question_text(qa, g) for qa in corpus.questions}
        texts = {d.id: d.text for d in corpus.documents}

        graph[a], rag[a] = {}, {}
        for k in PASSAGE_K_GRID:
            rag_preds, graph_preds, golds = [], [], []
            for qa in corpus.questions:
                if getattr(llm, "exhausted", False):
                    exhausted = True
                    break
                golds.append(gold[qa.id])
                topk = lexical_retrieve(corpus.documents, query_terms_for(qa, g), k)
                rag_preds.append(
                    llm_answer_rag([texts[i] for i in topk], questions[qa.id], llm, surface_to_canon=s2c)
                )
                sn = seed_of.get(qa.start_entity_id)
                if sn is None:
                    graph_preds.append(None)
                else:
                    sub = _retrieve_local(
                        slice_graph, [sn], max_hops=_RETRIEVAL_HOPS, node_budget=_NODE_BUDGET
                    )
                    triples = [(e["subj"], e.get("predicate", ""), e["obj"]) for e in sub.get("edges", ())]
                    graph_preds.append(
                        llm_answer_graph(triples, questions[qa.id], llm, surface_to_canon=s2c)
                    )
            rag[a][k] = answer_match_accuracy(rag_preds, golds)
            graph[a][k] = answer_match_accuracy(graph_preds, golds)
            if exhausted:
                break
        if exhausted:
            break
    return AnswerMatchResult(graph=graph, rag=rag, budget_exhausted=exhausted)


def render_answer_match_md(res: AnswerMatchResult) -> str:
    ks = list(PASSAGE_K_GRID)
    lines = [
        "# GoldenGraph crossover -- answer-match (real LLM, opt-in, UNGATED)",
        "",
        "Does the recall crossover flow to answers? graph arm = LLM over resolved subgraph;",
        "rag arm = LLM over top-k lexical passages. A negative (graph never overtakes RAG)",
        "is a valid finding, not a failure.",
        "",
        f"budget_exhausted: {res.budget_exhausted}",
        "",
    ]
    for arm, tbl in (("graph", res.graph), ("rag", res.rag)):
        lines += [f"## {arm} answer-match", "",
                  "| ambiguity | " + " | ".join(f"k={k}" for k in ks) + " |",
                  "|---|" + "---|" * len(ks)]
        for a in AMBIGUITY_GRID:
            if a not in tbl:
                continue
            cells = " | ".join(f"{tbl[a].get(k, float('nan')):.3f}" for k in ks)
            lines.append(f"| {a:.2f} | {cells} |")
        lines.append("")
    return "\n".join(lines) + "\n"
