import pytest

ray = pytest.importorskip("ray")


def test_pairs_list_to_dataset_roundtrips():
    from goldenmatch.distributed.clustering import pairs_list_to_dataset

    pairs = [(1, 2, 0.9), (2, 3, 0.85), (4, 5, 0.95)]
    ds = pairs_list_to_dataset(pairs)
    rows = list(ds.take_all())
    assert len(rows) == 3
    assert {"id_a", "id_b", "score"} == set(rows[0].keys())
    assert sorted([(r["id_a"], r["id_b"]) for r in rows]) == [(1, 2), (2, 3), (4, 5)]


def test_propagate_one_step_emits_min_labels():
    import ray
    from goldenmatch.distributed.clustering import (
        _propagate_one_step,
        pairs_list_to_dataset,
    )

    pairs_ds = pairs_list_to_dataset([(1, 2, 0.9), (2, 3, 0.85), (5, 6, 0.95)])
    labels_ds = ray.data.from_items(
        [{"id": i, "label": i} for i in [1, 2, 3, 5, 6]]
    )
    new_labels_ds = _propagate_one_step(pairs_ds, labels_ds)
    rows = sorted(new_labels_ds.take_all(), key=lambda r: r["id"])
    label_map = {r["id"]: r["label"] for r in rows}
    assert label_map[1] == 1
    assert label_map[2] == 1
    assert label_map[3] == 2
    assert label_map[5] == 5
    assert label_map[6] == 5


def test_label_propagation_converges_on_simple_graph():
    from goldenmatch.distributed.clustering import (
        label_propagation,
        pairs_list_to_dataset,
    )

    pairs_ds = pairs_list_to_dataset([
        (1, 2, 0.9), (2, 3, 0.85), (5, 6, 0.95),
    ])
    labels_ds, iters = label_propagation(
        pairs_ds, all_ids=[1, 2, 3, 5, 6], convergence_max_iterations=10,
    )
    rows = sorted(labels_ds.take_all(), key=lambda r: r["id"])
    label_map = {r["id"]: r["label"] for r in rows}

    assert label_map[1] == label_map[2] == label_map[3]
    assert label_map[5] == label_map[6]
    assert label_map[1] != label_map[5]
    assert iters < 10


def test_label_propagation_isolated_nodes_keep_own_labels():
    from goldenmatch.distributed.clustering import (
        label_propagation,
        pairs_list_to_dataset,
    )

    pairs_ds = pairs_list_to_dataset([(1, 2, 0.9)])
    labels_ds, _ = label_propagation(
        pairs_ds, all_ids=[1, 2, 99], convergence_max_iterations=10,
    )
    rows = {r["id"]: r["label"] for r in labels_ds.take_all()}
    assert rows[1] == rows[2]
    assert rows[99] == 99


def test_build_clusters_distributed_produces_cluster_assignments():
    from goldenmatch.distributed.clustering import (
        build_clusters_distributed,
        pairs_list_to_dataset,
    )

    pairs_ds = pairs_list_to_dataset([
        (1, 2, 0.9), (2, 3, 0.85), (5, 6, 0.95),
    ])
    clusters_ds = build_clusters_distributed(
        pairs_ds, all_ids=[1, 2, 3, 5, 6],
    )
    rows = sorted(clusters_ds.take_all(), key=lambda r: r["member_id"])
    assert {"member_id", "cluster_id", "cluster_size"} <= set(rows[0].keys())
    by_member = {r["member_id"]: r for r in rows}
    assert by_member[1]["cluster_id"] == by_member[2]["cluster_id"] == by_member[3]["cluster_id"]
    assert by_member[5]["cluster_id"] == by_member[6]["cluster_id"]
    assert by_member[1]["cluster_id"] != by_member[5]["cluster_id"]
    assert by_member[1]["cluster_size"] == 3
    assert by_member[5]["cluster_size"] == 2


def test_materialize_cluster_dict_matches_in_memory_shape():
    from goldenmatch.core.cluster import build_clusters
    from goldenmatch.distributed.clustering import (
        build_clusters_distributed,
        materialize_cluster_dict,
        pairs_list_to_dataset,
    )

    pairs = [(1, 2, 0.9), (2, 3, 0.85), (5, 6, 0.95)]
    in_mem = build_clusters(pairs, all_ids=[1, 2, 3, 5, 6])
    pairs_ds = pairs_list_to_dataset(pairs)
    clusters_ds = build_clusters_distributed(pairs_ds, all_ids=[1, 2, 3, 5, 6])
    distributed = materialize_cluster_dict(clusters_ds, pairs_ds)

    def partitions(cluster_dict):
        return sorted(tuple(sorted(info["members"])) for info in cluster_dict.values())

    assert partitions(in_mem) == partitions(distributed)


def test_materialize_cluster_dict_includes_pair_scores():
    from goldenmatch.distributed.clustering import (
        build_clusters_distributed,
        materialize_cluster_dict,
        pairs_list_to_dataset,
    )

    pairs = [(1, 2, 0.9), (2, 3, 0.85)]
    pairs_ds = pairs_list_to_dataset(pairs)
    clusters_ds = build_clusters_distributed(pairs_ds, all_ids=[1, 2, 3])
    result = materialize_cluster_dict(clusters_ds, pairs_ds)
    assert len(result) == 1
    info = next(iter(result.values()))
    assert info["size"] == 3
    assert "pair_scores" in info
    assert len(info["pair_scores"]) == 2


def test_build_clusters_distributed_falls_back_on_non_convergence(caplog):
    import logging

    from goldenmatch.distributed.clustering import (
        build_clusters_distributed,
        pairs_list_to_dataset,
    )

    pairs = [(1, 2, 0.9), (2, 3, 0.85), (3, 4, 0.8), (4, 5, 0.75)]
    pairs_ds = pairs_list_to_dataset(pairs)
    with caplog.at_level(logging.WARNING):
        clusters_ds = build_clusters_distributed(
            pairs_ds, all_ids=[1, 2, 3, 4, 5],
            convergence_max_iterations=1,
        )
    rows = clusters_ds.take_all()
    cluster_ids = {r["cluster_id"] for r in rows}
    assert len(cluster_ids) == 1
    assert any("fallback" in r.message.lower() for r in caplog.records)
