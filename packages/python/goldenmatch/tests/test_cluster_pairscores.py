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
