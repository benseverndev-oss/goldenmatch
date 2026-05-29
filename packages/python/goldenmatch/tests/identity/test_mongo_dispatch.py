"""Tests for ``IdentityStore(backend="mongo")`` dispatch.

PR #558 shipped ``MongoIdentityStore`` as a standalone class. This PR
wires it through the unified ``IdentityStore`` interface via a per-method
``if self._backend == "mongo"`` early-return so existing SQLite + Postgres
callers can swap to Mongo by changing one constructor argument.

These tests cover the dispatch surface end-to-end: every public method
that ``MongoIdentityStore`` implements should work when called via
``IdentityStore(backend="mongo", ...)``.
"""
from __future__ import annotations

import pytest

mongomock = pytest.importorskip("mongomock")


@pytest.fixture
def store():
    """Fresh IdentityStore(backend="mongo") backed by a mongomock client."""
    from goldenmatch.identity.store import IdentityStore

    client = mongomock.MongoClient()
    s = IdentityStore(backend="mongo", database="gm", client=client)
    yield s
    s.close()


# ----- construction + close --------------------------------------------------


def test_constructs_with_mongo_backend() -> None:
    """``IdentityStore(backend="mongo", client=...)`` should route every
    call through the MongoIdentityStore -- no SQL connection needed."""
    from goldenmatch.identity.store import IdentityStore

    client = mongomock.MongoClient()
    s = IdentityStore(backend="mongo", database="gm", client=client)
    assert s._backend == "mongo"
    assert s._mongo is not None
    s.close()


def test_close_routes_to_mongo() -> None:
    """Close on a mongo-backed store delegates to MongoIdentityStore.close."""
    from goldenmatch.identity.store import IdentityStore

    client = mongomock.MongoClient()
    s = IdentityStore(backend="mongo", database="gm", client=client)
    # close should not touch self._conn (which doesn't exist on mongo).
    s.close()
    # And it should not raise on a second close (idempotent in practice).


# ----- identity nodes --------------------------------------------------------


def test_upsert_and_get_identity_through_dispatch(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(
        entity_id=eid, dataset="customers", status="active",
        confidence=0.95,
    ))
    n = store.get_identity(eid)
    assert n is not None
    assert n.entity_id == eid
    assert n.confidence == 0.95


def test_list_identities_through_dispatch(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    for ds in ("customers", "customers", "vendors"):
        store.upsert_identity(IdentityNode(
            entity_id=new_entity_id(), dataset=ds, status="active",
        ))
    customers = store.list_identities(dataset="customers")
    assert len(customers) == 2


def test_count_identities_through_dispatch(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    for _ in range(3):
        store.upsert_identity(IdentityNode(
            entity_id=new_entity_id(), dataset="customers",
        ))
    assert store.count_identities() == 3
    assert store.count_identities(dataset="customers") == 3
    assert store.count_identities(dataset="vendors") == 0


def test_retire_identity_through_dispatch(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid, dataset="customers"))
    store.retire_identity(eid, merged_into="other-eid")
    n = store.get_identity(eid)
    assert n is not None
    assert n.status == "retired"
    assert n.merged_into == "other-eid"


# ----- source records --------------------------------------------------------


def test_upsert_record_and_find_through_dispatch(store) -> None:
    from goldenmatch.identity.model import IdentityNode, SourceRecord
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid, dataset="customers"))
    store.upsert_record(SourceRecord(
        record_id="salesforce:001",
        source="salesforce", source_pk="001", record_hash="h1",
        entity_id=eid, payload={"name": "Alice"},
    ))

    assert store.find_entity_by_record("salesforce:001") == eid
    rec = store.get_record("salesforce:001")
    assert rec is not None
    assert rec.payload is not None
    assert rec.payload["name"] == "Alice"


def test_get_records_for_entity_through_dispatch(store) -> None:
    from goldenmatch.identity.model import IdentityNode, SourceRecord
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    for i in range(3):
        store.upsert_record(SourceRecord(
            record_id=f"src:{i}", source="src", source_pk=str(i),
            record_hash="h", entity_id=eid,
        ))
    assert len(store.get_records_for_entity(eid)) == 3


