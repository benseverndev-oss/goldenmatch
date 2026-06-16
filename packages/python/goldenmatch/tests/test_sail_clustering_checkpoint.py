"""S2 lineage-checkpoint gate (the 100M fix): connected_components_scale with a
parquet ``checkpoint_dir`` yields a cluster PARTITION identical to the
no-checkpoint run -- the lineage barrier is output-invariant, it only resets the
Spark Connect query plan. Skips without the `sail` extra; runs in the `sail`
lane (same convention as the S1/S2 parity tests)."""
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


def _partition(out_df):
    from collections import defaultdict

    by_cid = defaultdict(set)
    for r in out_df.collect():
        by_cid[r["cluster_id"]].add(int(r["member_id"]))
    return {frozenset(v) for v in by_cid.values()}


def test_checkpoint_is_output_invariant(spark, tmp_path):
    """checkpoint_dir=... gives the SAME partition as no-checkpoint (and the
    correct answer): a 6-node chain (the lineage-stressing shape) + a singleton."""
    from goldenmatch.sail.clustering import connected_components_scale

    ids = spark.createDataFrame([(i,) for i in range(7)], ["__row_id__"])
    edges = spark.createDataFrame(
        [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)], ["a", "b"]
    )

    base = _partition(connected_components_scale(edges, ids, id_col="__row_id__"))
    ckpt = _partition(
        connected_components_scale(
            edges,
            ids,
            id_col="__row_id__",
            checkpoint_interval=1,
            checkpoint_dir=str(tmp_path / "ckpt"),
        )
    )

    assert ckpt == base
    assert base == {frozenset({0, 1, 2, 3, 4, 5}), frozenset({6})}


def test_checkpoint_interval_requires_dir(spark):
    """checkpoint_interval>0 with no dir is a loud misconfig, not a silent no-op."""
    from goldenmatch.sail.clustering import connected_components_scale

    ids = spark.createDataFrame([(0,), (1,)], ["__row_id__"])
    edges = spark.createDataFrame([(0, 1)], ["a", "b"])
    with pytest.raises(ValueError):
        connected_components_scale(
            edges, ids, id_col="__row_id__", checkpoint_interval=1
        )


def test_label_prop_checkpoint_is_output_invariant(spark, tmp_path):
    """label-prop ``connected_components`` with a checkpoint_dir gives the SAME
    partition as no-checkpoint (and the correct answer). The label-prop variant
    re-joins ``labels`` each round just like the scale path, so it needs the same
    lineage barrier at scale -- the gap the GKE real-cluster run surfaced."""
    from goldenmatch.sail.clustering import connected_components

    ids = spark.createDataFrame([(i,) for i in range(7)], ["__row_id__"])
    edges = spark.createDataFrame(
        [(0, 1), (1, 2), (2, 3), (3, 4), (4, 5)], ["a", "b"]
    )

    base = _partition(connected_components(edges, ids, id_col="__row_id__"))
    ckpt = _partition(
        connected_components(
            edges,
            ids,
            id_col="__row_id__",
            checkpoint_interval=1,
            checkpoint_dir=str(tmp_path / "ckpt_lp"),
        )
    )

    assert ckpt == base
    assert base == {frozenset({0, 1, 2, 3, 4, 5}), frozenset({6})}


def test_label_prop_checkpoint_interval_requires_dir(spark):
    """connected_components: checkpoint_interval>0 with no dir is a loud misconfig."""
    from goldenmatch.sail.clustering import connected_components

    ids = spark.createDataFrame([(0,), (1,)], ["__row_id__"])
    edges = spark.createDataFrame([(0, 1)], ["a", "b"])
    with pytest.raises(ValueError):
        connected_components(edges, ids, id_col="__row_id__", checkpoint_interval=1)
