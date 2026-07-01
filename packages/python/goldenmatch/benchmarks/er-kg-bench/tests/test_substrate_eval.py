"""Substrate-quality eval: pure scoring over a built graph (alignment / coherence / provenance / A-B)."""
from __future__ import annotations

from erkgbench.substrate_eval import align_mentions_to_nodes, graph_coherence, provenance_coverage


def _edge(subj, obj, doc, pred="r"):
    return {"subj": subj, "predicate": pred, "obj": obj, "source_refs": [doc]}


def test_emit_gold_mentions_from_documents():
    from erkgbench.qa_e2e.engineered import emit_gold_mentions

    class _Doc:  # mimic corpora.Document (id + src_surface + dst_surface)
        def __init__(self, id, ss, ds):
            self.id, self.src_surface, self.dst_surface = id, ss, ds

    docs = [_Doc("gm:a::works_at::gm:b", "Ay", "Bee"),
            _Doc("gm:a::located_in::gm:c", "Ay", "Cee"),
            _Doc("gm:a::works_at::gm:b::1", "X", "Y")]   # a co-occurrence extra (::1) -> SKIPPED
    mentions = emit_gold_mentions(docs)
    assert mentions == [
        ("gm:a", "Ay", "gm:a::works_at::gm:b"), ("gm:b", "Bee", "gm:a::works_at::gm:b"),
        ("gm:a", "Ay", "gm:a::located_in::gm:c"), ("gm:c", "Cee", "gm:a::located_in::gm:c"),
    ]


def test_align_clean_one_node_per_entity():
    # docs: A::r::B and A::r2::C ; build kept both edges, endpoints distinct nodes
    gm = [("A", "A", "A::r::B"), ("B", "B", "A::r::B"), ("A", "A", "A::r2::C"), ("C", "C", "A::r2::C")]
    graph = {"entities": [], "edges": [_edge(0, 1, "A::r::B"), _edge(0, 2, "A::r2::C", "r2")]}
    clustering = align_mentions_to_nodes(graph, gm)
    # mention 0 (A) -> node0, 1 (B)->node1, 2 (A)->node0, 3 (C)->node2
    assert sorted(map(sorted, clustering)) == [[0, 2], [1], [3]]   # A's two mentions share node0


def test_align_entity_split_recall_loss():
    # A appears in two docs but under-merge put it in DIFFERENT nodes (0 and 9)
    gm = [("A", "A", "A::r::B"), ("B", "B", "A::r::B"), ("A", "A", "A::r2::C"), ("C", "C", "A::r2::C")]
    graph = {"entities": [], "edges": [_edge(0, 1, "A::r::B"), _edge(9, 2, "A::r2::C", "r2")]}
    clustering = align_mentions_to_nodes(graph, gm)
    assert [0] in [sorted(c) for c in clustering] and [2] in [sorted(c) for c in clustering]  # A split


def test_align_node_absorbs_two_entities_precision_loss():
    # B and C both landed in node 5 (cross-doc over-merge of distinct entities)
    gm = [("A", "A", "A::r::B"), ("B", "B", "A::r::B"), ("D", "D", "D::r::C"), ("C", "C", "D::r::C")]
    graph = {"entities": [], "edges": [_edge(0, 5, "A::r::B"), _edge(3, 5, "D::r::C")]}
    clustering = align_mentions_to_nodes(graph, gm)
    assert [1, 3] in [sorted(c) for c in clustering]   # B(idx1) + C(idx3) share node5 -> precision loss


def test_align_shared_surface_collision_disambiguated_by_doc():
    # A and X share the surface "Ay" but are different entities in different docs -> doc keys them apart
    gm = [("A", "Ay", "A::r::B"), ("B", "B", "A::r::B"), ("X", "Ay", "X::r::Y"), ("Y", "Y", "X::r::Y")]
    graph = {"entities": [], "edges": [_edge(0, 1, "A::r::B"), _edge(7, 8, "X::r::Y")]}
    clustering = align_mentions_to_nodes(graph, gm)
    # A(idx0)->node0, X(idx2)->node7 ; the shared surface did NOT merge them
    flat = {tuple(sorted(c)) for c in clustering}
    assert (0,) in flat and (2,) in flat


def test_align_extraction_miss_singleton():
    # doc D::r::E produced NO edge (extraction dropped it) -> both mentions are singletons
    gm = [("D", "D", "D::r::E"), ("E", "E", "D::r::E")]
    graph = {"entities": [], "edges": []}
    clustering = align_mentions_to_nodes(graph, gm)
    assert sorted(map(sorted, clustering)) == [[0], [1]]


def test_align_strips_cooccur_suffix():
    # build edge's source_ref carries the ::1 co-occurrence suffix; base doc id still matches
    gm = [("A", "A", "A::r::B"), ("B", "B", "A::r::B")]
    graph = {"entities": [], "edges": [{"subj": 0, "predicate": "r", "obj": 1, "source_refs": ["A::r::B::1"]}]}
    clustering = align_mentions_to_nodes(graph, gm)
    assert sorted(map(sorted, clustering)) == [[0], [1]]   # matched via base id


def test_coherence_components_and_largest_fraction():
    # nodes 0-1 connected, 2-3 connected, 4 isolated -> 3 components, largest = 2/5
    graph = {"entities": [{"entity_id": i, "canonical_name": str(i), "surface_names": [str(i)]} for i in range(5)],
             "edges": [{"subj": 0, "predicate": "r", "obj": 1, "source_refs": ["d"]},
                       {"subj": 2, "predicate": "r", "obj": 3, "source_refs": ["d"]}]}
    coh = graph_coherence(graph)
    assert coh["components"] == 3 and abs(coh["largest_fraction"] - 0.4) < 1e-9


def test_provenance_coverage():
    graph = {"entities": [], "edges": [
        {"subj": 0, "predicate": "r", "obj": 1, "source_refs": ["d"]},
        {"subj": 1, "predicate": "r", "obj": 2, "source_refs": []}]}
    assert provenance_coverage(graph) == 0.5   # 1 of 2 edges has a source_ref
