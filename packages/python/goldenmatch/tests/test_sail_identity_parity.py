"""S5 gate: identity-on-Sail (distributed create + edge-emit) parity vs the
one-box ``resolve_clusters`` create path on a fresh store.

Two test tiers:
  * PURE-HELPER unit tests (``record_id_for_row`` / ``entity_id_for_members``)
    need no Sail server -- they run anywhere (incl. the normal python lane).
  * SERVER tests (the builders, the 3-part parity gate, determinism) use an
    in-process Sail Spark Connect server; the ``spark`` fixture ``importorskip``s
    ``pysail``/``pyspark`` so they SKIP without the [sail] extra and run in the
    `sail` CI lane.

Parity gate (entity-id-INDEPENDENT, since one-box mints UUIDv7 while S5 mints
deterministic ``ent:h1:`` ids): (1) ``same_as`` edge SET, (2) record->entity
PARTITION equivalence, (3) node/edge counts. Spec:
docs/superpowers/specs/2026-06-10-sail-tier-stage-s5-identity-design.md.
"""
from __future__ import annotations

from collections import defaultdict

import pytest
from goldenmatch.sail.identity import entity_id_for_members, record_id_for_row

# --------------------------------------------------------------------------
# Tier 1: pure-helper unit tests (no Sail server needed).
# --------------------------------------------------------------------------


def test_record_id_pk_path():
    # PK present -> "{source}:{pk}" (mirrors one-box _record_id_candidates).
    assert record_id_for_row({"id": 42, "name": "x"}, "people", "id") == "people:42"


def test_record_id_h1_path_matches_one_box():
    # No PK -> "{source}:h1:{fingerprint[:12]}", byte-identical to the one-box
    # primary id (parity by construction: same record_fingerprint call).
    from goldenmatch.core._hashing import record_fingerprint
    from goldenmatch.identity.fingerprint_batch import _canonical_payload

    payload = {"first_name": "Jon", "email": "jon@x.com"}
    expected = f"dataframe:h1:{record_fingerprint(_canonical_payload(payload))[:12]}"
    assert record_id_for_row(payload, "dataframe", None) == expected


def test_entity_id_order_independent():
    a = entity_id_for_members(["people:1", "people:2", "people:3"])
    b = entity_id_for_members(["people:3", "people:1", "people:2"])
    assert a == b
    assert a.startswith("ent:h1:")


def test_entity_id_distinct_for_distinct_members():
    a = entity_id_for_members(["people:1", "people:2"])
    b = entity_id_for_members(["people:1", "people:3"])
    assert a != b


# --------------------------------------------------------------------------
# Tier 2: server-backed tests (in-process Sail Spark Connect server).
# --------------------------------------------------------------------------


@pytest.fixture(scope="module")
def spark():
    pytest.importorskip("pysail")
    pytest.importorskip("pyspark")
    from pysail.spark import SparkConnectServer
    from pyspark.sql import SparkSession

    server = SparkConnectServer()
    server.start()
    _, port = server.listening_address
    sess = SparkSession.builder.remote(f"sc://localhost:{port}").getOrCreate()
    yield sess
    sess.stop()
    server.stop()


def test_derive_record_ids_pk(spark):
    from goldenmatch.sail.identity import derive_record_ids

    df = spark.createDataFrame(
        [(0, "people", 10, "Jon"), (1, "people", 11, "Marg")],
        ["__row_id__", "__source__", "pk", "first_name"],
    )
    out = {
        r["__row_id__"]: r["record_id"]
        for r in derive_record_ids(df, source_pk_col="pk").collect()
    }
    assert out == {0: "people:10", 1: "people:11"}


def test_mint_entity_ids(spark):
    from goldenmatch.sail.identity import entity_id_for_members, mint_entity_ids

    rows = [(0, "people:10"), (0, "people:11"), (5, "people:15")]
    df = spark.createDataFrame(rows, ["cluster_id", "record_id"])
    got = {r["cluster_id"]: r["entity_id"] for r in mint_entity_ids(df).collect()}
    assert got[0] == entity_id_for_members(["people:10", "people:11"])
    assert got[5] == entity_id_for_members(["people:15"])


