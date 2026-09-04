"""Layer-2 gate (#966): incremental identity resolution on the Sail tier.

Stage S5 shipped create-only; this covers absorb / merge against an EXISTING
store. Semantics mirror one-box ``identity.resolve.resolve_clusters`` -- see
docs/superpowers/specs/2026-08-08-sail-identity-layer2-incremental-design.md.

Two tiers, matching ``test_sail_identity_parity.py``:
  * PURE unit tests (the public import surface) run in the normal python lane.
  * SERVER tests use an in-process Sail Spark Connect server behind an
    ``importorskip`` on ``pyspark``, so they SKIP without a Spark client and run in the
    `sail` CI lane.
"""
from __future__ import annotations

import pytest

pytest.importorskip("pyspark")


RUN = {
    "run_name": "run-2",
    "recorded_at": "2026-08-08T00:00:00",
    "dataset": "people",
    "matchkey_name": "mk",
}
T0 = "2026-01-01T00:00:00"  # prior-run timestamp for existing nodes


# --------------------------------------------------------------------------
# Tier 1: import surface (no Spark).
# --------------------------------------------------------------------------


def test_incremental_is_exported_without_sail_extra():
    """The Layer-2 entry point must be importable with no pyspark, same as the
    create path -- consumers pin the contract without a Spark runtime."""
    from goldenmatch.spark import build_identity_graph_incremental  # noqa: F401


def test_incremental_signature_is_additive():
    """`IdentityGraphFrames` is a frozen contract, so Layer 2 arrives as a NEW
    entry point rather than a reshaped dataclass. Existing consumers unaffected."""
    import dataclasses
    import inspect

    from goldenmatch.spark import (
        IdentityGraphFrames,
        build_identity_graph,
        build_identity_graph_incremental,
    )

    # The create path's signature is untouched.
    create = inspect.signature(build_identity_graph).parameters
    assert "existing_records" not in create
    assert "existing_nodes" not in create

    inc = inspect.signature(build_identity_graph_incremental).parameters
    for name in ("pairs", "assignments", "source_df", "golden_df"):
        assert name in inc
    for name in ("existing_records", "existing_nodes", "run_meta"):
        assert inc[name].kind is inspect.Parameter.KEYWORD_ONLY
    # Prior state is optional -> degrades to the create path.
    assert inc["existing_records"].default is None
    assert inc["existing_nodes"].default is None

    # Output contract unchanged.
    assert [f.name for f in dataclasses.fields(IdentityGraphFrames)] == [
        "nodes", "records", "edges", "events",
    ]


# --------------------------------------------------------------------------
# Tier 2: server tests.
# --------------------------------------------------------------------------


def _source(spark, rows):
    """rows: (row_id, pk) -> source frame with a PK so record_ids are readable
    (`people:<pk>`), which keeps the assertions legible."""
    return spark.createDataFrame(
        [(r, "people", pk, f"name{pk}") for r, pk in rows],
        ["__row_id__", "__source__", "pk", "first_name"],
    )


def _assignments(spark, rows):
    return spark.createDataFrame(list(rows), ["member_id", "cluster_id"])


def _pairs(spark, rows):
    return spark.createDataFrame(list(rows), ["a", "b", "score"])


def _golden(spark, rows):
    return spark.createDataFrame(list(rows), ["cluster_id", "first_name"])


def _existing_records(spark, rows):
    return spark.createDataFrame(
        [(rid, eid, "people", T0, T0) for rid, eid in rows],
        ["record_id", "entity_id", "dataset", "first_seen_at", "last_seen_at"],
    )


def _existing_nodes(spark, rows):
    """rows: (entity_id, created_at).

    An explicit schema is REQUIRED here: `merged_into` / `golden_record` /
    `confidence` are all-NULL in these fixtures and Spark cannot infer a type
    for a column it only ever sees as None.
    """
    from pyspark.sql.types import (
        DoubleType,
        StringType,
        StructField,
        StructType,
    )

    schema = StructType([
        StructField("entity_id", StringType(), False),
        StructField("status", StringType(), True),
        StructField("merged_into", StringType(), True),
        StructField("golden_record", StringType(), True),
        StructField("confidence", DoubleType(), True),
        StructField("dataset", StringType(), True),
        StructField("created_at", StringType(), True),
        StructField("updated_at", StringType(), True),
    ])
    return spark.createDataFrame(
        [(eid, "active", None, None, None, "people", created, created)
         for eid, created in rows],
        schema,
    )


