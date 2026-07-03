"""SP-moat materialization: one index entry + one store node per cluster; the store
holds the PRE-MERGED graph (edges unioned by Python collapse, not store merge)."""
from __future__ import annotations

import pytest

ggn = pytest.importorskip("goldengraph_native")
from goldengraph.bulk import bulk_load                       # noqa: E402
from goldengraph.stark_moat import collapse_for_index, collapse_for_store  # noqa: E402

_BIG = 1 << 62


def _store():
    from goldengraph_native import _native as gg

    return gg.PyStore()


def test_collapse_for_index_merges_docs_per_cluster():
    nodes2 = [("1#a0", "IL6", "gene"), ("1#a1", "IL 6", "gene"), ("2", "aspirin", "drug")]
    texts2 = ["sentence one", "sentence two", "aspirin doc"]
    ordinal_of = {"1#a0": 0, "1#a1": 0, "2": 1}              # aliases merged into ord 0
    ents = collapse_for_index(nodes2, texts2, ordinal_of)
    by_ord = {e["entity_id"]: e for e in ents}
    assert set(by_ord) == {0, 1}                             # one entry per cluster
    assert "sentence one" in by_ord[0]["canonical_name"] and "sentence two" in by_ord[0]["canonical_name"]


def test_collapse_for_store_unions_neighborhood():
    # aliases 1#a0,1#a1 (cluster 0) each hold one edge to distinct neighbors 2,3
    nodes2 = [("1#a0", "IL6", "gene"), ("1#a1", "IL 6", "gene"),
              ("2", "aspirin", "drug"), ("3", "fever", "effect")]
    edges2 = [("1#a0", "targets", "2"), ("1#a1", "assoc", "3")]
    ordinal_of = {"1#a0": 0, "1#a1": 0, "2": 1, "3": 2}
    coll_nodes, coll_edges = collapse_for_store(nodes2, edges2, ordinal_of)
    assert len(coll_nodes) == 3                              # 3 clusters -> 3 nodes
    store = _store()
    bulk_load(store, coll_nodes, coll_edges)
    g = store.as_of(_BIG, _BIG)
    ord_to_eid = {int(e["source_refs"][0]): e["entity_id"] for e in g.entities()}
    eid0 = ord_to_eid[0]                                     # view-eid != ordinal; map via source_refs
    neighbors = {e["obj"] for e in g.query([eid0], 1)["edges"] if e["subj"] == eid0}
    assert neighbors == {ord_to_eid[1], ord_to_eid[2]}      # UNIONED: both aliases' edges on cluster 0


def test_collapse_drops_intra_cluster_self_loops():
    nodes2 = [("1#a0", "IL6", "gene"), ("1#a1", "IL 6", "gene")]
    edges2 = [("1#a0", "same", "1#a1")]                      # both ends in cluster 0
    ordinal_of = {"1#a0": 0, "1#a1": 0}
    _, coll_edges = collapse_for_store(nodes2, edges2, ordinal_of)
    assert coll_edges == []                                  # self-loop dropped