def _run_meta():
    return {
        "run_name": "r1",
        "dataset": None,
        "recorded_at": "2026-06-10T00:00:00",
        "matchkey_name": "mk",
    }


def test_same_as_edges_set(spark):
    from goldenmatch.sail.identity import build_same_as_edges

    pairs = spark.createDataFrame([(0, 1, 0.97), (2, 3, 0.91)], ["a", "b", "score"])
    assignments = spark.createDataFrame(
        [(0, 0), (0, 1), (2, 2), (2, 3)], ["cluster_id", "member_id"]
    )
    recid = spark.createDataFrame(
        [(0, "p:0"), (1, "p:1"), (2, "p:2"), (3, "p:3")], ["member_id", "record_id"]
    )
    entity_ids = spark.createDataFrame(
        [(0, "ent:A"), (2, "ent:B")], ["cluster_id", "entity_id"]
    )
    edges = build_same_as_edges(
        pairs, assignments, recid, entity_ids, run_meta=_run_meta()
    )
    collected = edges.collect()
    got = {(r["record_a_id"], r["record_b_id"], r["entity_id"]) for r in collected}
    assert got == {("p:0", "p:1", "ent:A"), ("p:2", "p:3", "ent:B")}
    assert all(r["kind"] == "same_as" for r in collected)


def test_nodes_include_singletons_and_records(spark):
    from goldenmatch.sail.identity import build_identity_nodes, build_source_records

    assignments = spark.createDataFrame(
        [(0, 0), (0, 1), (5, 5)], ["cluster_id", "member_id"]
    )
    recid = spark.createDataFrame(
        [(0, "p:0"), (1, "p:1"), (5, "p:5")], ["member_id", "record_id"]
    )
    entity_ids = spark.createDataFrame(
        [(0, "ent:A"), (5, "ent:S")], ["cluster_id", "entity_id"]
    )
    # build_golden emits cluster 0 only (multi-member); cluster 5 is a singleton.
    golden = spark.createDataFrame([(0, "Jonathan")], ["cluster_id", "first_name"])

    nodes = build_identity_nodes(entity_ids, golden, run_meta=_run_meta())
    node_ids = {r["entity_id"] for r in nodes.collect()}
    assert node_ids == {"ent:A", "ent:S"}  # singleton node MUST exist

    records = build_source_records(assignments, recid, entity_ids, run_meta=_run_meta())
    rec_to_ent = {r["record_id"]: r["entity_id"] for r in records.collect()}
    assert rec_to_ent == {"p:0": "ent:A", "p:1": "ent:A", "p:5": "ent:S"}


# --------------------------------------------------------------------------
# The 3-part parity gate + determinism.
# --------------------------------------------------------------------------


def _fixture():
    # rows: (__row_id__, __source__, pk, name). Clusters by design:
    #   chain 0-1-2 (a-b 0.96, b-c 0.95), junction-multimerge 5-6,6-7 (two pairs
    #   share member 6), singleton 9.
    rows = [
        (0, "p", 100, "Ann"), (1, "p", 101, "Anne"), (2, "p", 102, "Annie"),
        (5, "p", 105, "Bob"), (6, "p", 106, "Bobby"), (7, "p", 107, "Robert"),
        (9, "p", 109, "Zed"),
    ]
    pairs = [(0, 1, 0.96), (1, 2, 0.95), (5, 6, 0.93), (6, 7, 0.92)]
    # assignments via union-find over pairs (cluster_id = min member id).
    assignments = [(0, 0), (0, 1), (0, 2), (5, 5), (5, 6), (5, 7), (9, 9)]
    return rows, pairs, assignments


def _partition_sig(rec_to_ent):
    # Entity-id-independent signature: frozenset of frozensets of record_ids.
    groups = defaultdict(set)
    for rid, eid in rec_to_ent.items():
        groups[eid].add(rid)
    return frozenset(frozenset(g) for g in groups.values())


