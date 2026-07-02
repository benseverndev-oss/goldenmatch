"""Substrate-quality eval: pure scoring over a built graph (alignment / coherence / provenance / A-B)."""
from __future__ import annotations

from erkgbench.substrate_eval import (
    align_mentions_to_nodes,
    align_real_mentions_to_nodes,
    align_real_mentions_to_nodes_aliased,
    edge_recall,
    fragmentation_report,
    graph_coherence,
    provenance_coverage,
    real_alignment_coverage,
    real_alignment_coverage_aliased,
    score_substrate,
)


def _ent(eid, name, typ="thing"):
    return {"entity_id": eid, "canonical_name": name, "typ": typ}


def _rent(eid, *surfaces):  # entity with surface_names for the real (surface+doc) aligner
    return {"entity_id": eid, "canonical_name": surfaces[0], "typ": "thing", "surface_names": list(surfaces)}


def test_align_real_surface_and_doc_match():
    graph = {"entities": [_rent(5, "IBM"), _rent(1, "Red Hat")],
             "edges": [{"subj": 5, "obj": 1, "predicate": "acquired", "source_refs": ["d1"]}]}
    gm = [("Q_ibm", "IBM", "d1"), ("Q_rh", "Red Hat", "d1")]
    assert sorted(map(sorted, align_real_mentions_to_nodes(graph, gm))) == [[0], [1]]
    assert real_alignment_coverage(graph, gm) == 1.0


def test_align_real_exact_beats_substring_and_orphan_unique():
    graph = {"entities": [_rent(7, "Apple"), _rent(8, "Apple Inc")],
             "edges": [{"subj": 7, "obj": 8, "predicate": "r", "source_refs": ["d1"]}]}
    gm = [("Qa", "Apple", "d1"), ("Qx", "Ghost", "d1"), ("Qy", "Nowhere", "d1")]
    clusters = sorted(map(sorted, align_real_mentions_to_nodes(graph, gm)))
    assert [0] in clusters                          # Apple -> node 7 (exact), own cluster
    assert sum(len(c) for c in clusters) == 3       # 2 orphans stay SEPARATE (unique negatives)
    assert real_alignment_coverage(graph, gm) == 1 / 3


def test_aliased_match_finds_node_when_wikilink_surface_misses():
    # gold surface "Big Blue" != node surface "IBM"; the QID alias set bridges it
    graph = {"entities": [_rent(5, "IBM"), _rent(1, "Red Hat")],
             "edges": [{"subj": 5, "obj": 1, "predicate": "acquired", "source_refs": ["Q37156"]}]}
    gm = [("Q37156", "Big Blue", "Q37156"), ("Qrh", "Red Hat", "Q37156")]
    aliases = {"Q37156": {"ibm", "big blue", "international business machines"}, "Qrh": {"red hat"}}
    clusters = sorted(map(sorted, align_real_mentions_to_nodes_aliased(graph, gm, aliases)))
    assert clusters == [[0], [1]]                            # Big Blue -> node 5 via alias "ibm"
    assert real_alignment_coverage_aliased(graph, gm, aliases) == 1.0


def test_aliased_orphan_unique_and_coverage():
    graph = {"entities": [_rent(5, "IBM"), _rent(8, "Apple")],
             "edges": [{"subj": 5, "obj": 8, "predicate": "r", "source_refs": ["d1"]}]}
    gm = [("Q37156", "IBM", "d1"), ("Qx", "Ghost", "d1")]
    aliases = {"Q37156": {"ibm"}}                            # Qx: no alias + no surface match -> orphan
    clusters = sorted(map(sorted, align_real_mentions_to_nodes_aliased(graph, gm, aliases)))
    assert [0] in clusters and sum(len(c) for c in clusters) == 2
    assert real_alignment_coverage_aliased(graph, gm, aliases) == 0.5


def test_aliased_substring_fallback_when_extracted_form_is_novel():
    # the 7B extracted "IBM Corporation" -- not a verbatim alias/surface; substring bridges via "ibm"
    graph = {"entities": [_rent(5, "IBM Corporation")],
             "edges": [{"subj": 5, "obj": 5, "predicate": "r", "source_refs": ["d1"]}]}
    gm = [("Q37156", "IBM", "d1")]
    aliases = {"Q37156": {"ibm", "ibm corp."}}          # exact-intersect misses "ibm corporation"
    assert align_real_mentions_to_nodes_aliased(graph, gm, aliases) == [[0]]   # substring "ibm" hits
    assert real_alignment_coverage_aliased(graph, gm, aliases) == 1.0