def _rows(df):
    return [r.asDict() for r in df.collect()]


def _by(df, key):
    return {r[key]: r for r in _rows(df)}


def test_no_prior_state_is_the_create_path(spark):
    """`existing_*=None` must be the create path VERBATIM -- same entity ids as
    build_identity_graph, so calling the incremental entry point
    unconditionally is safe on a fresh store."""
    from goldenmatch.spark.identity import (
        build_identity_graph,
        build_identity_graph_incremental,
    )

    src = _source(spark, [(0, 1), (1, 2)])
    asg = _assignments(spark, [(0, 100), (1, 100)])
    prs = _pairs(spark, [(0, 1, 0.9)])
    gld = _golden(spark, [(100, "name1")])

    a = build_identity_graph(prs, asg, src, gld, run_meta=RUN, source_pk_col="pk")
    b = build_identity_graph_incremental(
        prs, asg, src, gld, run_meta=RUN, source_pk_col="pk"
    )
    assert {r["entity_id"] for r in _rows(a.records)} == {
        r["entity_id"] for r in _rows(b.records)
    }


def test_absorb_reuses_the_existing_entity(spark):
    """One overlapping entity -> the cluster joins it. The NEW record lands on
    the SAME entity_id (not a freshly minted one), which is the whole point."""
    from goldenmatch.spark.identity import build_identity_graph_incremental

    # people:1 already belongs to ent:A. The run clusters it with a new people:2.
    src = _source(spark, [(0, 1), (1, 2)])
    asg = _assignments(spark, [(0, 100), (1, 100)])
    prs = _pairs(spark, [(0, 1, 0.9)])
    gld = _golden(spark, [(100, "name1")])

    frames = build_identity_graph_incremental(
        prs, asg, src, gld,
        existing_records=_existing_records(spark, [("people:1", "ent:A")]),
        existing_nodes=_existing_nodes(spark, [("ent:A", T0)]),
        run_meta=RUN, source_pk_col="pk",
    )

    recs = _by(frames.records, "record_id")
    assert recs["people:1"]["entity_id"] == "ent:A"
    assert recs["people:2"]["entity_id"] == "ent:A"  # absorbed, not re-minted

    # first_seen_at is preserved for the known record, this run for the new one.
    assert recs["people:1"]["first_seen_at"] == T0
    assert recs["people:2"]["first_seen_at"] == RUN["recorded_at"]

    node = _by(frames.nodes, "entity_id")["ent:A"]
    assert node["status"] == "active"
    assert node["created_at"] == T0            # same identity, original birth
    assert node["updated_at"] == RUN["recorded_at"]

    # Only the NEWLY added record earns an ABSORBED_RECORD event.
    kinds = [r["kind"] for r in _rows(frames.events)]
    assert kinds.count("ABSORBED_RECORD") == 1
    assert "CREATED" not in kinds


def test_merge_retires_losers_into_the_winner(spark):
    """Two overlapping entities -> merge. Losers go status=merged_into with
    merged_into set, and their OTHER records follow them to the winner."""
    from goldenmatch.spark.identity import build_identity_graph_incremental

    # ent:A holds people:1; ent:B holds people:2 + people:9 (9 is NOT in this
    # run, so it is the record that must FOLLOW its entity into the winner).
    # Counts tie 1-1 and created_at ties, so the documented final tie-break
    # (entity_id ascending) makes ent:A the winner deterministically.
    src = _source(spark, [(0, 1), (1, 2)])
    asg = _assignments(spark, [(0, 100), (1, 100)])
    prs = _pairs(spark, [(0, 1, 0.95)])
    gld = _golden(spark, [(100, "name1")])

    frames = build_identity_graph_incremental(
        prs, asg, src, gld,
        existing_records=_existing_records(spark, [
            ("people:1", "ent:A"), ("people:2", "ent:B"), ("people:9", "ent:B"),
        ]),
        existing_nodes=_existing_nodes(spark, [("ent:A", T0), ("ent:B", T0)]),
        run_meta=RUN, source_pk_col="pk",
    )

    recs = _by(frames.records, "record_id")
    assert recs["people:1"]["entity_id"] == "ent:A"
    assert recs["people:2"]["entity_id"] == "ent:A"
    # The LOSER's record that this run never saw is reassigned to the winner --
    # without this, ent:B's history would be orphaned behind a retired node.
    assert recs["people:9"]["entity_id"] == "ent:A"

    nodes = _by(frames.nodes, "entity_id")
    assert nodes["ent:A"]["status"] == "active"
    assert nodes["ent:B"]["status"] == "merged_into"
    assert nodes["ent:B"]["merged_into"] == "ent:A"

    kinds = [r["kind"] for r in _rows(frames.events)]
    assert kinds.count("MERGED_WITH") == 2  # winner + loser


