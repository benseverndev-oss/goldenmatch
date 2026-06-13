"""Multi-node determinism gate -- the Stage-D analog for the Sail tier (R2 of
``2026-06-13-sail-tier-past-one-box-roadmap.md``).

The emitted pair SET and the cluster PARTITION must be invariant to the number
of shuffle partitions Sail fans the plan across. This is the prerequisite that
makes an S4 multi-node bench TRUSTWORTHY: a green bench on a
partition-count-sensitive pipeline would be measuring luck, not the engine.

Mirrors the one-box spine's Stage D (determinism across ``target_partitions``
{1, 3, N}); here the knob is ``spark.sql.shuffle.partitions``. Self-contained;
skips where the ``sail`` extra is absent; runs in the ``sail`` CI lane.

Fixture discipline (carried from the spine's Stage D + the S1 score fixture):
every within-block pair scores 1.0 -- a 0.15 margin over the 0.85 threshold --
so NO pair sits within f32-ULP of the cutoff. The gate then measures
determinism, NOT threshold flapping.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pysail")
pytest.importorskip("pyspark")
pytest.importorskip("rapidfuzz")


# Sweep the shuffle-partition count: 1 (single partition) up to more partitions
# than rows, so the plan is forced to fan out and re-coalesce.
_SHUFFLE_PARTITIONS = (1, 3, 8)
_THRESHOLD = 0.85

# block on zip, score last_name; identical within-block strings -> every pair
# scores 1.0 (0.15 over threshold). 3-member / 2-member / singleton.
_ROWS = [
    (0, "Aaaa", "10001"),
    (1, "Aaaa", "10001"),
    (2, "Aaaa", "10001"),
    (3, "Brown", "20002"),
    (4, "Brown", "20002"),
    (5, "Carter", "30003"),
]


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
    frozensets of member ids (singletons included). The correctness oracle:
    independent of shuffle count, scorer, and WCC algorithm."""
    parent = {i: i for i in ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in edges:
        parent[find(a)] = find(b)
    comp: dict[int, set[int]] = {}
    for i in ids:
        comp.setdefault(find(i), set()).add(i)
    return {frozenset(v) for v in comp.values()}


def _partition_of(assignments_df):
    """assignments DataFrame -> set of frozensets of member ids per cluster_id."""
    from collections import defaultdict

    by_cid: dict[int, set[int]] = defaultdict(set)
    for r in assignments_df.collect():
        by_cid[r["cluster_id"]].add(int(r["member_id"]))
    return {frozenset(v) for v in by_cid.values()}


def _pair_set(pairs_df):
    """(a, b, score) DataFrame -> set of canonical (a, b) int pairs."""
    return {(int(r["a"]), int(r["b"])) for r in pairs_df.collect()}


@pytest.mark.parametrize(
    "wcc_fn_name", ["connected_components", "connected_components_scale"]
)
def test_wcc_partition_invariant_to_shuffle_partitions(spark, wcc_fn_name):
    """WCC alone: the cluster partition is identical across shuffle-partition
    counts AND equals the reference Union-Find. Min-label propagation is
    order-independent by construction; this proves Sail's distribution doesn't
    break that for either the S2 (label-prop) or the scale (pointer-jumping)
    algorithm."""
    from goldenmatch.sail import clustering

    fn = getattr(clustering, wcc_fn_name)
    ids = list(range(12))
    # two chains + a star + singletons {4}, {8}, {11}
    edges = [(0, 1), (1, 2), (2, 3), (5, 6), (6, 7), (9, 10)]
    ref = _reference_partition(ids, edges)

    parts = []
    for n in _SHUFFLE_PARTITIONS:
        spark.conf.set("spark.sql.shuffle.partitions", str(n))
        ids_df = spark.createDataFrame([(i,) for i in ids], ["__row_id__"])
        pairs_df = spark.createDataFrame(edges, ["a", "b"])
        parts.append(_partition_of(fn(pairs_df, ids_df, id_col="__row_id__")))

    # determinism: identical across every shuffle-partition count
    assert all(p == parts[0] for p in parts)
    # correctness: that invariant value is the right one
    assert parts[0] == ref


def test_pipeline_pair_set_and_partition_invariant_to_shuffle_partitions(spark):
    """Full S1->S2 path (score -> dedup -> WCC): both the emitted pair SET and
    the cluster PARTITION are invariant to shuffle-partition count. Covers the
    float-reduction determinism the WCC-only test cannot -- the dedup
    ``max(score)`` GROUP BY runs across partitions, the place a non-deterministic
    parallel reduction would surface."""
    from goldenmatch.sail.clustering import connected_components_scale
    from goldenmatch.sail.scoring import score_and_dedup

    pair_sets = []
    partitions = []
    for n in _SHUFFLE_PARTITIONS:
        spark.conf.set("spark.sql.shuffle.partitions", str(n))
        df = spark.createDataFrame(_ROWS, ["__row_id__", "last", "zip"])
        pairs = score_and_dedup(
            df,
            block_col="zip",
            value_col="last",
            id_col="__row_id__",
            scorer_name="jaro_winkler",
            threshold=_THRESHOLD,
        )
        pair_sets.append(_pair_set(pairs))
        ids_df = df.select("__row_id__")
        assignments = connected_components_scale(
            pairs, ids_df, id_col="__row_id__"
        )
        partitions.append(_partition_of(assignments))

    # determinism: identical across every shuffle-partition count
    assert all(ps == pair_sets[0] for ps in pair_sets)
    assert all(pt == partitions[0] for pt in partitions)
    # correctness: the WCC partition matches a reference UF over the EMITTED
    # pairs (no dependence on exact scores -- robust to rapidfuzz versions)
    ids = [r[0] for r in _ROWS]
    assert partitions[0] == _reference_partition(ids, pair_sets[0])
