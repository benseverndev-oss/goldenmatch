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


def _good_result():
    # Mirrors the measured grid (run 28294346180): graph reachability flat per ambiguity
    # and DOMINATING the lexical floor at every cell; the floor decays with passage_k.
    graph = {0.0: 0.90, 0.25: 0.75, 0.5: 0.60, 0.75: 0.71, 1.0: 0.66}
    rag = {
        0.0: {10: 0.53, 5: 0.50, 3: 0.47, 1: 0.27},
        0.25: {10: 0.54, 5: 0.48, 3: 0.46, 1: 0.25},
        0.5: {10: 0.48, 5: 0.42, 3: 0.41, 1: 0.25},
        0.75: {10: 0.43, 5: 0.38, 3: 0.33, 1: 0.22},
        1.0: {10: 0.40, 5: 0.33, 3: 0.29, 1: 0.23},
    }
    return cx.CrossoverResult(graph=graph, rag=rag)


def test_gate_passes_on_well_formed_surface():
    res = _good_result()
    labels = cx.evaluate_assertions(res)
    hard = [(lbl, ok) for lbl, ok, is_hard in labels if is_hard]
    assert all(ok for _lbl, ok in hard), hard
    assert cx.gate_exit_code(res) == 0


def test_gate_fails_when_rag_non_monotone():
    res = _good_result()
    res.rag[0.5][3] = 0.99  # k=3 recall above k=5 -> non-monotone
    assert cx.gate_exit_code(res) == 1


def test_gate_fails_when_graph_does_not_dominate():
    res = _good_result()
    res.graph[0.0] = 0.40  # now below rag@10=0.53 -> domination fails
    assert cx.gate_exit_code(res) == 1


def test_gate_fails_when_floor_does_not_starve():
    res = _good_result()
    for k in (10, 5, 3, 1):
        res.rag[1.0][k] = 0.40  # flat across passage_k -> no starvation drop
    assert cx.gate_exit_code(res) == 1


def test_render_md_is_ascii_and_has_grid():
    md = cx.render_crossover_md(_good_result())
    assert md.isascii()
    assert "passage_k" in md and "## verdicts" in md
