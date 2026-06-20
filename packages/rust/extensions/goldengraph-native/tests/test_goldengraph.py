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
