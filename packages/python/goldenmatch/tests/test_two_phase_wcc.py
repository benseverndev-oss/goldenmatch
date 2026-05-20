"""Tests for Two-Phase WCC (Tasks 1-5 of Phase 5.5)."""
import pytest

ray = pytest.importorskip("ray")


def test_phase_a_local_cc_emits_member_root_pairs():
    """Phase A produces (member_id, local_root) rows; all members in a
    component share the same local_root within a partition."""
    import pyarrow as pa
    from goldenmatch.distributed.clustering import _phase_a_local_cc

    # Partition input: 5 pairs forming 2 components: {1,2,3} and {5,6}.
    batch = pa.Table.from_pylist([
        {"id_a": 1, "id_b": 2, "score": 0.9},
        {"id_a": 2, "id_b": 3, "score": 0.85},
        {"id_a": 5, "id_b": 6, "score": 0.95},
    ])
    out = _phase_a_local_cc(batch)
    rows = out.to_pylist()
    by_member = {r["member_id"]: r["local_root"] for r in rows}
    # All 5 members present
    assert set(by_member.keys()) == {1, 2, 3, 5, 6}
    # {1,2,3} share a root
    assert by_member[1] == by_member[2] == by_member[3]
    # {5,6} share a root
    assert by_member[5] == by_member[6]
    # Components are distinct
    assert by_member[1] != by_member[5]


def test_emit_boundary_pairs_filters_to_cross_partition():
    """Boundary edges are pairs whose two endpoints have DIFFERENT
    local_roots in the global member_to_local_root map."""
    import pyarrow as pa
    from goldenmatch.distributed.clustering import _emit_boundary_pairs

    pairs_batch = pa.Table.from_pylist([
        {"id_a": 1, "id_b": 2, "score": 0.9},   # same root -> not boundary
        {"id_a": 3, "id_b": 4, "score": 0.85},  # different roots -> boundary
        {"id_a": 5, "id_b": 6, "score": 0.8},   # same root -> not boundary
    ])
    member_to_root = {1: 1, 2: 1, 3: 3, 4: 4, 5: 5, 6: 5}

    out = _emit_boundary_pairs(pairs_batch, member_to_root)
    rows = out.to_pylist()
    assert len(rows) == 1
    assert rows[0]["root_a"] == 3 and rows[0]["root_b"] == 4


def test_phase_b_merges_super_graph_correctly():
    """Phase B: given local roots + boundary pairs, every member maps
    to the same global root within its true component."""
    import polars as pl
    from goldenmatch.distributed.clustering import (
        _phase_b_merge_boundaries,
        pairs_list_to_dataset,
    )

    local_components_pl = pl.DataFrame({
        "member_id": [1, 2, 3, 4, 5, 6],
        "local_root": [1, 1, 1, 4, 4, 6],
    })
    pairs_ds = pairs_list_to_dataset([(2, 4, 0.9)])  # bridges {1,2,3} and {4,5}

    out_pl = _phase_b_merge_boundaries(local_components_pl, pairs_ds)

    # Members 1-5 should share one global root; member 6 keeps its own.
    by_member = dict(zip(
        out_pl["member_id"].to_list(),
        out_pl["global_root"].to_list(),
    ))
    assert by_member[1] == by_member[2] == by_member[3]
    assert by_member[4] == by_member[5]
    assert by_member[1] == by_member[4]
    assert by_member[6] != by_member[1]


def test_phase_b_merges_super_graph_via_polars():
    """Phase B accepts a Polars frame for local_components and produces
    the same global root remap as the in-memory dict path."""
    import polars as pl
    from goldenmatch.distributed.clustering import (
        _phase_b_merge_boundaries,
        pairs_list_to_dataset,
    )

    # Two partitions: {1,2,3} on partition A (root=1), {4,5} on B (root=4).
    # Pair (3,4) is a boundary edge. After Phase B all 5 share one global root.
    local_components_pl = pl.DataFrame({
        "member_id": [1, 2, 3, 4, 5],
        "local_root": [1, 1, 1, 4, 4],
    })
    pairs_ds = pairs_list_to_dataset([(3, 4, 0.9)])

    out_pl = _phase_b_merge_boundaries(local_components_pl, pairs_ds)
    # All 5 members map to the same global root
    roots = set(out_pl["global_root"].to_list())
    assert len(roots) == 1


def test_two_phase_wcc_partition_structure_matches_in_memory():
    """Two-Phase WCC must produce the same component partitioning as
    the single-node Union-Find in core.cluster.build_clusters."""
    from goldenmatch.core.cluster import build_clusters
    from goldenmatch.distributed.clustering import (
        pairs_list_to_dataset,
        two_phase_wcc,
    )

    pairs = [(1, 2, 0.9), (2, 3, 0.85), (5, 6, 0.95)]
    in_mem = build_clusters(pairs, all_ids=[1, 2, 3, 5, 6])

    pairs_ds = pairs_list_to_dataset(pairs)
    labels_ds = two_phase_wcc(pairs_ds, all_ids=[1, 2, 3, 5, 6])
    label_map = {r["id"]: r["label"] for r in labels_ds.take_all()}

    # Group by label -> compare to in_mem's partition structure.
    by_label: dict[int, set[int]] = {}
    for member, label in label_map.items():
        by_label.setdefault(label, set()).add(member)
    two_phase_partitions = sorted(tuple(sorted(s)) for s in by_label.values())

    in_mem_partitions = sorted(
        tuple(sorted(info["members"])) for info in in_mem.values()
    )
    assert two_phase_partitions == in_mem_partitions


