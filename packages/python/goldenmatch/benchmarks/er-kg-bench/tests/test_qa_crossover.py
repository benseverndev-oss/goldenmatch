"""Slice C crossover bench -- wheel-free unit tests (no goldengraph_native)."""
from __future__ import annotations

from erkgbench.qa_e2e import crossover as cx
from erkgbench.qa_e2e.corpora import Document


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
