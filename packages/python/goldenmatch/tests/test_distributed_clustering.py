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
        pairs_list_to_dataset,
        _propagate_one_step,
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
        pairs_list_to_dataset,
        label_propagation,
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
        pairs_list_to_dataset,
        label_propagation,
    )

    pairs_ds = pairs_list_to_dataset([(1, 2, 0.9)])
    labels_ds, _ = label_propagation(
        pairs_ds, all_ids=[1, 2, 99], convergence_max_iterations=10,
    )
    rows = {r["id"]: r["label"] for r in labels_ds.take_all()}
    assert rows[1] == rows[2]
    assert rows[99] == 99
