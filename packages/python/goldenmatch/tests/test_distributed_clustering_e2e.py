import pytest

ray = pytest.importorskip("ray")


def test_build_clusters_dispatches_to_distributed_on_ray_dataset():
    from goldenmatch.core.cluster import build_clusters
    from goldenmatch.distributed.clustering import pairs_list_to_dataset

    pairs = [(1, 2, 0.9), (2, 3, 0.85), (5, 6, 0.95)]
    pairs_ds = pairs_list_to_dataset(pairs)
    result = build_clusters(pairs_ds, all_ids=[1, 2, 3, 5, 6])

    assert isinstance(result, dict)
    assert len(result) == 2
    for cid, info in result.items():
        assert "members" in info
        assert "size" in info
        assert "pair_scores" in info


def test_build_clusters_dispatches_to_in_memory_on_python_list():
    from goldenmatch.core.cluster import build_clusters

    pairs = [(1, 2, 0.9), (2, 3, 0.85)]
    result = build_clusters(pairs)
    assert isinstance(result, dict)
    assert len(result) == 1
