import demo.narrative as nv   # run from the er-kg-bench/ dir

# index -> (mention, entity_id) for a tiny IBM-only fixture
RECS = {
    0: ("IBM", "Q37156"),
    1: ("International Business Machines", "Q37156"),
    2: ("Big Blue", "Q37156"),
}
MENTIONS = {i: m for i, (m, _) in RECS.items()}
EIDS = {i: e for i, (_, e) in RECS.items()}

def test_complete_partition_adds_singletons():
    parts = nv.complete_partition([[0, 1]], [0, 1, 2])
    assert sorted(map(sorted, parts)) == [[0, 1], [2]]

def test_under_merge_answer_fragmented():
    before = [[0], [1], [2]]
    ans = nv.under_merge_answer(before, MENTIONS, EIDS, "Q37156", query="IBM")
    assert ans["distinct_nodes"] == 3
    assert ans["names_reachable"] == ["IBM"]
    assert ans["complete"] is False

def test_under_merge_answer_resolved():
    after = [[0, 1, 2]]
    ans = nv.under_merge_answer(after, MENTIONS, EIDS, "Q37156", query="IBM")
    assert ans["distinct_nodes"] == 1
    assert set(ans["names_reachable"]) == {"IBM", "International Business Machines", "Big Blue"}
    assert ans["complete"] is True

def test_pair_merged_detects_over_merge():
    clusters = [[10, 11]]
    eids = {10: "Q230", 11: "Q1428"}
    assert nv.pair_merged(clusters, eids, "Q230", "Q1428") is True
    assert nv.pair_merged([[10], [11]], eids, "Q230", "Q1428") is False

def test_render_demo_md_is_deterministic():
    before = [[0], [1], [2]]
    after = [[0, 1, 2]]
    md1 = nv.render_demo_md(MENTIONS, EIDS, "Q37156", "IBM", before, after)
    md2 = nv.render_demo_md(MENTIONS, EIDS, "Q37156", "IBM", before, after)
    assert md1 == md2
    assert "International Business Machines" in md1
    assert "F1 0.066" in md1   # cites the harness exact-family number (scaled corpus)