def test_two_phase_wcc_isolated_nodes_keep_own_labels():
    from goldenmatch.distributed.clustering import (
        pairs_list_to_dataset,
        two_phase_wcc,
    )

    pairs_ds = pairs_list_to_dataset([(1, 2, 0.9)])
    labels_ds = two_phase_wcc(pairs_ds, all_ids=[1, 2, 99])
    label_map = {r["id"]: r["label"] for r in labels_ds.take_all()}

    assert label_map[1] == label_map[2]
    # 99 is isolated -> labels itself.
    assert label_map[99] == 99


def test_two_phase_wcc_handles_chains_correctly():
    """Adversarial chain: 100 chains of 10 nodes each = 900 edges,
    longest path = 10. Two-Phase WCC must produce 100 components,
    each containing exactly 10 members."""
    from goldenmatch.distributed.clustering import (
        pairs_list_to_dataset,
        two_phase_wcc,
    )

    pairs = []
    all_ids = []
    for chain_idx in range(100):
        base = chain_idx * 100
        chain_nodes = list(range(base, base + 10))
        all_ids.extend(chain_nodes)
        for i in range(len(chain_nodes) - 1):
            pairs.append((chain_nodes[i], chain_nodes[i + 1], 0.9))

    pairs_ds = pairs_list_to_dataset(pairs)
    labels_ds = two_phase_wcc(pairs_ds, all_ids=all_ids)
    label_map = {r["id"]: r["label"] for r in labels_ds.take_all()}

    by_label: dict[int, set[int]] = {}
    for member, label in label_map.items():
        by_label.setdefault(label, set()).add(member)

    assert len(by_label) == 100
    for members in by_label.values():
        assert len(members) == 10


def test_build_clusters_distributed_uses_two_phase_by_default(monkeypatch, caplog):
    """Default WCC algorithm is two_phase."""
    import logging

    from goldenmatch.distributed.clustering import (
        build_clusters_distributed,
        pairs_list_to_dataset,
    )

    monkeypatch.delenv("GOLDENMATCH_DISTRIBUTED_WCC", raising=False)
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD", "0")

    pairs = [(1, 2, 0.9), (2, 3, 0.85), (5, 6, 0.95)]
    pairs_ds = pairs_list_to_dataset(pairs)

    with caplog.at_level(logging.INFO):
        build_clusters_distributed(pairs_ds, all_ids=[1, 2, 3, 5, 6])

    msgs = [r.message.lower() for r in caplog.records]
    assert any("two_phase" in m for m in msgs), msgs


def test_build_clusters_distributed_routes_to_label_prop_via_env(monkeypatch, caplog):
    """GOLDENMATCH_DISTRIBUTED_WCC=label_propagation routes to label prop."""
    import logging

    from goldenmatch.distributed.clustering import (
        build_clusters_distributed,
        pairs_list_to_dataset,
    )

    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_WCC", "label_propagation")
    monkeypatch.setenv("GOLDENMATCH_DISTRIBUTED_CLUSTERING_THRESHOLD", "0")

    pairs = [(1, 2, 0.9), (2, 3, 0.85), (5, 6, 0.95)]
    pairs_ds = pairs_list_to_dataset(pairs)

    with caplog.at_level(logging.INFO):
        build_clusters_distributed(pairs_ds, all_ids=[1, 2, 3, 5, 6])

    msgs = [r.message.lower() for r in caplog.records]
    assert any("label propagation" in m for m in msgs), msgs


def test_two_phase_wcc_does_not_capture_local_components_in_udf():
    """Regression: Phase B's _emit_boundary_pairs map_batches must not
    serialize ``local_components`` into the UDF closure. With closure
    capture, the UDF size scales linearly with len(local_components) and
    Ray warns + kills the job above ~250 MiB.

    We check the property structurally (does the map_batches UDF carry
    the dict as a free var?) rather than running at 5M scale, so this
    test stays in the fast lane. Closes the runner-OOM gap that the
    Phase 5.5 bench surfaced (run 26159448413, exit 143 at 477 MiB UDF).
    """
    import inspect

    from goldenmatch.distributed import clustering

    src = inspect.getsource(clustering._phase_b_merge_boundaries)
    # The lambda capture pattern from the original implementation was
    # `lambda b: _emit_boundary_pairs(b, local_components)`. The fix
    # routes local_components through ray.put + fn_kwargs instead.
    assert "lambda b: _emit_boundary_pairs(b, local_components)" not in src, (
        "Phase B closes over local_components again -- this is the closure"
        " blowup that killed run 26159448413. Use ray.put + fn_kwargs."
    )
    assert "ray.put(local_components)" in src, (
        "Phase B should ray.put(local_components) so workers share one copy"
        " from the object store instead of receiving a copy per task."
    )


def test_emit_boundary_pairs_accepts_polars_frame():
    """The columnar refactor path: pass a Polars frame as the lookup."""
    import polars as pl
    import pyarrow as pa
    from goldenmatch.distributed.clustering import _emit_boundary_pairs

    pairs_batch = pa.Table.from_pylist([
        {"id_a": 1, "id_b": 2, "score": 0.9},   # same root -> drop
        {"id_a": 3, "id_b": 4, "score": 0.85},  # different roots -> keep
        {"id_a": 5, "id_b": 6, "score": 0.8},   # same root -> drop
    ])
    roots_pl = pl.DataFrame({
        "member_id": [1, 2, 3, 4, 5, 6],
        "local_root": [1, 1, 3, 4, 5, 5],
    })

    out = _emit_boundary_pairs(pairs_batch, roots_pl)
    rows = out.to_pylist()
    assert len(rows) == 1
    assert rows[0]["root_a"] == 3 and rows[0]["root_b"] == 4