def test_lookup_entity_ids_batch_through_dispatch(store) -> None:
    from goldenmatch.identity.model import IdentityNode, SourceRecord
    from goldenmatch.identity.store import new_entity_id

    eid1, eid2 = new_entity_id(), new_entity_id()
    for eid in (eid1, eid2):
        store.upsert_identity(IdentityNode(entity_id=eid))
    store.upsert_record(SourceRecord(
        record_id="a", source="s", source_pk="a", record_hash="h",
        entity_id=eid1,
    ))
    store.upsert_record(SourceRecord(
        record_id="b", source="s", source_pk="b", record_hash="h",
        entity_id=eid2,
    ))

    out = store.lookup_entity_ids(["a", "b", "missing"])
    assert out == {"a": eid1, "b": eid2}


# ----- evidence edges --------------------------------------------------------


def test_add_edge_and_list_through_dispatch(store) -> None:
    from goldenmatch.identity.model import EvidenceEdge, IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    store.add_edge(EvidenceEdge(
        entity_id=eid, record_a_id="a", record_b_id="b",
        kind="same_as", score=0.91, run_name="test",
    ))
    edges = store.edges_for_entity(eid)
    assert len(edges) == 1
    assert edges[0].score == 0.91


def test_find_conflicts_through_dispatch(store) -> None:
    from goldenmatch.identity.model import EvidenceEdge, IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid, dataset="customers"))
    store.add_edge(EvidenceEdge(
        entity_id=eid, record_a_id="a", record_b_id="b",
        kind="same_as", run_name="test", dataset="customers",
    ))
    store.add_edge(EvidenceEdge(
        entity_id=eid, record_a_id="c", record_b_id="d",
        kind="conflicts_with", run_name="test", dataset="customers",
    ))
    conflicts = store.find_conflicts("customers")
    assert len(conflicts) == 1
    assert conflicts[0].kind == "conflicts_with"


# ----- events ----------------------------------------------------------------


def test_emit_event_and_history_through_dispatch(store) -> None:
    from goldenmatch.identity.model import IdentityEvent, IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    store.emit_event(IdentityEvent(
        entity_id=eid, kind="created", payload={"by": "test"}, run_name="r1",
    ))
    events = store.history(eid)
    assert len(events) == 1
    assert events[0].kind == "created"


def test_history_with_limit_through_dispatch(store) -> None:
    from goldenmatch.identity.model import IdentityEvent, IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    for k in ("a", "b", "c"):
        store.emit_event(IdentityEvent(entity_id=eid, kind=k))
    assert len(store.history(eid, limit=2)) == 2


def test_has_run_event_through_dispatch(store) -> None:
    from goldenmatch.identity.model import IdentityEvent, IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    store.emit_event(IdentityEvent(
        entity_id=eid, kind="merged", run_name="run-42",
    ))
    assert store.has_run_event(eid, "run-42", "merged") is True
    assert store.has_run_event(eid, "run-42", "split") is False


# ----- aliases ---------------------------------------------------------------


def test_alias_round_trip_through_dispatch(store) -> None:
    from goldenmatch.identity.model import IdentityAlias, IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    store.add_alias(IdentityAlias(
        alias="external:abc", entity_id=eid, kind="external_id",
        dataset="customers",
    ))
    assert store.resolve_alias("external:abc") == eid
    assert store.resolve_alias("missing") is None


# ----- regression: sqlite path stays untouched -------------------------------


def test_sqlite_backend_still_works(tmp_path) -> None:
    """The mongo dispatch should NOT affect existing SQLite callers."""
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import IdentityStore, new_entity_id

    db = tmp_path / "identity.db"
    s = IdentityStore(backend="sqlite", path=str(db))
    eid = new_entity_id()
    s.upsert_identity(IdentityNode(
        entity_id=eid, dataset="customers", status="active",
    ))
    n = s.get_identity(eid)
    assert n is not None
    assert n.entity_id == eid
    s.close()
