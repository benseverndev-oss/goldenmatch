"""goldengraph-native binding tests (run in CI via maturin develop + pytest).

The SP1 differentiator through the Python API: same mentions + edges, two
resolution maps. `resolved` merges Apple Inc / Apple, so a 1-hop query from the
Apple entity finds BOTH facts; `exact` keeps them split, so it finds only one.
Plus a Native-path test that reaches the same merged end-state via the
score-core + graph-core kernels.
"""

from goldengraph_native import _native as gg

MENTIONS = [
    ("Apple Inc", "org"),  # 0
    ("Apple", "org"),  # 1
    ("Jobs", "person"),  # 2
    ("iPhone", "product"),  # 3
]
EDGES = [
    (0, "founded_by", 2, "c1"),
    (1, "released", 3, "c2"),
]


def _predicates(view):
    return sorted(e["predicate"] for e in view["edges"])


def test_provided_resolved_one_hop_finds_both_facts():
    # host says mentions 0 and 1 are the same entity (0); 2->1; 3->2
    g = gg.build_graph(MENTIONS, EDGES, {0: 0, 1: 0, 2: 1, 3: 2})
    seeds = g.seeds_by_name("Apple Inc")
    assert seeds == [0]
    view = g.query(seeds, 1)
    assert _predicates(view) == ["founded_by", "released"]  # BOTH facts


def test_provided_exact_one_hop_finds_only_half():
    # Apple stays split: each mention is its own entity
    g = gg.build_graph(MENTIONS, EDGES, {0: 0, 1: 1, 2: 2, 3: 3})
    seeds = g.seeds_by_name("Apple Inc")
    assert seeds == [0]
    view = g.query(seeds, 1)
    # only founded_by Jobs; the `released` fact hangs off the separate "Apple"
    assert _predicates(view) == ["founded_by"]


def test_native_path_merges_apple():
    # ("native", scorer_id, threshold); scorer_id 0 = jaro_winkler
    g = gg.build_graph(MENTIONS, EDGES, ("native", 0, 0.85))
    seeds = g.seeds_by_name("Apple Inc")
    assert seeds == [0]
    view = g.query(seeds, 1)
    # same end-state as the Provided `resolved` map
    assert _predicates(view) == ["founded_by", "released"]
    apple = next(e for e in view["entities"] if e["entity_id"] == 0)
    assert sorted(apple["members"]) == [0, 1]  # Apple Inc + Apple merged
    assert apple["surface_names"] == ["Apple", "Apple Inc"]  # distinct forms, sorted


def test_seeds_by_name_finds_entity_by_any_surface_form():
    # Dogfood-derived: a resolved entity must be findable by ANY name it was
    # mentioned under, not just the canonical the resolver picked. Here three
    # org forms merge; the canonical is the longest ("Apple Computer").
    mentions = [
        ("Apple Inc.", "org"),
        ("Apple", "org"),
        ("Apple Computer", "org"),
    ]
    g = gg.build_graph(mentions, [], ("native", 0, 0.85))
    apple = g.query(g.seeds_by_name("Apple Computer"), 1)["entities"][0]
    assert apple["canonical_name"] == "Apple Computer"  # longest form wins canonical
    # ...but all three surface forms resolve to the same entity id
    assert g.seeds_by_name("Apple Inc.") == [apple["entity_id"]]
    assert g.seeds_by_name("Apple") == [apple["entity_id"]]
    assert g.seeds_by_name("Apple Computer") == [apple["entity_id"]]
    assert g.seeds_by_name("Nonexistent") == []


def test_bad_resolution_raises():
    import pytest

    with pytest.raises(ValueError):
        gg.build_graph(MENTIONS, EDGES, "not-a-valid-resolution")


def test_communities_group_connected_entities():
    # resolved differentiator graph: 0(Apple)->1(Jobs), 0->2(iPhone) all connected
    g = gg.build_graph(MENTIONS, EDGES, {0: 0, 1: 0, 2: 1, 3: 2})
    comms = g.communities()
    assert len(comms) == 1
    assert sorted(comms[0]["members"]) == [0, 1, 2]


# ---- SP4a: PyStore (durable bi-temporal store) ----------------------------

import json


def _ent(local, name, keys, typ="org"):
    return {
        "local_id": local,
        "canonical_name": name,
        "typ": typ,
        "surface_names": [name],
        "record_keys": keys,
    }


def _edge(s, o, valid_from, valid_to, refs, predicate="made"):
    return {
        "subj_local": s,
        "predicate": predicate,
        "obj_local": o,
        "valid_from": valid_from,
        "valid_to": valid_to,
        "source_refs": refs,
    }


def _batch(entities, edges, at):
    return json.dumps({"entities": entities, "edges": edges, "ingested_at": at})


def test_store_round_trip_snapshot_byte_identical():
    s = gg.PyStore()
    s.append(_batch([_ent(0, "Acme", ["a"]), _ent(1, "Beta", ["b"])],
                     [_edge(0, 1, 10, None, ["d1"])], 100))
    s.append(_batch([_ent(0, "Acme", ["a"])], [], 200))
    snap = s.snapshot()
    assert snap == gg.PyStore(snap).snapshot()


def test_store_bitemporal_correction():
    # learn an open edge, then correct it to end at 20 (later tx-time)
    s = gg.PyStore()
    s.append(_batch([_ent(0, "X", ["x"]), _ent(1, "Y", ["y"])],
                    [_edge(0, 1, 10, None, ["d"])], 100))
    s.append(_batch([_ent(0, "X", ["x"]), _ent(1, "Y", ["y"])],
                    [_edge(0, 1, 10, 20, ["d"])], 200))

    before = s.as_of(25, 150)  # only the open version known -> edge present
    assert len(before.query(before.seeds_by_name("X"), 1)["edges"]) == 1
    after = s.as_of(25, 250)   # correction known -> window ended at 20
    assert len(after.query(after.seeds_by_name("X"), 1)["edges"]) == 0


def test_store_merge_time_travel():
    # A,B both -> C; later A and B resolve together (share keys a,b) at tx 200
    s = gg.PyStore()
    s.append(_batch(
        [_ent(0, "Acme", ["a"]), _ent(1, "Beta", ["b"]), _ent(2, "Cee", ["c"], "product")],
        [_edge(0, 2, 0, None, ["e1"]), _edge(1, 2, 0, None, ["e2"])],
        100,
    ))
    s.append(_batch([_ent(0, "Acme", ["a", "b"]), _ent(1, "Cee", ["c"], "product")], [], 200))

    # before the merge: C neighbors A and B -> 2 edges
    before = s.as_of(50, 150)
    assert len(before.query(before.seeds_by_name("Cee"), 1)["edges"]) == 2
    # after: B's edge remaps onto merged A -> collapses to 1 edge
    after = s.as_of(50, 250)
    assert len(after.query(after.seeds_by_name("Cee"), 1)["edges"]) == 1


def test_store_history_names_both_sides_of_merge():
    s = gg.PyStore()
    s.append(_batch([_ent(0, "A", ["a"]), _ent(1, "B", ["b"])], [], 10))
    s.append(_batch([_ent(0, "AB", ["a", "b"])], [], 20))
    ev = s.history(0)
    assert len(ev) == 1 and ev[0]["kind"] == "merge" and ev[0]["kept"] == 0
    assert s.history(1)[0]["absorbed"] == [1]


def test_store_bad_json_raises():
    import pytest

    with pytest.raises(ValueError):
        gg.PyStore("{ not valid json")
    s = gg.PyStore()
    with pytest.raises(ValueError):
        s.append("{ also not valid")