def test_aliased_reduces_to_exact_surface_when_alias_is_surface():
    graph = {"entities": [_rent(7, "Apple")],
             "edges": [{"subj": 7, "obj": 7, "predicate": "r", "source_refs": ["d1"]}]}
    gm = [("Qa", "Apple", "d1")]
    assert align_real_mentions_to_nodes_aliased(graph, gm, {"Qa": {"apple"}}) == [[0]]


def test_align_real_reproduces_engineered_oracle():
    # SANITY GUARD: on an engineered-shaped graph the surface aligner must match the doc-id oracle.
    graph = {"entities": [_rent(0, "A"), _rent(1, "B"), _rent(2, "C")],
             "edges": [{"subj": 0, "obj": 1, "predicate": "r", "source_refs": ["A::r::B"]},
                       {"subj": 0, "obj": 2, "predicate": "r2", "source_refs": ["A::r2::C"]}]}
    gm = [("A", "A", "A::r::B"), ("B", "B", "A::r::B"), ("A", "A", "A::r2::C"), ("C", "C", "A::r2::C")]
    assert (sorted(map(sorted, align_real_mentions_to_nodes(graph, gm)))
            == sorted(map(sorted, align_mentions_to_nodes(graph, gm))))


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


def test_edge_recall_counts_gold_docs_with_surviving_edge():
    # 3 gold edge-docs; build produced an edge for only 2 (doc "A::r3::D" dropped -> extraction/self-loop)
    gm = [
        ("A", "A", "A::r::B"), ("B", "B", "A::r::B"),
        ("A", "A", "A::r2::C"), ("C", "C", "A::r2::C"),
        ("A", "A", "A::r3::D"), ("D", "D", "A::r3::D"),
    ]
    graph = {"entities": [], "edges": [_edge(0, 1, "A::r::B"), _edge(0, 2, "A::r2::C", "r2")]}
    assert edge_recall(graph, gm) == 2 / 3
    # a ::N co-occurrence suffix on a source_ref still counts its base doc
    graph2 = {"entities": [], "edges": [_edge(0, 1, "A::r::B::1")]}
    assert edge_recall(graph2, [("A", "A", "A::r::B"), ("B", "B", "A::r::B")]) == 1.0
    assert edge_recall({"edges": []}, []) == 1.0  # no gold -> 1.0


def test_fragmentation_report_attributes_name_vs_type_jitter():
    # gold entity "A" appears in two docs; the build put it in two nodes (5 and 6) that differ by NAME
    gm = [("A", "A", "A::r::B"), ("B", "B", "A::r::B"), ("A", "A", "A::r2::C"), ("C", "C", "A::r2::C")]
    graph = {
        "entities": [_ent(5, "A"), _ent(6, "Ay"), _ent(1, "B"), _ent(2, "C")],
        "edges": [_edge(5, 1, "A::r::B"), _edge(6, 2, "A::r2::C", "r2")],
    }
    fr = fragmentation_report(graph, gm)
    assert fr["fragmented_entities"] == 1 and fr["total_entities"] == 3
    assert fr["mean_nodes_per_entity"] == (2 + 1 + 1) / 3   # A->2 nodes, B->1, C->1
    assert fr["name_jitter_frac"] == 1.0 and fr["type_jitter_frac"] == 0.0
    assert fr["identical_frac"] == 0.0
    # type jitter: same name, different typ -> attributed to type not name
    graph_t = {
        "entities": [_ent(5, "A", "person"), _ent(6, "A", "org"), _ent(1, "B"), _ent(2, "C")],
        "edges": [_edge(5, 1, "A::r::B"), _edge(6, 2, "A::r2::C", "r2")],
    }
    ft = fragmentation_report(graph_t, gm)
    assert ft["type_jitter_frac"] == 1.0 and ft["name_jitter_frac"] == 0.0


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


