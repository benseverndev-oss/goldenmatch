"""Slice C crossover bench -- wheel-free unit tests (no goldengraph_native)."""
from __future__ import annotations

from erkgbench.qa_e2e import crossover as cx
from erkgbench.qa_e2e.corpora import Document
from erkgbench.qa_e2e.engineered import generate_engineered
from erkgbench.qa_e2e.gold import GoldGraph


def _docs():
    return (
        Document(id="x::works_at::a", text="X works at Apple.", src_surface="X", dst_surface="Apple"),
        Document(id="a::located_in::b", text="Apple located in Cupertino.", src_surface="Apple", dst_surface="Cupertino"),
        Document(id="z::founded::w", text="Zeta founded Widgets.", src_surface="Zeta", dst_surface="Widgets"),
    )


def test_lexical_retrieve_ranks_by_overlap_then_id():
    got = cx.lexical_retrieve(_docs(), ["x", "works", "at"], 2)
    assert got[0] == "x::works_at::a"
    assert len(got) == 2


def test_lexical_retrieve_is_nested_prefix_in_k():
    terms = ["apple", "located", "in"]
    top3 = cx.lexical_retrieve(_docs(), terms, 3)
    top1 = cx.lexical_retrieve(_docs(), terms, 1)
    top2 = cx.lexical_retrieve(_docs(), terms, 2)
    assert top1 == top3[:1]
    assert top2 == top3[:2]


def test_lexical_retrieve_ties_broken_by_doc_id():
    got = cx.lexical_retrieve(_docs(), ["nonexistent"], 3)
    assert got == sorted(d.id for d in _docs())[:3]


def test_query_terms_include_relation_tokens():
    corpus = generate_engineered(seed=7, n_questions=8, ambiguity=0.0, max_hops=3)
    g = GoldGraph.from_corpus(corpus)
    qa = corpus.questions[0]
    terms = cx.query_terms_for(qa, g)
    for rel in qa.relation_chain:
        for tok in rel.split("_"):
            assert tok.lower() in terms


def test_passage_recall_fraction_of_gold_support():
    class _QA:
        gold_supporting_fact_ids = ("a::r::b", "b::r::c")

    assert cx.passage_recall(_QA(), ["a::r::b", "zzz"]) == 0.5
    assert cx.passage_recall(_QA(), ["a::r::b", "b::r::c"]) == 1.0
    assert cx.passage_recall(_QA(), []) == 0.0
