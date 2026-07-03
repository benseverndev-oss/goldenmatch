"""Pure oracle: reconstruct the engineered gold graph + walk a question's chain.
No native, no LLM, no network."""
from __future__ import annotations

from erkgbench.qa_e2e.engineered import generate_engineered
from erkgbench.qa_e2e.gold import GoldGraph, gold_chain


def test_gold_graph_rebuilds_edges_from_doc_ids():
    corpus = generate_engineered(seed=7, n_questions=20, ambiguity=0.5, max_hops=4)
    g = GoldGraph.from_corpus(corpus)
    # every traversed-edge document id `src::rel::dst` is an edge in the gold graph
    for d in corpus.documents:
        src, rel, dst = d.id.split("::")
        assert g.has_edge(src, rel, dst)


def test_gold_chain_walks_to_gold_answer():
    corpus = generate_engineered(seed=7, n_questions=20, ambiguity=0.5, max_hops=4)
    g = GoldGraph.from_corpus(corpus)
    for qa in corpus.questions:
        chain = gold_chain(g, qa)  # ordered [(src_id, rel, dst_id), ...]
        assert len(chain) == qa.hop_count
        assert chain[0][0] == qa.start_entity_id
        # the chain's terminal entity's canonical name == gold_answer
        assert g.canonical_name(chain[-1][2]) == qa.gold_answer


def test_gold_graph_ignores_non_edge_documents():
    # A non-edge (MuSiQue-style, not 3-part) doc id must be skipped, not crash.
    from erkgbench.qa_e2e.corpora import Document, QACorpus

    edge = Document(id="gm:a::works_at::gm:b", text="A works at B.")
    musique = Document(id="q1::p0", text="unrelated paragraph")  # 2-part id
    corpus = QACorpus(name="mix", documents=(edge, musique), questions=())
    g = GoldGraph.from_corpus(corpus)
    assert g.edge_count() == 1 and g.has_edge("gm:a", "works_at", "gm:b")
