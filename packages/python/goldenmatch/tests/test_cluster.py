"""Tests for goldenmatch clustering."""


from goldenmatch.core.cluster import UnionFind, build_clusters, split_oversized_cluster


class TestUnionFind:
    """Tests for UnionFind."""

    def test_basic_union(self):
        """Unioning two elements puts them in the same set."""
        uf = UnionFind()
        uf.add(1)
        uf.add(2)
        uf.union(1, 2)
        assert uf.find(1) == uf.find(2)

    def test_singletons(self):
        """Elements not unioned remain in separate sets."""
        uf = UnionFind()
        uf.add(1)
        uf.add(2)
        uf.add(3)
        assert uf.find(1) != uf.find(2)
        assert uf.find(2) != uf.find(3)

    def test_transitive_union(self):
        """Union is transitive: union(1,2) + union(2,3) => 1,2,3 in same set."""
        uf = UnionFind()
        for i in range(1, 4):
            uf.add(i)
        uf.union(1, 2)
        uf.union(2, 3)
        assert uf.find(1) == uf.find(3)

    def test_get_clusters(self):
        """get_clusters returns correct groupings."""
        uf = UnionFind()
        for i in range(1, 6):
            uf.add(i)
        uf.union(1, 2)
        uf.union(3, 4)
        clusters = uf.get_clusters()
        # Should have 3 clusters: {1,2}, {3,4}, {5}
        cluster_sets = [frozenset(c) for c in clusters]
        assert frozenset({1, 2}) in cluster_sets
        assert frozenset({3, 4}) in cluster_sets
        assert frozenset({5}) in cluster_sets
        assert len(clusters) == 3


class TestBuildClusters:
    """Tests for build_clusters."""

    def test_builds_from_pairs(self):
        """Builds clusters from scored pairs."""
        pairs = [(1, 2, 0.95), (2, 3, 0.90)]
        all_ids = [1, 2, 3, 4]
        result = build_clusters(pairs, all_ids)
        # IDs 1,2,3 should be in one cluster; 4 is a singleton
        assert len(result) == 2
        # Find the cluster containing member 1
        cluster_with_1 = [c for c in result.values() if 1 in c["members"]][0]
        # Member ORDER is not a contract (build_clusters keeps whatever order
        # the UF kernel returns -- Python dict-insertion vs native hash order
        # differ; see the v34 note in cluster.py). Assert membership only.
        assert sorted(cluster_with_1["members"]) == [1, 2, 3]
        assert cluster_with_1["size"] == 3
        assert cluster_with_1["oversized"] is False
        # pair_scores should contain the pairs
        assert (1, 2) in cluster_with_1["pair_scores"] or (2, 1) in cluster_with_1["pair_scores"]

    def test_oversized_auto_split(self):
        """Clusters exceeding max_cluster_size are auto-split."""
        pairs = [(1, 2, 0.9), (2, 3, 0.9)]
        all_ids = [1, 2, 3]
        result = build_clusters(pairs, all_ids, max_cluster_size=2)
        # Cluster of 3 was split — no cluster should exceed size 2
        for cluster in result.values():
            assert cluster["size"] <= 2

    def test_no_pairs_all_singletons(self):
        """No pairs means all IDs become singleton clusters."""
        pairs = []
        all_ids = [1, 2, 3]
        result = build_clusters(pairs, all_ids)
        assert len(result) == 3
        for cluster in result.values():
            assert cluster["size"] == 1
            assert cluster["oversized"] is False

    def test_monotonic_cluster_ids(self):
        """Cluster IDs start at 1 and are monotonically increasing."""
        pairs = [(1, 2, 0.9)]
        all_ids = [1, 2, 3]
        result = build_clusters(pairs, all_ids)
        assert sorted(result.keys()) == [1, 2]

    def test_pair_scores_stored(self):
        """Pair scores are stored in the cluster dict."""
        pairs = [(10, 20, 0.85), (20, 30, 0.77)]
        all_ids = [10, 20, 30]
        result = build_clusters(pairs, all_ids)
        cluster = list(result.values())[0]
        assert cluster["pair_scores"][(10, 20)] == 0.85
        assert cluster["pair_scores"][(20, 30)] == 0.77

    def test_members_complete(self):
        """A cluster contains exactly its connected members (order-agnostic:
        build_clusters does not guarantee member order -- native hash order vs
        Python insertion order differ; readers treat members as a set)."""
        pairs = [(5, 3, 0.9), (3, 1, 0.9)]
        all_ids = [5, 3, 1]
        result = build_clusters(pairs, all_ids)
        cluster_with_all = [c for c in result.values() if c["size"] == 3][0]
        assert sorted(cluster_with_all["members"]) == [1, 3, 5]


