"""S2 gate: Sail connected_components produces a cluster PARTITION identical
to a reference Union-Find on fixtures including a chain, a multi-merge
junction, and a singleton. Self-contained; skips where the sail extra is
absent; runs in the `sail` lane."""
from __future__ import annotations

import pytest

pytest.importorskip("pysail")
pytest.importorskip("pyspark")


@pytest.fixture(scope="module")
def spark():
    from pysail.spark import SparkConnectServer
    from pyspark.sql import SparkSession

    server = SparkConnectServer()
    server.start()
    _, port = server.listening_address
    sess = SparkSession.builder.remote(f"sc://localhost:{port}").getOrCreate()
    yield sess
    sess.stop()
    server.stop()


def _reference_partition(ids, edges):
    """Canonical connected components via plain Union-Find -> set of
    frozensets of member ids (singletons included)."""
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    comp = {}
    for i in ids:
        comp.setdefault(find(i), set()).add(i)
    return {frozenset(v) for v in comp.values()}


def _sail_partition(out_df):
    """assignments DataFrame -> set of frozensets of member ids per cluster_id."""
    from collections import defaultdict

    by_cid = defaultdict(set)
    for r in out_df.collect():
        by_cid[r["cluster_id"]].add(int(r["member_id"]))
    return {frozenset(v) for v in by_cid.values()}


def test_sail_wcc_partition_parity(spark):
    from goldenmatch.sail.clustering import connected_components

    # ids 0..6: chain {0-1-2}, pair {3-4}, singletons {5},{6}.
    ids = list(range(7))
    edges = [(0, 1), (1, 2), (3, 4)]  # canonical a<b
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])

    out = connected_components(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == _reference_partition(ids, edges)


def test_sail_wcc_deep_chain_converges(spark):
    """A longer chain 0-1-2-...-9 must collapse to ONE component (label-prop
    across many hops -- the correctness analog of the chain concern)."""
    from goldenmatch.sail.clustering import connected_components

    ids = list(range(10))
    edges = [(i, i + 1) for i in range(9)]
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])

    out = connected_components(pairs_df, ids_df, id_col="__row_id__")
    part = _sail_partition(out)
    assert part == {frozenset(range(10))}


def test_sail_wcc_junction_multimerge(spark):
    """The spec-named multi-merge archetype: branches 0,1,2 all merge at a
    junction node 3 (min-propagation arrives from multiple neighbors in one
    round), a separate pair {4,5}, and a singleton {6}. Stresses the case
    most likely to surface a subtle min-propagation bug."""
    from goldenmatch.sail.clustering import connected_components

    ids = list(range(7))
    edges = [(0, 3), (1, 3), (2, 3), (4, 5)]  # canonical a<b
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])

    out = connected_components(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == _reference_partition(ids, edges)


def test_sail_wcc_scale_two_node(spark):
    """Minimal case: edges=[(0,1)] -> one component {0,1}. The fastest-failing
    case for a wrong WCC (it returned two singletons in the blind attempt)."""
    from goldenmatch.sail.clustering import connected_components_scale

    ids = [0, 1]
    edges = [(0, 1)]
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])
    out = connected_components_scale(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == {frozenset({0, 1})}


def test_sail_wcc_scale_partition_parity(spark):
    from goldenmatch.sail.clustering import connected_components_scale

    ids = list(range(7))
    edges = [(0, 1), (1, 2), (3, 4)]
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])
    out = connected_components_scale(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == _reference_partition(ids, edges)


def test_sail_wcc_scale_long_chain(spark):
    """A 30-node chain: pointer-jumping converges in O(log 30) rounds where
    label-prop would need ~30. Must collapse to ONE component."""
    from goldenmatch.sail.clustering import connected_components_scale

    ids = list(range(30))
    edges = [(i, i + 1) for i in range(29)]
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])
    out = connected_components_scale(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == {frozenset(range(30))}


def test_sail_wcc_scale_junction(spark):
    from goldenmatch.sail.clustering import connected_components_scale

    ids = list(range(7))
    edges = [(0, 3), (1, 3), (2, 3), (4, 5)]  # singleton 6
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])
    out = connected_components_scale(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == _reference_partition(ids, edges)
