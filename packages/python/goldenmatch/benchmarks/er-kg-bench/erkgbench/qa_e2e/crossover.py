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
