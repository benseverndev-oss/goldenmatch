"""Slice C: ambiguity x passage_k crossover. A free, deterministic recall-crossover
gate (graph reachability flat in passage_k vs lexical passage-recall decay) plus an
opt-in real-LLM answer-match crossover headline. Self-contained -- no #1270 hybrid dep.

The deterministic recall surfaces + gate + render are wheel-free; only graph_recall_at
(reused slice-A store build) needs the goldengraph_native wheel.
"""
from __future__ import annotations

import re

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
