"""Tests for the MongoDB Identity Store backend.

Uses ``mongomock`` so no live MongoDB instance is required.
"""
from __future__ import annotations

import pytest

mongomock = pytest.importorskip("mongomock")


@pytest.fixture
def store(monkeypatch):
    """Fresh MongoIdentityStore on a clean mongomock client."""
    client = mongomock.MongoClient()
    from goldenmatch.identity.mongo_backend import MongoIdentityStore
    s = MongoIdentityStore(client=client, database="gm")
    yield s
    s.close()


# ----- identity nodes -----------------------------------------------------


def test_upsert_identity_and_get(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(
        entity_id=eid, dataset="customers", status="active",
        confidence=0.99, merged_into=None,
    ))
    n = store.get_identity(eid)
    assert n is not None
    assert n.entity_id == eid
    assert n.dataset == "customers"
    assert n.confidence == 0.99


def test_upsert_identity_is_idempotent(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    node = IdentityNode(entity_id=eid, dataset="customers", status="active")
    store.upsert_identity(node)
    store.upsert_identity(node)
    assert store.count_identities() == 1


def test_list_identities_filters(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    for ds in ("customers", "customers", "vendors"):
        store.upsert_identity(IdentityNode(
            entity_id=new_entity_id(), dataset=ds, status="active",
        ))
    customers = store.list_identities(dataset="customers")
    vendors = store.list_identities(dataset="vendors")
    assert len(customers) == 2
    assert len(vendors) == 1


def test_retire_identity_sets_status(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid, dataset="customers"))
    store.retire_identity(eid, merged_into="some-other-eid")
    n = store.get_identity(eid)
    assert n is not None
    assert n.status == "retired"
    assert n.merged_into == "some-other-eid"


# ----- source records ----------------------------------------------------


def test_upsert_record_and_lookup(store) -> None:
    from goldenmatch.identity.model import IdentityNode, SourceRecord
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid, dataset="customers"))
    store.upsert_record(SourceRecord(
        record_id="salesforce:001",
        source="salesforce", source_pk="001", record_hash="h1",
        entity_id=eid, payload={"name": "Alice"}, dataset="customers",
    ))

    assert store.find_entity_by_record("salesforce:001") == eid
    rec = store.get_record("salesforce:001")
    assert rec is not None
    assert rec.payload is not None
    assert rec.payload["name"] == "Alice"

    records = store.get_records_for_entity(eid)
    assert len(records) == 1
    assert records[0].record_id == "salesforce:001"


def test_lookup_entity_ids_batch(store) -> None:
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


# ----- evidence edges ----------------------------------------------------


def test_add_edge_and_list(store) -> None:
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


def test_add_edge_replay_is_idempotent(store) -> None:
    from goldenmatch.identity.model import EvidenceEdge, IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    edge = EvidenceEdge(
        entity_id=eid, record_a_id="a", record_b_id="b",
        kind="same_as", score=0.91, run_name="test",
    )
    store.add_edge(edge)
    store.add_edge(edge)
    assert len(store.edges_for_entity(eid)) == 1


def test_find_conflicts_filters_by_kind(store) -> None:
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


# ----- events ------------------------------------------------------------


def test_emit_event_and_history_order(store) -> None:
    from goldenmatch.identity.model import IdentityEvent, IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    store.emit_event(IdentityEvent(
        entity_id=eid, kind="created", payload={"by": "test"}, run_name="r1",
    ))
    store.emit_event(IdentityEvent(
        entity_id=eid, kind="absorbed", payload={"src": "x"}, run_name="r2",
    ))
    events = store.history(eid)
    kinds = [e.kind for e in events]
    assert kinds == ["created", "absorbed"]


def test_history_limit(store) -> None:
    from goldenmatch.identity.model import IdentityEvent, IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    for k in ("a", "b", "c"):
        store.emit_event(IdentityEvent(entity_id=eid, kind=k))
    events = store.history(eid, limit=2)
    assert len(events) == 2


def test_has_run_event(store) -> None:
    from goldenmatch.identity.model import IdentityEvent, IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    store.emit_event(IdentityEvent(
        entity_id=eid, kind="merged", run_name="run-42",
    ))
    assert store.has_run_event(eid, "run-42", "merged") is True
    assert store.has_run_event(eid, "run-42", "split") is False
    assert store.has_run_event(eid, "run-other", "merged") is False


# ----- aliases -----------------------------------------------------------


def test_add_alias_and_resolve(store) -> None:
    from goldenmatch.identity.model import IdentityAlias, IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    store.add_alias(IdentityAlias(
        alias="external:abc", entity_id=eid, kind="external_id",
        dataset="customers",
    ))
    assert store.resolve_alias("external:abc") == eid


def test_resolve_alias_unknown_returns_none(store) -> None:
    assert store.resolve_alias("never-seen") is None


# ----- lifecycle ---------------------------------------------------------


def test_context_manager_closes(monkeypatch) -> None:
    """``with MongoIdentityStore(...)`` closes the client on exit."""
    client = mongomock.MongoClient()
    from goldenmatch.identity.mongo_backend import MongoIdentityStore

    with MongoIdentityStore(client=client, database="gm") as s:
        from goldenmatch.identity.model import IdentityNode
        from goldenmatch.identity.store import new_entity_id
        s.upsert_identity(IdentityNode(entity_id=new_entity_id()))
    # Client passed in is not owned, so the test's outer client stays
    # usable -- no AttributeError on the next call.
    assert client.list_database_names() is not None


def test_index_setup_runs_on_open(store) -> None:
    """First-open creates the indexes that mirror the SQL DDL."""
    # mongomock surfaces indexes via list_indexes(); each collection
    # gets at least the _id index plus our named ones.
    names = {ix["name"] for ix in store._db["identity_nodes"].list_indexes()}
    assert "entity_id_1" in names
    names = {ix["name"] for ix in store._db["evidence_edges"].list_indexes()}
    assert "edges_unique" in names
