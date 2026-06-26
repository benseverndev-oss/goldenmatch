"""Pure tests for the path-preserving hybrid subgraph filter (no native, no LLM,
no network). The filter must be CHAIN-SAFE: every entity on a path between two
anchors survives; only off-path leaves the wide ball dragged in are dropped --
the property the 2026-06-22 topology-blind predicate-focus revert lacked."""

from __future__ import annotations

from goldengraph.subgraph_filter import filter_subgraph_to_paths


def _ent(i, name="n"):
    return {"entity_id": i, "canonical_name": f"{name}{i}", "typ": "concept"}


def _sub(entity_ids, edges):
    """edges: list of (subj, obj) -- predicate filled in."""
    return {
        "entities": [_ent(i) for i in entity_ids],
        "edges": [{"subj": s, "predicate": "rel", "obj": o} for (s, o) in edges],
    }


def _ids(sub):
    return sorted(e["entity_id"] for e in sub["entities"])


def test_keeps_anchor_to_anchor_chain_drops_offtopic_leaves():
    # chain 0->1->2->3 between anchors 0 and 3; 1 also has off-topic leaves 4,5.
    sub = _sub([0, 1, 2, 3, 4, 5], [(0, 1), (1, 2), (2, 3), (1, 4), (4, 5)])
    out = filter_subgraph_to_paths(sub, [0, 3], halo=0)
    assert _ids(out) == [0, 1, 2, 3]  # bridges 1,2 kept; leaves 4,5 dropped
    # edges fully inside the kept set survive; edges touching a dropped node go
    assert {(e["subj"], e["obj"]) for e in out["edges"]} == {(0, 1), (1, 2), (2, 3)}


def test_follows_edges_undirected():
    # answer edge points 3->2 (reverse); path 0->1->2<-3 must still connect 0 and 3.
    sub = _sub([0, 1, 2, 3], [(0, 1), (1, 2), (3, 2)])
    out = filter_subgraph_to_paths(sub, [0, 3], halo=0)
    assert _ids(out) == [0, 1, 2, 3]


def test_halo_keeps_direct_neighbor_of_single_seed():
    # one seed (0) with neighbor 1 and a 2-hop node 2.
    sub = _sub([0, 1, 2], [(0, 1), (1, 2)])
    out1 = filter_subgraph_to_paths(sub, [0], halo=1)
    assert _ids(out1) == [0, 1]  # halo=1 keeps the direct neighbor, not the 2-hop
    out0 = filter_subgraph_to_paths(sub, [0], halo=0)
    assert _ids(out0) == [0]  # halo=0 keeps only the seed


def test_determinism_equal_length_paths_lowest_id_next_hop():
    # two equal-length paths 0->1->3 and 0->2->3; deterministic pick keeps the
    # lowest-id next hop (1), not 2.
    sub = _sub([0, 1, 2, 3], [(0, 1), (0, 2), (1, 3), (2, 3)])
    out = filter_subgraph_to_paths(sub, [0, 3], halo=0)
    assert _ids(out) == [0, 1, 3]
    # stable across repeated calls
    assert _ids(filter_subgraph_to_paths(sub, [0, 3], halo=0)) == [0, 1, 3]


def test_no_seeds_is_noop():
    sub = _sub([0, 1], [(0, 1)])
    assert filter_subgraph_to_paths(sub, [], halo=1) is sub


def test_empty_subgraph_no_crash():
    out = filter_subgraph_to_paths({"entities": [], "edges": []}, [0], halo=1)
    assert out["entities"] == [] and out["edges"] == []


def test_disconnected_seed_pair_no_error():
    # seeds 0 and 2 are in different components; no path -> keep = seeds (+halo).
    sub = _sub([0, 1, 2, 3], [(0, 1), (2, 3)])
    out = filter_subgraph_to_paths(sub, [0, 2], halo=0)
    assert _ids(out) == [0, 2]


def test_isolated_seed_not_in_adjacency():
    # seed 9 has no incident edge -> must not KeyError on adjacency lookup.
    sub = _sub([9, 0, 1], [(0, 1)])
    out = filter_subgraph_to_paths(sub, [9], halo=1)
    assert _ids(out) == [9]