def test_score_substrate_assembles_a_b_gap():
    gm = [("A", "A", "A::r::B"), ("B", "B", "A::r::B"), ("A", "A", "A::r2::C"), ("C", "C", "A::r2::C")]
    # Level A: a PERFECT resolver clustering (A's two mentions together)
    resolver_clusters = [[0, 2], [1], [3]]
    # Level B graph: under-merge split A across node0 and node9 -> worse than A
    graph = {"entities": [{"entity_id": n, "canonical_name": "x", "surface_names": ["x"]} for n in (0, 1, 2, 9)],
             "edges": [{"subj": 0, "predicate": "r", "obj": 1, "source_refs": ["A::r::B"]},
                       {"subj": 9, "predicate": "r2", "obj": 2, "source_refs": ["A::r2::C"]}]}
    sb = score_substrate(gold_mentions=gm, resolver_clusters=resolver_clusters, graph=graph)
    assert sb["er_f1_a"] == 1.0                       # perfect resolver
    assert sb["er_f1_b"] < sb["er_f1_a"]              # build fragmented A -> B worse
    assert abs(sb["ab_gap"] - (sb["er_f1_a"] - sb["er_f1_b"])) < 1e-9
    assert sb["components"] == 2 and 0.0 <= sb["provenance"] <= 1.0


# --- GLiNER entity-recall probe (gliner_probe_report) ---
from erkgbench.substrate_eval import gliner_probe_report


def _graph(entities, edges):
    return {"entities": entities, "edges": edges}


def test_probe_splits_ner_miss_from_edge_miss():
    # gold A: aligned (node 1 has an in-doc edge). gold B: edge-miss (node 2 exists, no edge in its doc).
    # gold C: ner-miss (no node matches its aliases anywhere).
    entities = [
        {"entity_id": 1, "canonical_name": "Apple", "surface_names": ["Apple"], "typ": "org"},
        {"entity_id": 2, "canonical_name": "Tim Cook", "surface_names": ["Tim Cook"], "typ": "person"},
    ]
    edges = [{"subj": 1, "obj": 1, "predicate": "is", "source_refs": ["docA"]}]  # only docA has an edge
    graph = _graph(entities, edges)
    gold = [
        ("Qa", "apple", "docA"),      # aligned (node 1, docA edge)
        ("Qb", "tim cook", "docB"),   # edge-miss: node 2 exists but docB has no edge
        ("Qc", "sundar pichai", "docC"),  # ner-miss: no node matches
    ]
    aliases = {"Qa": ["apple"], "Qb": ["tim cook"], "Qc": ["sundar pichai"]}
    # GLiNER finds the edge-miss AND the ner-miss entity in their docs
    gliner_by_doc = {"docB": {"Tim Cook"}, "docC": {"Sundar Pichai"}}
    r = gliner_probe_report(graph, gold, aliases, gliner_by_doc)
    assert r["n_gold"] == 3
    assert r["n_missed"] == 2          # B and C
    assert r["n_edge_miss"] == 1       # B
    assert r["n_ner_miss"] == 1        # C
    # the true prize: of the 1 ner-miss, GLiNER found 1
    assert r["ner_recovered_frac"] == 1.0
    # conflated context metric counts both missed that GLiNER matched
    assert r["residual_recovered_frac"] == 1.0


def test_probe_case_folds_gliner_surface():
    # cased GLiNER surface must match a lowercased alias/gold set (guards false REFUTED).
    graph = _graph([], [])
    gold = [("Qx", "barack obama", "d1")]
    aliases = {"Qx": ["barack obama"]}
    r = gliner_probe_report(graph, gold, aliases, {"d1": {"Barack Obama"}})
    assert r["gliner_recall"] == 1.0


def test_probe_alias_and_substring_and_per_doc_match():
    graph = _graph([], [])
    gold = [
        ("Qibm", "big blue", "d1"),        # matches via alias "ibm"
        ("Qn", "thomas nabbes", "d2"),     # matches via substring "nabbes"
        ("Qz", "zeta", "d3"),              # no gliner match
    ]
    aliases = {"Qibm": ["ibm", "big blue"], "Qn": ["thomas nabbes"], "Qz": ["zeta"]}
    gliner_by_doc = {
        "d1": {"IBM"},          # alias match
        "d2": {"Nabbes"},       # substring match
        "d3": {"Yeti"},         # unrelated -> junk, no gold match
    }
    r = gliner_probe_report(graph, gold, aliases, gliner_by_doc)
    assert r["gliner_recall"] == 2 / 3
    # per-doc: a d1 surface must not match a d3 gold
    # junk: "Yeti" in d3 matches no d3 gold -> 1 junk of 3 total surfaces
    assert r["junk_rate"] == 1 / 3


