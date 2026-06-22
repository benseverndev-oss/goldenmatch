"""Answerability guard for the engineered QA corpus.

The whole evidence-program headline rides on the engineered corpus being
*answerable*: if a perfect graph-walker with the full KG cannot recover the gold
answer, then no GraphRAG engine can either, and every engine scores ~0 (which is
exactly what the 2026-06-21 head-to-head run produced -- the old "follow the
chain from X" phrasing named neither the relations nor the hop count, so the gold
answer was one arbitrary walk among many).

These tests encode the contract: an oracle that walks the structural graph must
score answer_match == 1.0 on every generated question, and at ambiguity=0 the
supporting documents must literally contain the canonical chain.
"""
from __future__ import annotations

import sys
from pathlib import Path

# _BENCH_ROOT bootstrap: make `erkgbench` + `dataset` importable regardless of mode.
_BENCH_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BENCH_ROOT))

from dataset.concepts_loader import load_concepts  # noqa: E402
from erkgbench.qa_e2e import metrics  # noqa: E402
from erkgbench.qa_e2e.engineered import generate_engineered  # noqa: E402


def _id_to_canonical() -> dict[str, str]:
    concepts = load_concepts(_BENCH_ROOT / "dataset" / "concepts.jsonl")
    return {c.canonical_id: c.concept for c in concepts}


def _rebuild_edges(corpus) -> dict[str, dict[str, str]]:
    """Reconstruct {src: {relation: dst}} purely from the document ids (which encode
    `src::rel::dst` with canonical ids). This is independent of the generator's
    in-memory state, so a mismatch between the recorded relation_chain and the
    actual edges/documents would surface here."""
    edges: dict[str, dict[str, str]] = {}
    for doc in corpus.documents:
        src, rel, dst = doc.id.split("::")
        edges.setdefault(src, {})[rel] = dst
    return edges


def _oracle_answer(corpus, q) -> str:
    """Walk the structural graph along the question's stated relation chain and
    return the terminal entity's canonical name."""
    edges = _rebuild_edges(corpus)
    id2canon = _id_to_canonical()
    cur = q.start_entity_id
    for rel in q.relation_chain:
        cur = edges[cur][rel]
    return id2canon[cur]


def test_oracle_answers_every_engineered_question():
    corpus = generate_engineered(seed=20260620, n_questions=60, ambiguity=0.5)
    assert corpus.questions, "generator produced no questions"
    for q in corpus.questions:
        assert q.start_entity_id and q.relation_chain, f"{q.id} missing gold metadata"
        assert len(q.relation_chain) == q.hop_count
        oracle = _oracle_answer(corpus, q)
        # The oracle reaches the gold entity ...
        assert oracle == q.gold_answer, f"{q.id}: oracle {oracle!r} != gold {q.gold_answer!r}"
        # ... and that answer scores a perfect containment match (the metric the
        # head-to-head reports). If this is ever 0, the corpus is unanswerable.
        assert metrics.answer_match(oracle, q.gold_answer) == 1.0


def test_each_entity_has_at_most_one_edge_per_relation():
    """Uniqueness of (entity, relation) is what makes a relation sequence determine a
    single answer. Two edges sharing a relation would reintroduce ambiguity."""
    corpus = generate_engineered(seed=1, n_questions=40, ambiguity=0.3)
    seen: set[tuple[str, str]] = set()
    for doc in corpus.documents:
        src, rel, _dst = doc.id.split("::")
        key = (src, rel)
        assert key not in seen, f"duplicate (entity, relation) edge: {key}"
        seen.add(key)


def test_supporting_facts_are_the_traversed_edges():
    corpus = generate_engineered(seed=5, n_questions=40, ambiguity=0.5)
    doc_ids = {d.id for d in corpus.documents}
    for q in corpus.questions:
        assert len(q.gold_supporting_fact_ids) == q.hop_count
        # every traversed edge is an actual emitted document
        assert set(q.gold_supporting_fact_ids) <= doc_ids
        # and the chain of edge endpoints is connected start -> ... -> answer
        cur = q.start_entity_id
        for edge_id in q.gold_supporting_fact_ids:
            src, _rel, dst = edge_id.split("::")
            assert src == cur, f"{q.id}: edge {edge_id} does not start at {cur}"
            cur = dst


def test_ambiguity_zero_documents_contain_the_canonical_chain():
    """At ambiguity=0 every mention is canonical, so the supporting documents
    literally spell out the chain -- a text-only reader could answer. This is the
    floor case the decay curve sweeps up from."""
    corpus = generate_engineered(seed=9, n_questions=40, ambiguity=0.0)
    id2canon = _id_to_canonical()
    docs = {d.id: d.text for d in corpus.documents}
    for q in corpus.questions:
        for edge_id in q.gold_supporting_fact_ids:
            src, _rel, dst = edge_id.split("::")
            text = docs[edge_id]
            assert id2canon[src] in text
            assert id2canon[dst] in text