def test_auto_split_oversized():
    """build_clusters auto-splits oversized clusters."""
    # Chain: 0-1(0.9) - 1-2(0.5) - 2-3(0.8), max_cluster_size=2
    pairs = [(0, 1, 0.9), (1, 2, 0.5), (2, 3, 0.8)]
    clusters = build_clusters(pairs, [0, 1, 2, 3], max_cluster_size=2)
    for cinfo in clusters.values():
        assert cinfo["size"] <= 2


def test_split_recursive_oversized():
    """build_clusters recursively splits clusters exceeding max_cluster_size."""
    pairs = [(0, 1, 0.9), (1, 2, 0.5), (2, 3, 0.8), (3, 4, 0.7), (4, 5, 0.6)]
    all_ids = list(range(6))
    clusters = build_clusters(pairs, all_ids, max_cluster_size=3)
    for cinfo in clusters.values():
        assert cinfo["size"] <= 3


def test_cluster_quality_strong():
    """Clusters with tight edges get quality='strong'."""
    pairs = [(0, 1, 0.95), (1, 2, 0.90), (0, 2, 0.92)]
    clusters = build_clusters(pairs, [0, 1, 2])
    cinfo = list(clusters.values())[0]
    assert cinfo["cluster_quality"] == "strong"


def test_cluster_quality_weak():
    """Clusters with large edge gap get quality='weak'."""
    pairs = [(0, 1, 0.95), (1, 2, 0.85), (0, 2, 0.40)]
    clusters = build_clusters(pairs, [0, 1, 2])
    cinfo = list(clusters.values())[0]
    assert cinfo["cluster_quality"] == "weak"


def test_cluster_quality_split_precedence():
    """Split clusters get quality='split' even if also weak."""
    pairs = [(0, 1, 0.9), (1, 2, 0.3), (2, 3, 0.9)]
    clusters = build_clusters(pairs, [0, 1, 2, 3], max_cluster_size=2)
    for cinfo in clusters.values():
        if cinfo["size"] > 1:
            assert cinfo["cluster_quality"] in ("strong", "split")


# --- split_oversized_cluster unit tests ---


def test_split_single_member():
    """Single member cluster returns unchanged."""
    result = split_oversized_cluster([5], {})
    assert len(result) == 1
    assert result[0]["members"] == [5]


def test_split_no_pairs():
    """No pairs returns original members as one cluster."""
    result = split_oversized_cluster([1, 2], {})
    assert len(result) == 1


def test_split_two_nodes():
    """Two nodes with one edge splits into two singletons."""
    result = split_oversized_cluster([0, 1], {(0, 1): 0.8})
    assert len(result) == 2
    member_sets = [set(c["members"]) for c in result]
    assert {0} in member_sets
    assert {1} in member_sets


def test_split_chain():
    """Chain splits at weakest MST edge."""
    pair_scores = {(0, 1): 0.9, (1, 2): 0.5, (2, 3): 0.8}
    result = split_oversized_cluster([0, 1, 2, 3], pair_scores)
    assert len(result) == 2
    member_sets = [set(c["members"]) for c in result]
    assert {0, 1} in member_sets
    assert {2, 3} in member_sets


