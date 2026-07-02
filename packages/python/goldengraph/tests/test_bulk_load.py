"""SP2 bulk_load: pre-structured KB -> StoreBatch -> real PyStore. Exercises the
store's append/as_of semantics directly (that IS what is under test), so it needs
goldengraph_native; skip cleanly if the wheel is absent."""
from __future__ import annotations

import json

import pytest

ggn = pytest.importorskip("goldengraph_native")
from goldengraph.bulk import bulk_load  # noqa: E402

_BIG = 1 << 62


def _store():
    from goldengraph_native import _native as gg

    return gg.PyStore()


# stark ids are strings; names/types are plain; one triangle A-B, A-C
_NODES = [("s10", "Alice", "person"), ("s20", "Acme", "org"), ("s30", "Bob", "person")]
_EDGES = [("s10", "works_at", "s20"), ("s10", "knows", "s30")]


def test_single_batch_stores_all_nodes_with_stark_record_keys():
    store = _store()
    out = bulk_load(store, _NODES, _EDGES)
    assert out == {"n_nodes": 3, "n_edges": 2, "n_dropped_edges": 0, "n_batches": 1}
    snap = json.loads(store.snapshot())
    # every node present, keyed by its stark id
    keys = {tuple(e["record_keys"]) for e in snap["entities"].values()}
    assert keys == {("s10",), ("s20",), ("s30",)}


def _by_name(slice_graph):
    return {e["canonical_name"]: e for e in slice_graph.entities()}


def test_edges_remap_to_right_entity_pairs():
    store = _store()
    bulk_load(store, _NODES, _EDGES)
    g = store.as_of(_BIG, _BIG)
    ents = _by_name(g)
    eid = {name: e["entity_id"] for name, e in ents.items()}
    edges = {(e["subj"], e["predicate"], e["obj"]) for e in g.query(list(eid.values()), 1)["edges"]}
    assert (eid["Alice"], "works_at", eid["Acme"]) in edges
    assert (eid["Alice"], "knows", eid["Bob"]) in edges


def test_passthrough_no_merges_or_splits():
    store = _store()
    bulk_load(store, _NODES, _EDGES)
    assert json.loads(store.snapshot())["history"] == []  # distinct keys -> zero HistoryEvent


def test_stark_id_rides_through_as_of():
    # eid_to_stark (the Arm-B translation map) must be recoverable from the slice.
    store = _store()
    bulk_load(store, _NODES, _EDGES)
    g = store.as_of(_BIG, _BIG)
    want = {"Alice": "s10", "Acme": "s20", "Bob": "s30"}
    for e in g.entities():
        assert e["source_refs"] == [want[e["canonical_name"]]]


def test_dangling_edge_dropped_and_counted():
    store = _store()
    out = bulk_load(store, _NODES, [("s10", "works_at", "s99")])  # s99 unknown
    assert out["n_edges"] == 0 and out["n_dropped_edges"] == 1
    g = store.as_of(_BIG, _BIG)
    assert g.query([e["entity_id"] for e in g.entities()], 1)["edges"] == []


def _canonical_state(store):
    """Order-independent (entities, edges) view for parity comparison across batchings."""
    g = store.as_of(_BIG, _BIG)
    ents = {tuple(e["source_refs"]): (e["canonical_name"], e["typ"]) for e in g.entities()}
    eid_to_stark = {e["entity_id"]: e["source_refs"][0] for e in g.entities()}
    edges = sorted(
        (eid_to_stark[e["subj"]], e["predicate"], eid_to_stark[e["obj"]])
        for e in g.query(list(eid_to_stark), 1)["edges"]
    )
    return ents, edges


def test_chunked_matches_single_batch_state():
    a, b = _store(), _store()
    out_single = bulk_load(a, _NODES, _EDGES)
    out_chunk = bulk_load(b, _NODES, _EDGES, chunk_edges=1)  # 1 edge per batch
    assert _canonical_state(a) == _canonical_state(b)  # identical final graph
    assert out_chunk["n_batches"] == 1 + 2  # nodes batch + 2 edge batches
    assert out_single["n_batches"] == 1


def test_chunked_edge_relists_endpoints_without_panic():
    # The whole risk of chunking: an edge batch must re-list its endpoint entities or
    # store.append panics on the missing local id. This asserts it lands the edge.
    store = _store()
    bulk_load(store, _NODES, _EDGES, chunk_edges=1)
    g = store.as_of(_BIG, _BIG)
    assert len(g.query([e["entity_id"] for e in g.entities()], 1)["edges"]) == 2
