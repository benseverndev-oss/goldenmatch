"""Bulk staged-MERGE path, and its equivalence to the singleton path."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

fakesnow = pytest.importorskip("fakesnow")
pl = pytest.importorskip("polars")

from tests.snowflake.test_identity_store_snowflake import store  # noqa: F401,E402


def _nodes_df(ids):
    now = datetime(2026, 8, 20, 12, 0, 0)
    return pl.DataFrame(
        [
            {
                "entity_id": eid, "status": "active", "merged_into": None,
                "golden_record": None, "confidence": 0.9, "dataset": "c",
                "created_at": now, "updated_at": now,
            }
            for eid in ids
        ],
        schema={
            "entity_id": pl.Utf8, "status": pl.Utf8, "merged_into": pl.Utf8,
            "golden_record": pl.Utf8, "confidence": pl.Float64,
            "dataset": pl.Utf8, "created_at": pl.Datetime,
            "updated_at": pl.Datetime,
        },
    )


def _edges_df(rows):
    return pl.DataFrame(
        rows,
        schema={
            "entity_id": pl.Utf8, "record_a_id": pl.Utf8, "record_b_id": pl.Utf8,
            "kind": pl.Utf8, "score": pl.Float64, "matchkey_name": pl.Utf8,
            "controller_snapshot": pl.Utf8, "run_name": pl.Utf8, "dataset": pl.Utf8,
            "actor": pl.Utf8, "trust": pl.Float64, "recorded_at": pl.Datetime,
        },
    )


def _records_df(rows):
    return pl.DataFrame(
        rows,
        schema={
            "record_id": pl.Utf8, "source": pl.Utf8, "source_pk": pl.Utf8,
            "record_hash": pl.Utf8, "entity_id": pl.Utf8, "payload": pl.Utf8,
            "dataset": pl.Utf8, "first_seen_at": pl.Datetime,
            "last_seen_at": pl.Datetime,
        },
    )


def _events_df(rows):
    return pl.DataFrame(
        rows,
        schema={
            "entity_id": pl.Utf8, "kind": pl.Utf8, "payload": pl.Utf8,
            "run_name": pl.Utf8, "dataset": pl.Utf8, "actor": pl.Utf8,
            "trust": pl.Float64, "recorded_at": pl.Datetime,
        },
    )


def test_supports_bulk_is_true(store) -> None:  # noqa: F811
    assert store.supports_bulk is True


def test_bulk_upsert_identities_inserts(store) -> None:  # noqa: F811
    from goldenmatch.identity.store import new_entity_id

    ids = [new_entity_id() for _ in range(3)]
    store.bulk_upsert_identities(_nodes_df(ids))
    assert store.count_identities() == 3


def test_bulk_upsert_identities_is_idempotent(store) -> None:  # noqa: F811
    from goldenmatch.identity.store import new_entity_id

    ids = [new_entity_id() for _ in range(3)]
    df = _nodes_df(ids)
    store.bulk_upsert_identities(df)
    store.bulk_upsert_identities(df)
    assert store.count_identities() == 3


def test_bulk_and_singleton_agree(store) -> None:  # noqa: F811
    """The two write paths must produce identical rows."""
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    bulk_id, single_id = new_entity_id(), new_entity_id()
    store.bulk_upsert_identities(_nodes_df([bulk_id]))
    now = datetime(2026, 8, 20, 12, 0, 0)
    store.upsert_identity(IdentityNode(
        entity_id=single_id, status="active", confidence=0.9, dataset="c",
        created_at=now, updated_at=now,
    ))
    a, b = store.get_identity(bulk_id), store.get_identity(single_id)
    assert a is not None and b is not None
    for field_name in ("status", "confidence", "dataset", "merged_into"):
        assert getattr(a, field_name) == getattr(b, field_name), field_name


def test_bulk_on_empty_frame_is_a_noop(store) -> None:  # noqa: F811
    store.bulk_upsert_identities(_nodes_df([]))
    assert store.count_identities() == 0


def test_bulk_add_edges_is_idempotent_no_duplicates(store) -> None:  # noqa: F811
    """``stage_and_merge(update_cols=None)`` is the insert-if-absent path that
    replaces the UNIQUE constraint Snowflake does not enforce. Calling
    ``bulk_add_edges`` twice with the same key (a resolve replay) must not
    duplicate the edge -- this is the entire replay-idempotency mechanism for
    the batched write path, and had no test anywhere before this one.

    The replay changes ``score`` on the second call and asserts the ORIGINAL
    value survives. A row-count-only assertion here cannot distinguish
    insert-if-absent from an upsert that happens to write the same key back
    (i.e. it would pass even with ``update_cols=_EDGE_COLS``) -- the risk this
    test exists to catch is specifically that a replayed edge gets silently
    overwritten rather than skipped.
    """
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    now = datetime(2026, 8, 20, 12, 0, 0)

    def _row(score: float) -> dict:
        return {
            "entity_id": eid, "record_a_id": "recA", "record_b_id": "recB",
            "kind": "same_as", "score": score, "matchkey_name": "mk1",
            "controller_snapshot": None, "run_name": "run-1", "dataset": "c",
            "actor": None, "trust": None, "recorded_at": now,
        }

    store.bulk_add_edges(_edges_df([_row(0.95)]))
    store.bulk_add_edges(_edges_df([_row(0.10)]))  # replay, different score
    edges = store.edges_for_entity(eid)
    assert len(edges) == 1
    assert edges[0].record_a_id == "recA"
    assert edges[0].record_b_id == "recB"
    assert edges[0].score == 0.95, (
        "bulk_add_edges must not overwrite a replayed edge's score "
        f"(insert-if-absent, not upsert); got {edges[0].score}"
    )


def test_bulk_add_edges_and_add_edge_agree(store) -> None:  # noqa: F811
    """bulk_add_edges (insert-if-absent) must match add_edge's own MERGE
    semantics: a bulk replay of a key add_edge already wrote does not
    overwrite it. Uses a different score on the bulk call and asserts the
    ORIGINAL (add_edge's) score survives -- same reasoning as the test above:
    a row-count-only check can't tell insert-if-absent from an upsert that
    happens to write matching data."""
    from goldenmatch.identity.model import EvidenceEdge
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    now = datetime(2026, 8, 20, 12, 0, 0)
    edge = EvidenceEdge(
        entity_id=eid, record_a_id="r1", record_b_id="r2", kind="same_as",
        score=0.8, run_name="run-1", dataset="c", recorded_at=now,
    )
    store.add_edge(edge, return_id=False)
    df = _edges_df([
        {
            "entity_id": eid, "record_a_id": "r1", "record_b_id": "r2",
            "kind": "same_as", "score": 0.3, "matchkey_name": None,
            "controller_snapshot": None, "run_name": "run-1", "dataset": "c",
            "actor": None, "trust": None, "recorded_at": now,
        },
    ])
    store.bulk_add_edges(df)
    edges = store.edges_for_entity(eid)
    assert len(edges) == 1
    assert edges[0].score == 0.8, (
        "bulk_add_edges must not overwrite the edge add_edge already wrote; "
        f"got {edges[0].score}"
    )


# --------------------------------------------------------------------------
# bulk_upsert_records
# --------------------------------------------------------------------------

def test_bulk_upsert_records_inserts_and_is_idempotent(store) -> None:  # noqa: F811
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))  # source_records FK
    now = datetime(2026, 8, 20, 12, 0, 0)
    row = {
        "record_id": "src:pk1", "source": "src", "source_pk": "pk1",
        "record_hash": "h1", "entity_id": eid, "payload": "{\"a\": 1}",
        "dataset": "c", "first_seen_at": now, "last_seen_at": now,
    }
    df = _records_df([row])
    store.bulk_upsert_records(df)
    store.bulk_upsert_records(df)  # replay must not duplicate
    recs = store.get_records_for_entity(eid)
    assert len(recs) == 1
    assert recs[0].record_id == "src:pk1"


def test_bulk_upsert_records_and_upsert_record_agree(store) -> None:  # noqa: F811
    """Same JSONB/VARIANT round-trip concern as identity nodes: payload must
    come back as a dict through both the bulk and singleton paths."""
    from goldenmatch.identity.model import IdentityNode, SourceRecord
    from goldenmatch.identity.store import new_entity_id

    bulk_eid, single_eid = new_entity_id(), new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=bulk_eid))  # source_records FK
    store.upsert_identity(IdentityNode(entity_id=single_eid))
    now = datetime(2026, 8, 20, 12, 0, 0)
    store.bulk_upsert_records(_records_df([{
        "record_id": "src:bulk", "source": "src", "source_pk": "bulk",
        "record_hash": "h1", "entity_id": bulk_eid,
        "payload": "{\"name\": \"Ada\", \"score\": 1.5}",
        "dataset": "c", "first_seen_at": now, "last_seen_at": now,
    }]))
    store.upsert_record(SourceRecord(
        record_id="src:single", source="src", source_pk="single",
        record_hash="h1", entity_id=single_eid,
        payload={"name": "Ada", "score": 1.5}, dataset="c",
        first_seen_at=now, last_seen_at=now,
    ))
    bulk_rec = store.get_record("src:bulk")
    single_rec = store.get_record("src:single")
    assert bulk_rec is not None and single_rec is not None
    assert bulk_rec.payload == single_rec.payload == {"name": "Ada", "score": 1.5}
    assert isinstance(bulk_rec.payload, dict)


# --------------------------------------------------------------------------
# bulk_emit_events -- Finding 1's regression coverage
# --------------------------------------------------------------------------

def test_bulk_emit_events_appends_without_dedupe(store) -> None:  # noqa: F811
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    now = datetime(2026, 8, 20, 12, 0, 0)
    row = {
        "entity_id": eid, "kind": "created", "payload": "{\"x\": 1}",
        "run_name": "run-1", "dataset": "c", "actor": "pipeline",
        "trust": 0.7, "recorded_at": now,
    }
    df = _events_df([row])
    store.bulk_emit_events(df)
    store.bulk_emit_events(df)  # no dedupe key -- both rows land
    hist = store.history(eid)
    assert len(hist) == 2


def test_bulk_emit_events_payload_round_trips_as_a_dict(store) -> None:  # noqa: F811
    """The assertion that would have caught Finding 1 (missing PARSE_JSON on
    the VARIANT payload column) had this suite's oracle been capable of it.

    IMPORTANT: this passes on fakesnow whether or not bulk_emit_events casts
    with PARSE_JSON, because fakesnow's write_pandas is a DuckDB
    INSERT ... SELECT * FROM df that auto-parses a JSON-looking string
    regardless of an explicit cast -- see the long comment on
    SnowflakeIdentityStore.bulk_emit_events. It is kept here because it is
    still the correct assertion in principle (it would catch a regression on
    a capable oracle, and it documents the shape the fix targets), but a green
    result here is NOT evidence that the PARSE_JSON fix is correct on real
    Snowflake -- only manual reasoning against Snowflake's documented
    write_pandas / COPY INTO behaviour can claim that.
    """
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    now = datetime(2026, 8, 20, 12, 0, 0)
    nested = {"score": 5, "tags": ["a", "b"], "meta": {"ok": True}}
    df = _events_df([{
        "entity_id": eid, "kind": "created", "payload": json.dumps(nested),
        "run_name": "run-1", "dataset": "c", "actor": "pipeline",
        "trust": 0.7, "recorded_at": now,
    }])
    store.bulk_emit_events(df)
    hist = store.history(eid)
    assert len(hist) == 1
    assert isinstance(hist[0].payload, dict), (
        f"expected payload as a dict, got {type(hist[0].payload)}: "
        f"{hist[0].payload!r}"
    )
    assert hist[0].payload == nested


def test_bulk_emit_events_and_emit_event_agree(store) -> None:  # noqa: F811
    from goldenmatch.identity.model import IdentityEvent
    from goldenmatch.identity.store import new_entity_id

    bulk_eid, single_eid = new_entity_id(), new_entity_id()
    now = datetime(2026, 8, 20, 12, 0, 0)
    store.bulk_emit_events(_events_df([{
        "entity_id": bulk_eid, "kind": "created", "payload": "{\"z\": 9}",
        "run_name": "run-1", "dataset": "c", "actor": "pipeline",
        "trust": 0.7, "recorded_at": now,
    }]))
    store.emit_event(IdentityEvent(
        entity_id=single_eid, kind="created", payload={"z": 9},
        run_name="run-1", dataset="c", actor="pipeline", trust=0.7,
        recorded_at=now,
    ), return_id=False)
    bulk_ev = store.history(bulk_eid)[0]
    single_ev = store.history(single_eid)[0]
    assert bulk_ev.payload == single_ev.payload == {"z": 9}
    assert bulk_ev.actor == single_ev.actor == "pipeline"
    assert bulk_ev.trust == single_ev.trust == 0.7
    # entry_hash is the one documented divergence: NULL on the bulk path,
    # computed on the singleton path.
    assert bulk_ev.entry_hash is None
    assert single_ev.entry_hash is not None


# --------------------------------------------------------------------------
# bulk_writes / bulk_flush_checkpoint -- Finding 2's regression coverage
# --------------------------------------------------------------------------

def test_bulk_writes_wraps_a_real_flush_without_crashing(store) -> None:  # noqa: F811
    """Regression test for Finding 2: store.bulk_writes() used to open a
    real BEGIN/COMMIT. Wrapping a real staged bulk flush (which runs DDL)
    inside that scope crashed under fakesnow with a DuckDB
    TransactionException ("a single transaction can only write to a single
    attached database") -- and, per SnowflakeIdentityStore.bulk_writes's
    docstring, would silently lose atomicity on real Snowflake too, since DDL
    there commits any open transaction implicitly. This is exactly the shape
    resolve_clusters uses (resolve.py:881): the whole write body, bulk
    flushes included, runs inside one bulk_writes() scope."""
    from goldenmatch.identity.store import new_entity_id

    ids = [new_entity_id() for _ in range(3)]
    with store.bulk_writes():
        store.bulk_upsert_identities(_nodes_df(ids))
    assert store.count_identities() == 3


def test_bulk_flush_checkpoint_is_a_noop(store) -> None:  # noqa: F811
    from goldenmatch.identity.store import new_entity_id

    ids = [new_entity_id() for _ in range(2)]
    with store.bulk_writes():
        store.bulk_upsert_identities(_nodes_df(ids))
        store.bulk_flush_checkpoint()  # must not raise or lose the batch
    assert store.count_identities() == 2


# --------------------------------------------------------------------------
# Finding 4: resolve_clusters / apply_batch actually routes into the bulk
# path on this backend -- previously untested end-to-end.
# --------------------------------------------------------------------------

def test_resolve_clusters_routes_brand_new_singletons_into_the_bulk_path(
    store, monkeypatch,  # noqa: F811
) -> None:
    """Exercises, in one call: the supports_bulk gate (resolve.py:~718), the
    store.bulk_writes() wrapper around the whole write body (resolve.py:881),
    the bulk-flush call sequence, and the run_event_entities CREATED-event
    preload gate (resolve.py:~749) -- none of which had a single
    resolve_clusters/apply_batch call against a Snowflake-backed store
    anywhere in this suite before this test."""
    from goldenmatch.identity.resolve import resolve_clusters

    n = 5
    df = pl.DataFrame({
        "__row_id__": list(range(n)),
        "__source__": ["crm"] * n,
        "raw_id": [f"r{i}" for i in range(n)],
        "name": [f"person {i}" for i in range(n)],
    })
    clusters = {
        i: {"members": [i], "size": 1, "pair_scores": {},
            "confidence": 1.0, "bottleneck_pair": None, "oversized": False}
        for i in range(n)
    }

    calls: list[str] = []

    def _spy(name):
        orig = getattr(store, name)

        def _wrapped(*a, **kw):
            calls.append(name)
            return orig(*a, **kw)
        monkeypatch.setattr(store, name, _wrapped)

    for name in (
        "upsert_identity", "upsert_record", "emit_event", "add_edge",
        "bulk_upsert_identities", "bulk_upsert_records",
        "bulk_add_edges", "bulk_emit_events",
    ):
        _spy(name)

    resolve_clusters(
        clusters, df, [], "mk", store, run_name="run1", dataset="crm",
        source_pk_col="raw_id", emit_singletons=True,
    )

    assert store.count_identities() == n
    # Routing, not just data shape: brand-new singletons must take the bulk
    # path, not the per-row loop.
    assert "bulk_upsert_identities" in calls
    assert "upsert_identity" not in calls, calls

    # A second resolve of the SAME run must not error (bulk_writes() no
    # longer opens a real transaction around the flush -- see Finding 2) and
    # must not duplicate identities -- the run_event_entities preload
    # (resolve.py:~749) is what keeps the CREATED-event guard correct here.
    resolve_clusters(
        clusters, df, [], "mk", store, run_name="run1", dataset="crm",
        source_pk_col="raw_id", emit_singletons=True,
    )
    assert store.count_identities() == n