def test_records_frame_is_a_delta_not_a_restatement(spark):
    """A WINNER's pre-existing records that this run did not touch are NOT
    re-emitted -- they already point at the winner, so re-stating them would
    make the frame O(store) instead of O(change). Only a LOSER's records move.

    (Learned by getting a fixture wrong: `records` is what this run CHANGED.)
    """
    from goldenmatch.spark.identity import build_identity_graph_incremental

    # people:9 belongs to ent:A, which WINS -> unchanged -> absent.
    src = _source(spark, [(0, 1), (1, 2)])
    asg = _assignments(spark, [(0, 100), (1, 100)])
    prs = _pairs(spark, [(0, 1, 0.95)])
    gld = _golden(spark, [(100, "name1")])

    frames = build_identity_graph_incremental(
        prs, asg, src, gld,
        existing_records=_existing_records(spark, [
            ("people:1", "ent:A"), ("people:9", "ent:A"), ("people:2", "ent:B"),
        ]),
        existing_nodes=_existing_nodes(spark, [("ent:A", T0), ("ent:B", T0)]),
        run_meta=RUN, source_pk_col="pk",
    )
    emitted = {r["record_id"] for r in _rows(frames.records)}
    assert emitted == {"people:1", "people:2"}
    assert "people:9" not in emitted


def test_winner_is_the_entity_holding_MOST_OF_THIS_CLUSTER(spark):
    """The rule that is easy to get backwards.

    one-box ranks by how many of THIS CLUSTER'S records an entity holds, not by
    how large the entity is overall. Here ent:BIG holds 3 records globally but
    only ONE in this cluster; ent:SMALL holds two of this cluster's three. The
    winner must be ent:SMALL. A "largest entity wins" implementation passes
    every other test in this file and fails only this one.
    """
    from goldenmatch.spark.identity import build_identity_graph_incremental

    src = _source(spark, [(0, 1), (1, 2), (2, 3)])
    asg = _assignments(spark, [(0, 100), (1, 100), (2, 100)])
    prs = _pairs(spark, [(0, 1, 0.9), (1, 2, 0.9)])
    gld = _golden(spark, [(100, "name1")])

    frames = build_identity_graph_incremental(
        prs, asg, src, gld,
        existing_records=_existing_records(spark, [
            ("people:1", "ent:SMALL"), ("people:2", "ent:SMALL"),   # 2 in-cluster
            ("people:3", "ent:BIG"),                                 # 1 in-cluster
            ("people:7", "ent:BIG"), ("people:8", "ent:BIG"),        # bigger overall
        ]),
        existing_nodes=_existing_nodes(spark, [("ent:BIG", T0), ("ent:SMALL", T0)]),
        run_meta=RUN, source_pk_col="pk",
    )

    recs = _by(frames.records, "record_id")
    assert recs["people:1"]["entity_id"] == "ent:SMALL"
    assert recs["people:3"]["entity_id"] == "ent:SMALL"
    nodes = _by(frames.nodes, "entity_id")
    assert nodes["ent:BIG"]["status"] == "merged_into"
    assert nodes["ent:BIG"]["merged_into"] == "ent:SMALL"


def test_merge_tie_breaks_on_oldest_created_at(spark):
    """Equal in-cluster counts -> oldest created_at wins (one-box `_node_age`)."""
    from goldenmatch.spark.identity import build_identity_graph_incremental

    src = _source(spark, [(0, 1), (1, 2)])
    asg = _assignments(spark, [(0, 100), (1, 100)])
    prs = _pairs(spark, [(0, 1, 0.9)])
    gld = _golden(spark, [(100, "name1")])

    frames = build_identity_graph_incremental(
        prs, asg, src, gld,
        existing_records=_existing_records(spark, [
            ("people:1", "ent:NEW"), ("people:2", "ent:OLD"),  # 1 each
        ]),
        existing_nodes=_existing_nodes(spark, [
            ("ent:NEW", "2026-05-05T00:00:00"), ("ent:OLD", "2020-01-01T00:00:00"),
        ]),
        run_meta=RUN, source_pk_col="pk",
    )
    assert _by(frames.records, "record_id")["people:1"]["entity_id"] == "ent:OLD"


