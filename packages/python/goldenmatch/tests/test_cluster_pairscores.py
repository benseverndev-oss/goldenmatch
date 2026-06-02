from goldenmatch.core.cluster_pairscores import ClusterPairScores


def _clusters():
    return {
        1: {"members": [0], "size": 1, "pair_scores": {}},
        2: {"members": [1, 2], "size": 2, "pair_scores": {(1, 2): 0.9}},
        3: {"members": [3, 4, 5], "size": 3,
            "pair_scores": {(3, 4): 0.8, (4, 5): 0.7, (3, 5): 0.6}},
    }


def test_for_cluster_matches_dict_exactly():
    clusters = _clusters()
    view = ClusterPairScores.from_cluster_dict(clusters)
    for cid, info in clusters.items():
        assert view.for_cluster(cid) == info["pair_scores"]


def test_for_cluster_missing_or_singleton_is_empty():
    view = ClusterPairScores.from_cluster_dict(_clusters())
    assert view.for_cluster(1) == {}
    assert view.for_cluster(999) == {}


def test_iter_clusters_yields_pairs_in_row_order():
    view = ClusterPairScores.from_cluster_dict(_clusters())
    got = {cid: list(pairs) for cid, pairs in view.iter_clusters()}
    assert got[2] == [(1, 2, 0.9)]
    assert got[3] == [(3, 4, 0.8), (4, 5, 0.7), (3, 5, 0.6)]
    assert 1 not in got  # singleton contributes no rows


def test_score_for_bottleneck_lookup():
    view = ClusterPairScores.from_cluster_dict(_clusters())
    assert view.score_for(3, 5, 3) == 0.6   # canonical (min,max) regardless of arg order
    assert view.score_for(3, 9, 9) is None


def test_from_pairs_reproduces_cluster_pair_scores():
    # from_pairs(raw pairs, clusters) == each cluster's pair_scores (the SP4 path).
    clusters = _clusters()
    pairs = [(a, b, s) for c in clusters.values() for (a, b), s in c["pair_scores"].items()]
    view = ClusterPairScores.from_pairs(pairs, clusters)
    for cid, info in clusters.items():
        assert view.for_cluster(cid) == info["pair_scores"]


def test_from_pairs_last_wins_and_excludes_cross_cluster():
    clusters = {
        1: {"members": [0, 1], "size": 2, "pair_scores": {}},
        2: {"members": [2, 3], "size": 2, "pair_scores": {}},
    }
    pairs = [
        (0, 1, 0.5), (0, 1, 0.9),   # last-wins -> 0.9
        (2, 3, 0.7),
        (1, 2, 0.4),                # cross-cluster (1 in c1, 2 in c2) -> excluded
    ]
    view = ClusterPairScores.from_pairs(pairs, clusters)
    assert view.for_cluster(1) == {(0, 1): 0.9}
    assert view.for_cluster(2) == {(2, 3): 0.7}
