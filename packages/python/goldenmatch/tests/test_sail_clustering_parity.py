"""S2 gate: connected_components produces a cluster PARTITION identical to a
reference Union-Find on fixtures including a chain, a multi-merge junction, and
a singleton. Self-contained; skips where no Spark Connect client is installed.

Runs in BOTH Spark lanes: `spark_connect` (Apache Spark, blocking) and `sail`
(pysail, advisory cross-check). The two servers plan differently, which is the
point -- these tests failed on each for a DIFFERENT reason, and both causes
turned out to be the same tier defect. See the P2a note below."""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")

# RESOLVED (P2a). These two long-chain WCC tests carried a non-strict xfail on
# real Spark: the iterative join loop exhausted the JVM heap building a broadcast
# join ("Not enough memory to build and broadcast the table to all worker nodes"
# / OutOfMemoryError: Java heap space). They passed under pysail, whose
# in-process server plans differently.
#
# The reading recorded at the time -- that a loop failing on BOTH backends for
# different reasons is usually itself the problem -- held. It was one defect, not
# a small-runner artifact: the loop never truncated plan lineage, so the query
# plan grew every iteration until Spark chose a broadcast it could not afford
# (and until Sail wedged at 12K rows). `_truncate_plan` in goldenmatch/spark/
# clustering.py fixed both.
#
# The marker is REMOVED rather than left to xpass. A non-strict xfail that passes
# reports XPASS and asserts nothing, so these would not have failed if the fix
# regressed -- the exact hole the fix closed would have reopened silently. They
# have passed on three consecutive main runs; they are ordinary tests now.


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
    from goldenmatch.spark.clustering import connected_components

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
    from goldenmatch.spark.clustering import connected_components

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
    from goldenmatch.spark.clustering import connected_components

    ids = list(range(7))
    edges = [(0, 3), (1, 3), (2, 3), (4, 5)]  # canonical a<b
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])

    out = connected_components(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == _reference_partition(ids, edges)


def test_sail_wcc_scale_two_node(spark):
    """Minimal case: edges=[(0,1)] -> one component {0,1}. The fastest-failing
    case for a wrong WCC (it returned two singletons in the blind attempt)."""
    from goldenmatch.spark.clustering import connected_components_scale

    ids = [0, 1]
    edges = [(0, 1)]
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])
    out = connected_components_scale(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == {frozenset({0, 1})}


def test_sail_wcc_scale_partition_parity(spark):
    from goldenmatch.spark.clustering import connected_components_scale

    ids = list(range(7))
    edges = [(0, 1), (1, 2), (3, 4)]
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])
    out = connected_components_scale(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == _reference_partition(ids, edges)


def test_sail_wcc_scale_long_chain(spark):
    """A 30-node chain: pointer-jumping converges in O(log 30) rounds where
    label-prop would need ~30. Must collapse to ONE component."""
    from goldenmatch.spark.clustering import connected_components_scale

    ids = list(range(30))
    edges = [(i, i + 1) for i in range(29)]
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])
    out = connected_components_scale(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == {frozenset(range(30))}


def test_sail_wcc_scale_junction(spark):
    from goldenmatch.spark.clustering import connected_components_scale

    ids = list(range(7))
    edges = [(0, 3), (1, 3), (2, 3), (4, 5)]  # singleton 6
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])
    out = connected_components_scale(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == _reference_partition(ids, edges)


def test_sail_wcc_scale_edge_node_seeding_singleton_heavy(spark):
    """Edge-node seeding (the 100M scale fix): the iteration runs only over the
    edge-endpoint subgraph; the (here vast majority of) singletons are
    re-attached as their own component after the loop. ids 0..9 with a SINGLE
    edge between two high ids (7,8) -> one {7,8} component + 8 singletons
    (incl. ids LOWER than the edge nodes). Asserts the re-attach produces the
    exact full-universe partition, incl. every singleton's cluster_id = self."""
    from goldenmatch.spark.clustering import connected_components_scale

    ids = list(range(10))
    edges = [(7, 8)]
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame(edges, ["a", "b"])
    out = connected_components_scale(pairs_df, ids_df, id_col="__row_id__")
    part = _sail_partition(out)
    assert part == _reference_partition(ids, edges)
    # explicit: 8 singletons + one 2-member component, every id present once.
    assert sum(len(c) for c in part) == 10
    assert frozenset({7, 8}) in part
    assert frozenset({0}) in part and frozenset({9}) in part


def test_sail_wcc_scale_no_edges_all_singletons(spark):
    """Degenerate edge case: zero matches -> empty edge-node seed -> every id is
    re-attached as its own singleton (no rows lost)."""
    from goldenmatch.spark.clustering import connected_components_scale

    ids = list(range(5))
    ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
    pairs_df = spark.createDataFrame([], "a long, b long")
    out = connected_components_scale(pairs_df, ids_df, id_col="__row_id__")
    assert _sail_partition(out) == {frozenset({i}) for i in ids}