def test_unrelated_cluster_still_creates(spark):
    """A cluster with no overlap is a create even when the store is non-empty --
    absorb must not capture unrelated records."""
    from goldenmatch.spark.identity import build_identity_graph_incremental

    src = _source(spark, [(0, 5), (1, 6)])
    asg = _assignments(spark, [(0, 200), (1, 200)])
    prs = _pairs(spark, [(0, 1, 0.9)])
    gld = _golden(spark, [(200, "name5")])

    frames = build_identity_graph_incremental(
        prs, asg, src, gld,
        existing_records=_existing_records(spark, [("people:1", "ent:A")]),
        existing_nodes=_existing_nodes(spark, [("ent:A", T0)]),
        run_meta=RUN, source_pk_col="pk",
    )
    eids = {r["entity_id"] for r in _rows(frames.records)}
    assert eids and "ent:A" not in eids
    assert all(e.startswith("ent:h1:") for e in eids)
    assert [r["kind"] for r in _rows(frames.events)].count("CREATED") == 1


def _one_box_incremental(rows, cluster_id, members, pairs, seed_entities, db_path):
    """Run the one-box resolver against a store PRE-SEEDED with existing
    entities (mirrors `_existing_records`/`_existing_nodes` above, entity_id
    for entity_id, so the winner can be compared directly rather than only via
    an id-independent partition signature the way `_one_box_graph` in
    `test_sail_identity_parity.py` does for the create-only path).

    ``seed_entities``: ``[(entity_id, [record_id, ...], created_at_iso), ...]``,
    written straight in via ``upsert_identity``/``upsert_record`` -- bypassing
    `resolve_clusters` for the seed step, so both systems start from the exact
    same prior state.

    Returns ``(winner_entity_id, {entity_id: (status, merged_into)})``.
    """
    import datetime

    import polars as pl
    from goldenmatch.identity.model import IdentityNode, SourceRecord
    from goldenmatch.identity.resolve import resolve_clusters
    from goldenmatch.identity.store import IdentityStore

    store = IdentityStore(backend="sqlite", path=str(db_path))
    for eid, record_ids, created_at in seed_entities:
        cdt = datetime.datetime.fromisoformat(created_at)
        store.upsert_identity(IdentityNode(entity_id=eid, created_at=cdt, updated_at=cdt))
        for rid in record_ids:
            store.upsert_record(SourceRecord(
                record_id=rid, source=rid.split(":")[0], source_pk=rid.split(":")[1],
                record_hash="seed", entity_id=eid, first_seen_at=cdt, last_seen_at=cdt,
            ))

    df = pl.DataFrame({
        "__row_id__": [r[0] for r in rows],
        "__source__": [r[1] for r in rows],
        "pk": [r[2] for r in rows],
        "first_name": [r[3] for r in rows],
    })
    pair_scores = {(min(a, b), max(a, b)): s for a, b, s in pairs}
    clusters = {
        cluster_id: {
            "members": members, "confidence": 1.0,
            "bottleneck_pair": None, "pair_scores": pair_scores,
        }
    }
    resolve_clusters(clusters, df, pairs, "mk", store, RUN["run_name"], source_pk_col="pk")

    record_ids = [f"people:{pk}" for _rid, _src, pk, _name in rows]
    winners = {store.get_record(rid).entity_id for rid in record_ids}
    assert len(winners) == 1, f"this cluster's records split across entities: {winners}"
    winner = next(iter(winners))

    node_status = {
        eid: (store.get_identity(eid).status, store.get_identity(eid).merged_into)
        for eid, _rids, _created in seed_entities
    }
    return winner, node_status


