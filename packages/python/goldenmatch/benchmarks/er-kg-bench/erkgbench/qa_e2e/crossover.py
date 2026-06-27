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


#: Frozen from the local measured grid (Task 5). Placeholders -- TIGHTEN after measuring.
RAG_HIGH_FLOOR = 0.85     # rag_recall at passage_k=10 must be at least this (retriever sane)
CROSSOVER_MARGIN = 0.2    # max over cells of (graph - rag) must reach this (crossover exists)


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
    """[(label, passed, is_hard), ...]. HARD gates; soft only warns."""
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
    # 3. retriever-sanity: RAG starts high at the largest passage_k.
    rag_starts_high = all(res.rag[a][kmax] >= RAG_HIGH_FLOOR for a in res.rag)
    # 4. measurement-frozen: a crossover cell exists somewhere (argmax graph-RAG margin).
    best_margin = max(res.graph[a] - res.rag[a][k] for a in res.rag for k in PASSAGE_K_GRID)
    crossover_exists = best_margin >= CROSSOVER_MARGIN

    return [
        ("graph reachability flat across passage_k (does not read passages)", graph_flat, True),
        ("RAG passage-recall monotone non-increasing as passage_k shrinks", rag_monotone, True),
        (f"RAG passage-recall >= {RAG_HIGH_FLOOR} at passage_k={kmax} (retriever sane)", rag_starts_high, True),
        (f"a crossover cell exists (max graph-RAG margin {best_margin:.3f} >= {CROSSOVER_MARGIN}, k={kmin} most starved)", crossover_exists, True),
    ]


def gate_exit_code(res: CrossoverResult) -> int:
    hard_failed = any(is_hard and not ok for _l, ok, is_hard in evaluate_assertions(res))
    return 1 if hard_failed else 0


def render_crossover_md(res: CrossoverResult) -> str:
    ks = list(PASSAGE_K_GRID)
    lines = [
        "# GoldenGraph crossover -- ambiguity x passage_k (recall, no LLM)",
        "",
        "graph = whole-chain bridge-recall (passage_k-invariant). rag = lexical top-k",
        "passage-recall vs the gold answer-chain docs. Where does graph overtake RAG?",
        "",
        "| ambiguity | graph | " + " | ".join(f"rag@{k}" for k in ks) + " |",
        "|---|---|" + "---|" * len(ks),
    ]
    for a in AMBIGUITY_GRID:
        cells = " | ".join(f"{res.rag[a][k]:.3f}" for k in ks)
        lines.append(f"| {a:.2f} | {res.graph[a]:.3f} | {cells} |")
    lines += ["", "## verdicts", "",
              "(assertion 4 is a measurement-frozen empirical gate, not a structural guarantee)"]
    for label, passed, is_hard in evaluate_assertions(res):
        tag = "PASS" if passed else ("FAIL" if is_hard else "WARN")
        lines.append(f"- [{tag}] {label}{'' if is_hard else ' (soft)'}")
    return "\n".join(lines) + "\n"
