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
    """With force_label_propagation=True and an unrealistically low iteration
    cap, the convergence fallback fires cleanly. Output stays correct."""
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
            force_label_propagation=True,
        )
    rows = clusters_ds.take_all()
    cluster_ids = {r["cluster_id"] for r in rows}
    assert len(cluster_ids) == 1
    assert any("fallback" in r.message.lower() for r in caplog.records)


def test_build_clusters_distributed_routes_to_scipy_below_threshold(caplog):
    """Default routing: small pair lists go straight to scipy.csgraph."""
    import logging

    from goldenmatch.distributed.clustering import (
        build_clusters_distributed,
        pairs_list_to_dataset,
    )

    pairs = [(1, 2, 0.9), (2, 3, 0.85), (5, 6, 0.95)]
    pairs_ds = pairs_list_to_dataset(pairs)

    with caplog.at_level(logging.INFO):
        clusters_ds = build_clusters_distributed(
            pairs_ds, all_ids=[1, 2, 3, 5, 6],
        )

    rows = clusters_ds.take_all()
    assert len(rows) == 5
    # Routing log must mention scipy.csgraph; label-prop log must NOT fire.
    routing_msgs = [r.message.lower() for r in caplog.records]
    assert any("scipy" in m for m in routing_msgs), routing_msgs
    assert not any("distributed label propagation" in m for m in routing_msgs)


def test_build_clusters_distributed_routes_to_label_prop_when_forced(caplog):
    """force_label_propagation=True overrides the threshold."""
    import logging

    from goldenmatch.distributed.clustering import (
        build_clusters_distributed,
        pairs_list_to_dataset,
    )

    pairs = [(1, 2, 0.9), (2, 3, 0.85), (5, 6, 0.95)]
    pairs_ds = pairs_list_to_dataset(pairs)

    with caplog.at_level(logging.INFO):
        build_clusters_distributed(
            pairs_ds, all_ids=[1, 2, 3, 5, 6],
            force_label_propagation=True,
        )

    routing_msgs = [r.message.lower() for r in caplog.records]
    assert any("distributed label propagation" in m for m in routing_msgs), routing_msgs


def test_build_clusters_distributed_threshold_env_override(monkeypatch, caplog):
    """GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD=0 forces label-prop path."""
    import logging

    from goldenmatch.distributed.clustering import (
        build_clusters_distributed,
        pairs_list_to_dataset,
    )

    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD", "0")
    pairs = [(1, 2, 0.9), (2, 3, 0.85)]
    pairs_ds = pairs_list_to_dataset(pairs)

    with caplog.at_level(logging.INFO):
        build_clusters_distributed(pairs_ds, all_ids=[1, 2, 3])

    routing_msgs = [r.message.lower() for r in caplog.records]
    assert any("distributed label propagation" in m for m in routing_msgs)


# ── Quality-invariant scale validation ──
#
# The distributed paths use DIFFERENT algorithms than the in-memory path
# (scipy.csgraph below the 50M-pair threshold; label propagation or two-phase
# WCC above), and two-phase WCC is partition-sensitive (Phase A is per-partition
# local Union-Find, Phase B reconciles roots across partitions). Nothing
# previously asserted that these produce the SAME connected components as the
# in-memory baseline, or that the result is invariant to Ray partition count.
# A silent divergence means a user scaling row/partition count could get
# different entity merges with nothing failing. These tests close that gap on a
# battery of adversarial graph shapes (chain = label-prop's worst case; bridge,
# clique, multi-component + isolated, all-singletons).

from goldenmatch.core.cluster import build_clusters  # noqa: E402

# (name, pairs, all_ids)
_GRAPH_FIXTURES = [
    ("two_comp_isolated", [(1, 2, 0.9), (2, 3, 0.85), (5, 6, 0.95)], [1, 2, 3, 5, 6, 99]),
    ("bridge", [(1, 2, 0.9), (3, 4, 0.9), (2, 3, 0.8)], [1, 2, 3, 4]),
    ("clique", [(1, 2, 0.9), (2, 3, 0.85), (1, 3, 0.95)], [1, 2, 3]),
    ("chain8", [(i, i + 1, 0.9) for i in range(1, 8)], list(range(1, 9))),
    ("all_singletons", [], [1, 2, 3]),
]
_GRAPHS_WITH_EDGES = [f for f in _GRAPH_FIXTURES if f[1]]


def _normalize(component_member_lists, all_ids):
    """Full partition of ``all_ids`` into sorted member-tuples, singletons
    included. Robust whether the source emits singletons explicitly (the
    distributed paths) or omits them (in-memory ``build_clusters``)."""
    seen: set[int] = set()
    parts = []
    for members in component_member_lists:
        ms = tuple(sorted(int(m) for m in set(members)))
        parts.append(ms)
        seen.update(ms)
    parts.extend((int(i),) for i in all_ids if i not in seen)
    return sorted(parts)