def test_incremental_winner_selection_matches_one_box_on_a_real_merge(spark, tmp_path):
    """Cross-execution parity for the WINNER RULE itself: ranked by how many
    of THIS CLUSTER's records an entity holds, not by the entity's overall
    size -- tie-broken by oldest `created_at`. This is the "easy to get
    backwards" part `resolve_cluster_entities`' docstring names, and the part
    `build_identity_graph_incremental`'s own docstring claims mirrors one-box.

    `test_identity_graph_parity` (test_sail_identity_parity.py) already runs a
    real one-box-vs-Spark comparison, but only for the CREATE-ONLY path with
    no pre-existing entities -- the winner/tie-break branch inside
    `resolve_cluster_entities` never fires there. This seeds BOTH systems with
    the SAME pre-existing entities (identical entity_id strings) and checks
    they pick the SAME winner on a scenario where "most of this cluster" and
    "biggest overall" disagree: ent:SMALL holds 2 of this cluster's 3 records;
    ent:BIG holds only 1 of this cluster's 3 but owns MORE records overall
    (people:7, people:8).

    Deliberately scoped to the records THIS run actually saw (people:1/2/3):
    it does NOT compare the fate of ent:BIG's untouched records (people:7/8,
    never part of this run's cluster). Audit finding, not asserted here: those
    two implementations DISAGREE on that point. `resolve.py`'s merge-loser
    reassignment loop is scoped to `existing`, built only from THIS cluster's
    own record_ids -- a loser's other records keep pointing at the retired
    entity (verified by running `resolve_clusters` directly: people:7/8 stay
    on `ent:BIG` after the merge). Spark's `build_incremental_records`
    reassigns a loser's ENTIRE record set via a join against the full
    `existing_records` table (confirmed by reading `resolve_cluster_entities`
    / `build_incremental_records` in `spark/identity.py`), matching its own
    `test_merge_retires_losers_into_the_winner` above. Baking either side into
    an assertion here would silently pick a winner in that unresolved mismatch.
    """
    from goldenmatch.spark.identity import build_identity_graph_incremental

    rows = [(0, "people", 1, "name1"), (1, "people", 2, "name1"), (2, "people", 3, "name1")]
    pairs = [(0, 1, 0.9), (1, 2, 0.9)]
    seed = [
        ("ent:SMALL", ["people:1", "people:2"], T0),
        ("ent:BIG", ["people:3", "people:7", "people:8"], T0),
    ]

    one_box_winner, one_box_nodes = _one_box_incremental(
        rows, 100, [0, 1, 2], pairs, seed, tmp_path / "identity.db",
    )

    src = _source(spark, [(0, 1), (1, 2), (2, 3)])
    asg = _assignments(spark, [(0, 100), (1, 100), (2, 100)])
    prs = _pairs(spark, pairs)
    gld = _golden(spark, [(100, "name1")])

    frames = build_identity_graph_incremental(
        prs, asg, src, gld,
        existing_records=_existing_records(spark, [
            ("people:1", "ent:SMALL"), ("people:2", "ent:SMALL"),
            ("people:3", "ent:BIG"), ("people:7", "ent:BIG"), ("people:8", "ent:BIG"),
        ]),
        existing_nodes=_existing_nodes(spark, [("ent:SMALL", T0), ("ent:BIG", T0)]),
        run_meta=RUN, source_pk_col="pk",
    )
    recs = _by(frames.records, "record_id")
    spark_winner = {recs[r]["entity_id"] for r in ("people:1", "people:2", "people:3")}
    assert len(spark_winner) == 1, spark_winner
    spark_winner = next(iter(spark_winner))

    assert spark_winner == one_box_winner, (
        f"Spark picked {spark_winner!r}, one-box picked {one_box_winner!r} for "
        "the SAME pre-existing entities and the SAME cluster"
    )

    nodes = _by(frames.nodes, "entity_id")
    assert nodes[one_box_winner]["status"] == "active"
    loser = "ent:BIG" if one_box_winner == "ent:SMALL" else "ent:SMALL"
    assert nodes[loser]["status"] == "merged_into"
    assert nodes[loser]["merged_into"] == one_box_winner
    assert one_box_nodes[loser] == ("merged_into", one_box_winner)


def test_re_observation_is_idempotent(spark):
    """Re-running the SAME clusters against the store they produced changes no
    assignment and emits no absorb/create event -- the convergence property the
    create-only path lacks."""
    from goldenmatch.spark.identity import build_identity_graph_incremental

    src = _source(spark, [(0, 1), (1, 2)])
    asg = _assignments(spark, [(0, 100), (1, 100)])
    prs = _pairs(spark, [(0, 1, 0.9)])
    gld = _golden(spark, [(100, "name1")])

    frames = build_identity_graph_incremental(
        prs, asg, src, gld,
        existing_records=_existing_records(spark, [
            ("people:1", "ent:A"), ("people:2", "ent:A"),
        ]),
        existing_nodes=_existing_nodes(spark, [("ent:A", T0)]),
        run_meta=RUN, source_pk_col="pk",
    )
    assert {r["entity_id"] for r in _rows(frames.records)} == {"ent:A"}
    # Nothing new was added, so no ABSORBED_RECORD and no CREATED.
    assert _rows(frames.events) == []