def test_probe_junk_rate_all_match_is_zero():
    graph = _graph([], [])
    gold = [("Qa", "apple", "d1")]
    aliases = {"Qa": ["apple"]}
    r = gliner_probe_report(graph, gold, aliases, {"d1": {"Apple"}})
    assert r["junk_rate"] == 0.0


def test_probe_degenerate_guards():
    graph = _graph([], [])
    # empty gliner
    r = gliner_probe_report(graph, [("Qa", "apple", "d1")], {"Qa": ["apple"]}, {})
    assert r["gliner_recall"] == 0.0 and r["ner_recovered_frac"] == 0.0 and r["junk_rate"] == 0.0
    # empty gold
    r0 = gliner_probe_report(graph, [], {}, {"d1": {"Apple"}})
    assert r0["n_gold"] == 0 and r0["residual_recovered_frac"] == 0.0
    # all-aligned (|missed| == 0): one gold, one node with an in-doc edge
    g2 = _graph([{"entity_id": 1, "canonical_name": "Apple", "surface_names": ["Apple"], "typ": "org"}],
                [{"subj": 1, "obj": 1, "predicate": "is", "source_refs": ["d1"]}])
    r2 = gliner_probe_report(g2, [("Qa", "apple", "d1")], {"Qa": ["apple"]}, {"d1": {"Apple"}})
    assert r2["n_missed"] == 0 and r2["residual_recovered_frac"] == 0.0 and r2["ner_recovered_frac"] == 0.0


# --- node-provenance aligner union (edge-endpoints U node source_refs) ---
from erkgbench.substrate_eval import _assign_real_nodes_aliased


def _graph_prov(entities, edges):
    return {"entities": entities, "edges": edges}


def test_aligner_reaches_node_via_source_refs_not_edge():
    # node 1 edged only in docB but source_refs = {docA, docB}; gold for it in docA.
    entities = [{"entity_id": 1, "canonical_name": "IBM", "surface_names": ["IBM"],
                 "typ": "org", "source_refs": ["docA", "docB"]}]
    edges = [{"subj": 1, "obj": 1, "predicate": "is", "source_refs": ["docB"]}]
    gold = [("Qibm", "ibm", "docA")]
    aliases = {"Qibm": ["ibm"]}
    node_of = _assign_real_nodes_aliased(_graph_prov(entities, edges), gold, aliases)
    assert node_of[0] == 1          # reached via source_refs (docA), though its edge is in docB


def test_aligner_byte_identical_without_node_source_refs():
    # no entity carries source_refs -> candidate set is edge-only, exactly as before.
    entities = [{"entity_id": 1, "canonical_name": "IBM", "surface_names": ["IBM"], "typ": "org"}]
    edges = [{"subj": 1, "obj": 1, "predicate": "is", "source_refs": ["docA"]}]
    gold = [("Qibm", "ibm", "docA"), ("Qz", "zeta", "docB")]
    aliases = {"Qibm": ["ibm"], "Qz": ["zeta"]}
    node_of = _assign_real_nodes_aliased(_graph_prov(entities, edges), gold, aliases)
    assert node_of[0] == 1 and node_of[1] < 0   # docA aligns via edge; docB orphan (no node there)


def test_aligner_mixed_provenance_no_regression():
    # one entity has source_refs, one doesn't; the without-prov entity still aligns via its edge.
    entities = [
        {"entity_id": 1, "canonical_name": "IBM", "surface_names": ["IBM"], "typ": "org",
         "source_refs": ["docA"]},
        {"entity_id": 2, "canonical_name": "Apple", "surface_names": ["Apple"], "typ": "org"},  # no refs
    ]
    edges = [{"subj": 2, "obj": 2, "predicate": "is", "source_refs": ["docB"]}]
    gold = [("Qibm", "ibm", "docA"), ("Qap", "apple", "docB")]
    aliases = {"Qibm": ["ibm"], "Qap": ["apple"]}
    node_of = _assign_real_nodes_aliased(_graph_prov(entities, edges), gold, aliases)
    assert node_of[0] == 1 and node_of[1] == 2   # IBM via source_refs, Apple via edge -- both align