def _inmem_partition(pairs, all_ids):
    d = build_clusters(list(pairs), all_ids=list(all_ids))
    return _normalize([info["members"] for info in d.values()], all_ids)


def _labels_to_partition(labels_ds, all_ids):
    rows = labels_ds.take_all()
    ids = [r["id"] for r in rows]
    # Every member appears exactly once: duplicate member rows (Phase A emits a
    # boundary member per partition) would inflate cluster_size downstream.
    assert len(ids) == len(set(ids)), "duplicate member rows in labels output"
    groups: dict[int, list[int]] = {}
    for r in rows:
        groups.setdefault(r["label"], []).append(r["id"])
    return _normalize(list(groups.values()), all_ids)


def _clusters_ds_to_partition(clusters_ds, all_ids):
    rows = clusters_ds.take_all()
    members = [r["member_id"] for r in rows]
    assert len(members) == len(set(members)), "duplicate member rows in cluster output"
    groups: dict[int, list[int]] = {}
    # cluster_size must equal the actual member count of each cluster.
    size_by_cid: dict[int, int] = {}
    for r in rows:
        groups.setdefault(r["cluster_id"], []).append(r["member_id"])
        size_by_cid[r["cluster_id"]] = r["cluster_size"]
    for cid, mems in groups.items():
        assert size_by_cid[cid] == len(mems), (
            f"cluster {cid}: reported size {size_by_cid[cid]} != {len(mems)} members"
        )
    return _normalize(list(groups.values()), all_ids)


@pytest.mark.parametrize("name,pairs,all_ids", _GRAPH_FIXTURES)
def test_scipy_route_matches_in_memory(name, pairs, all_ids):
    """Default route (scipy.csgraph, below threshold) == in-memory components."""
    from goldenmatch.distributed.clustering import (
        build_clusters_distributed,
        pairs_list_to_dataset,
    )

    clusters_ds = build_clusters_distributed(pairs_list_to_dataset(pairs), all_ids=all_ids)
    assert _clusters_ds_to_partition(clusters_ds, all_ids) == _inmem_partition(pairs, all_ids)


@pytest.mark.parametrize("name,pairs,all_ids", _GRAPH_FIXTURES)
def test_label_propagation_matches_in_memory(name, pairs, all_ids):
    """force_label_propagation route == in-memory components."""
    from goldenmatch.distributed.clustering import (
        build_clusters_distributed,
        pairs_list_to_dataset,
    )

    clusters_ds = build_clusters_distributed(
        pairs_list_to_dataset(pairs), all_ids=all_ids, force_label_propagation=True,
    )
    assert _clusters_ds_to_partition(clusters_ds, all_ids) == _inmem_partition(pairs, all_ids)


@pytest.mark.parametrize("name,pairs,all_ids", _GRAPH_FIXTURES)
def test_two_phase_wcc_matches_in_memory(name, pairs, all_ids):
    """Two-phase WCC (default distributed algorithm) == in-memory components."""
    from goldenmatch.distributed.clustering import pairs_list_to_dataset, two_phase_wcc

    labels_ds = two_phase_wcc(pairs_list_to_dataset(pairs), all_ids)
    assert _labels_to_partition(labels_ds, all_ids) == _inmem_partition(pairs, all_ids)


@pytest.mark.parametrize("name,pairs,all_ids", _GRAPHS_WITH_EDGES)
def test_two_phase_wcc_partition_count_invariant(name, pairs, all_ids):
    """Same edges, different Ray partition counts -> identical components.
    Two-phase WCC is the partition-sensitive path (per-partition Phase A +
    cross-partition Phase B merge), so a partition bug surfaces here. npart=7
    over <=7 pairs forces single-edge / empty partitions (Phase A edge cases)."""
    from goldenmatch.distributed.clustering import pairs_list_to_dataset, two_phase_wcc

    expected = _inmem_partition(pairs, all_ids)
    for npart in (1, 2, 3, 7):
        ds = pairs_list_to_dataset(pairs).repartition(npart)
        got = _labels_to_partition(two_phase_wcc(ds, all_ids), all_ids)
        assert got == expected, f"{name}: npart={npart} diverged: {got} != {expected}"


@pytest.mark.parametrize("name,pairs,all_ids", _GRAPHS_WITH_EDGES)
def test_label_propagation_partition_count_invariant(name, pairs, all_ids):
    """Label propagation must also be invariant to input partition count."""
    from goldenmatch.distributed.clustering import (
        build_clusters_distributed,
        pairs_list_to_dataset,
    )

    expected = _inmem_partition(pairs, all_ids)
    for npart in (1, 2, 5):
        ds = pairs_list_to_dataset(pairs).repartition(npart)
        clusters_ds = build_clusters_distributed(
            ds, all_ids=all_ids, force_label_propagation=True,
        )
        got = _clusters_ds_to_partition(clusters_ds, all_ids)
        assert got == expected, f"{name}: npart={npart} diverged: {got} != {expected}"
