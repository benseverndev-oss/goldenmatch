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


def test_from_frames_equals_from_pairs():
    # from_frames(assignments, raw pairs) must be BYTE-IDENTICAL to
    # from_pairs(raw pairs, clusters) for every cid -- incl. singletons and
    # auto-split sub-clusters. Native is irrelevant (off-native build is fine).
    from goldenmatch.core.cluster import build_clusters, cluster_dict_to_frames

    # Adversarial pair set:
    #  - singleton (id 9, no edge) -> appears only as an assignments row
    #  - multi-member cluster (1,2,3)
    #  - duplicate canonical pair (4,5) with DIFFERENT scores -> locks last-wins
    #  - a dense star that build_clusters will auto-split when oversized
    pairs = [
        (1, 2, 0.91),
        (2, 3, 0.88),
        (4, 5, 0.40),   # first write
        (4, 5, 0.95),   # last-wins -> 0.95
        (5, 6, 0.82),
        # dense star around 10 to trigger oversize+split
        (10, 11, 0.9), (10, 12, 0.9), (10, 13, 0.9), (10, 14, 0.9),
        (10, 15, 0.9), (10, 16, 0.9), (11, 12, 0.5), (13, 14, 0.5),
        (15, 16, 0.5),
    ]
    # ensure a true singleton exists in the assignments frame
    clusters = build_clusters(pairs, auto_split=True)
    # inject a singleton cluster (no pairs) so assignments carries a lone member
    next_cid = (max(clusters) + 1) if clusters else 0
    clusters[next_cid] = {
        "members": [9], "size": 1, "oversized": False,
        "pair_scores": {}, "confidence": 0.0, "bottleneck_pair": None,
        "cluster_quality": "strong",
    }
    frames = cluster_dict_to_frames(clusters)

    v_pairs = ClusterPairScores.from_pairs(pairs, clusters)
    v_frames = ClusterPairScores.from_frames(frames.assignments, pairs)
    for cid in clusters:
        assert v_frames.for_cluster(cid) == v_pairs.for_cluster(cid)


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