def _one_box_graph(rows, pairs, assignments, db_path):
    """Run the one-box resolver on a fresh SQLite store; return
    (canonical edge_set, record->entity partition signature)."""
    import polars as pl
    from goldenmatch.identity.resolve import resolve_clusters
    from goldenmatch.identity.store import IdentityStore

    df = pl.DataFrame(
        {
            "__row_id__": [r[0] for r in rows],
            "__source__": [r[1] for r in rows],
            "pk": [r[2] for r in rows],
            "name": [r[3] for r in rows],
        }
    )
    members: dict[int, list[int]] = {}
    for cid, mid in assignments:
        members.setdefault(cid, []).append(mid)
    pair_scores: dict[int, dict[tuple[int, int], float]] = {}
    for a, b, s in pairs:
        cid = next(c for c, ms in members.items() if a in ms)
        pair_scores.setdefault(cid, {})[(min(a, b), max(a, b))] = s
    clusters = {
        cid: {
            "members": ms,
            "confidence": 1.0,
            "bottleneck_pair": None,
            "pair_scores": pair_scores.get(cid, {}),
        }
        for cid, ms in members.items()
    }
    store = IdentityStore(backend="sqlite", path=str(db_path))
    resolve_clusters(
        clusters, df, [(a, b, s) for a, b, s in pairs], "mk", store, "r1",
        source_pk_col="pk",
    )
    edges, partition = set(), {}
    for node in store.list_identities(limit=10000):
        eid = node.entity_id
        for rec in store.get_records_for_entity(eid):
            partition[rec.record_id] = eid
        for edge in store.edges_for_entity(eid):
            if edge.kind == "same_as":
                edges.add(tuple(sorted((edge.record_a_id, edge.record_b_id))))
    return edges, _partition_sig(partition)


def _clusters(assignments):
    return {cid for cid, _ in assignments}


def test_identity_graph_parity(spark, tmp_path):
    from goldenmatch.sail.identity import build_identity_graph

    rows, pairs, assignments = _fixture()
    edges_ref, part_ref = _one_box_graph(
        rows, pairs, assignments, tmp_path / "identity.db"
    )

    source = spark.createDataFrame(rows, ["__row_id__", "__source__", "pk", "name"])
    pairs_df = spark.createDataFrame(pairs, ["a", "b", "score"])
    assign_df = spark.createDataFrame(assignments, ["cluster_id", "member_id"])
    golden_df = spark.createDataFrame(
        [(0, "Annie"), (5, "Robert")], ["cluster_id", "name"]  # multi-member only
    )

    g = build_identity_graph(
        pairs_df, assign_df, source, golden_df, run_meta=_run_meta(),
        source_pk_col="pk",
    )

    edges_got = {
        tuple(sorted((r["record_a_id"], r["record_b_id"])))
        for r in g.edges.collect()
    }
    part_got = _partition_sig(
        {r["record_id"]: r["entity_id"] for r in g.records.collect()}
    )
    node_count = g.nodes.count()

    assert edges_got == edges_ref                  # (1) edge-set parity
    assert part_got == part_ref                    # (2) partition equivalence
    assert node_count == len(_clusters(assignments))  # (3) count parity


def test_identity_graph_deterministic(spark, tmp_path):
    from goldenmatch.sail.identity import build_identity_graph

    rows, pairs, assignments = _fixture()
    source = spark.createDataFrame(rows, ["__row_id__", "__source__", "pk", "name"])
    pairs_df = spark.createDataFrame(pairs, ["a", "b", "score"])
    assign_df = spark.createDataFrame(assignments, ["cluster_id", "member_id"])
    golden_df = spark.createDataFrame([(0, "Annie"), (5, "Robert")], ["cluster_id", "name"])

    def run():
        g = build_identity_graph(
            pairs_df, assign_df, source, golden_df, run_meta=_run_meta(),
            source_pk_col="pk",
        )
        return sorted((r["record_id"], r["entity_id"]) for r in g.records.collect())

    assert run() == run()  # content-hash entity_ids -> deterministic