def test_split_partitions_pair_scores_correctly():
    """After splitting, each subcluster keeps EVERY original pair whose two
    endpoints landed in that subcluster -- including non-MST edges -- and the
    cut edge appears in no subcluster. Locks the one-pass partition refactor
    (member -> subcluster map) against the old per-subcluster rescan."""
    # Triangle {0,1,2} (dense) + weak bridge 2-3. MST keeps 0-1, 1-2, 2-3;
    # weakest MST edge 2-3 (0.3) is removed -> {0,1,2} and {3}. The non-MST
    # edge 0-2 (0.85) is WITHIN {0,1,2} and must be retained.
    pair_scores = {(0, 1): 0.9, (0, 2): 0.85, (1, 2): 0.88, (2, 3): 0.3}
    result = split_oversized_cluster([0, 1, 2, 3], pair_scores)

    by_members = {frozenset(c["members"]): c for c in result}
    assert set(by_members) == {frozenset({0, 1, 2}), frozenset({3})}

    triangle = by_members[frozenset({0, 1, 2})]
    # All three intra-subcluster edges retained (incl. the non-MST 0-2).
    assert set(triangle["pair_scores"]) == {(0, 1), (0, 2), (1, 2)}
    # The removed cross-cut edge is dropped from every subcluster.
    assert all((2, 3) not in c["pair_scores"] for c in result)
    # bottleneck is the weakest intra-subcluster edge (0,2)=0.85.
    assert triangle["bottleneck_pair"] == (0, 2)
    assert by_members[frozenset({3})]["pair_scores"] == {}


def test_was_split_not_in_output():
    """_was_split sentinel must not leak into final cluster dicts."""
    pairs = [(0, 1, 0.9), (1, 2, 0.5), (2, 3, 0.8)]
    clusters = build_clusters(pairs, [0, 1, 2, 3], max_cluster_size=2)
    for cinfo in clusters.values():
        assert "_was_split" not in cinfo


def test_dense_cluster_split_is_bounded(monkeypatch):
    """A large DENSE cluster has no clean weak bridge, so the single-weakest-edge
    split peels ~1 node per O(edges) pass -- O(nodes*edges), effectively a hang.
    The work budget must bound the loop and leave the blob oversized rather than
    peeling it into arbitrary pieces. Regression for the build_clusters
    dense-cluster split-loop pathology (found greening the ray lane, 2026-05-26)."""
    import time

    # Tiny budget so the guard trips deterministically on the first dense pass.
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET", "1000")
    n = 60
    all_ids = list(range(n))
    # Complete graph: every pair a strong match -> one dense cohesive cluster.
    pairs = [(i, j, 0.95) for i in range(n) for j in range(i + 1, n)]

    t0 = time.time()
    clusters = build_clusters(pairs, all_ids, max_cluster_size=10)
    elapsed = time.time() - t0

    assert elapsed < 5.0, f"auto-split loop not bounded: {elapsed:.1f}s"
    # The dense blob is left oversized (not peeled into arbitrary sub-clusters).
    assert any(c["size"] > 10 for c in clusters.values())
    # Membership is conserved exactly -- no lost or duplicated records.
    members = sorted(m for c in clusters.values() for m in c["members"])
    assert members == all_ids


def test_sparse_oversized_still_splits_under_default_budget():
    """The budget must not change behavior for normal (sparse) weak-bridge
    clusters: a chain well under the default budget still splits fully."""
    # Chain of 8, max_cluster_size=2 -> must split so no cluster exceeds 2.
    pairs = [(i, i + 1, 0.9 - i * 0.05) for i in range(7)]
    clusters = build_clusters(pairs, list(range(8)), max_cluster_size=2)
    assert all(c["size"] <= 2 for c in clusters.values())
    members = sorted(m for c in clusters.values() for m in c["members"])
    assert members == list(range(8))


def test_union_find_nodes_returns_added_members():
    from goldenmatch.core.cluster import UnionFind

    uf = UnionFind()
    uf.add_many([1, 2, 3])
    uf.union(1, 2)
    nodes = sorted(uf.nodes())
    assert nodes == [1, 2, 3]
