"""IdentityStore unit tests."""
from __future__ import annotations

import pytest
from goldenmatch.identity import (
    EdgeKind,
    EventKind,
    EvidenceEdge,
    IdentityAlias,
    IdentityEvent,
    IdentityNode,
    IdentityStatus,
    IdentityStore,
    SourceRecord,
    new_entity_id,
)


@pytest.fixture()
def store(tmp_path):
    path = str(tmp_path / "identity.db")
    s = IdentityStore(backend="sqlite", path=path)
    yield s
    s.close()


def test_new_entity_id_is_unique_uuid_string():
    ids = {new_entity_id() for _ in range(100)}
    assert len(ids) == 100
    # UUID string shape: 36 chars, 4 dashes
    for eid in ids:
        assert len(eid) == 36 and eid.count("-") == 4


def test_identity_upsert_and_get(store: IdentityStore):
    eid = new_entity_id()
    node = IdentityNode(
        entity_id=eid,
        dataset="t",
        golden_record={"name": "Alice"},
        confidence=0.9,
    )
    store.upsert_identity(node)
    fetched = store.get_identity(eid)
    assert fetched is not None
    assert fetched.entity_id == eid
    assert fetched.dataset == "t"
    assert fetched.golden_record == {"name": "Alice"}
    assert fetched.status == IdentityStatus.ACTIVE.value


def test_identity_upsert_overwrites(store: IdentityStore):
    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid, golden_record={"v": 1}))
    store.upsert_identity(IdentityNode(entity_id=eid, golden_record={"v": 2}))
    fetched = store.get_identity(eid)
    assert fetched and fetched.golden_record == {"v": 2}


def test_source_record_roundtrip(store: IdentityStore):
    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    rec = SourceRecord(
        record_id="src:1",
        source="src",
        source_pk="1",
        record_hash="h1",
        entity_id=eid,
        payload={"k": "v"},
        dataset="t",
    )
    store.upsert_record(rec)
    fetched = store.get_record("src:1")
    assert fetched is not None
    assert fetched.entity_id == eid
    assert fetched.payload == {"k": "v"}
    assert store.find_entity_by_record("src:1") == eid


def test_lookup_entity_ids_bulk(store: IdentityStore):
    e1, e2 = new_entity_id(), new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=e1))
    store.upsert_identity(IdentityNode(entity_id=e2))
    store.upsert_record(SourceRecord("a:1", "a", "1", "h", entity_id=e1))
    store.upsert_record(SourceRecord("a:2", "a", "2", "h", entity_id=e2))
    store.upsert_record(SourceRecord("a:3", "a", "3", "h"))  # no entity

    out = store.lookup_entity_ids(["a:1", "a:2", "a:3", "a:404"])
    assert out == {"a:1": e1, "a:2": e2}


def test_lookup_entity_ids_chunks_beyond_sqlite_var_limit(store: IdentityStore):
    # #670: a single IN-list over >999 ids raised sqlite3 "too many SQL
    # variables". lookup_entity_ids must chunk the IN-list. Seed 2500 records
    # (spanning multiple 900-id chunks) and look them all up in one call.
    n = 2500
    e = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=e))
    for i in range(n):
        store.upsert_record(SourceRecord(f"a:{i}", "a", str(i), "h", entity_id=e))
    out = store.lookup_entity_ids([f"a:{i}" for i in range(n)] + ["a:missing"])
    assert out == {f"a:{i}": e for i in range(n)}


def test_records_for_entity(store: IdentityStore):
    e = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=e))
    for i in range(3):
        store.upsert_record(SourceRecord(f"s:{i}", "s", str(i), "h", entity_id=e))
    recs = store.get_records_for_entity(e)
    assert {r.record_id for r in recs} == {"s:0", "s:1", "s:2"}


def test_edge_canonicalization(store: IdentityStore):
    e = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=e))
    edge = EvidenceEdge(
        entity_id=e,
        record_a_id="z:9",
        record_b_id="a:1",
        score=0.92,
        matchkey_name="weighted_default",
        field_scores={"name": 0.95, "email": 1.0},
        run_name="run-1",
    )
    store.add_edge(edge)
    edges = store.edges_for_entity(e)
    assert len(edges) == 1
    # Canonical ordering: a:1 < z:9
    assert edges[0].record_a_id == "a:1"
    assert edges[0].record_b_id == "z:9"
    assert edges[0].field_scores == {"name": 0.95, "email": 1.0}


def test_edge_dedup_by_run(store: IdentityStore):
    e = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=e))
    edge = EvidenceEdge(entity_id=e, record_a_id="a:1", record_b_id="a:2", run_name="r1")
    store.add_edge(edge)
    store.add_edge(edge)  # exact duplicate -> ignored by UNIQUE constraint
    assert len(store.edges_for_entity(e)) == 1
    # Same pair, new run -> new row
    edge2 = EvidenceEdge(entity_id=e, record_a_id="a:1", record_b_id="a:2", run_name="r2")
    store.add_edge(edge2)
    assert len(store.edges_for_entity(e)) == 2


def test_events_history(store: IdentityStore):
    e = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=e))
    store.emit_event(IdentityEvent(entity_id=e, kind=EventKind.CREATED.value, run_name="r1"))
    store.emit_event(IdentityEvent(
        entity_id=e, kind=EventKind.ABSORBED_RECORD.value,
        payload={"record_id": "s:1"}, run_name="r1",
    ))
    history = store.history(e)
    assert len(history) == 2
    assert history[0].kind == EventKind.CREATED.value
    assert history[1].payload == {"record_id": "s:1"}
    assert store.has_run_event(e, "r1", EventKind.CREATED.value) is True
    assert store.has_run_event(e, "r999", EventKind.CREATED.value) is False


def test_retire_identity_with_merge(store: IdentityStore):
    a, b = new_entity_id(), new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=a))
    store.upsert_identity(IdentityNode(entity_id=b))
    store.retire_identity(a, merged_into=b)
    fetched = store.get_identity(a)
    assert fetched is not None
    assert fetched.status == IdentityStatus.MERGED_INTO.value
    assert fetched.merged_into == b


def test_find_conflicts(store: IdentityStore):
    e = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=e, dataset="d1"))
    store.add_edge(EvidenceEdge(
        entity_id=e, record_a_id="a:1", record_b_id="a:2",
        kind=EdgeKind.CONFLICTS_WITH.value, dataset="d1", run_name="r",
    ))
    store.add_edge(EvidenceEdge(
        entity_id=e, record_a_id="a:3", record_b_id="a:4",
        kind=EdgeKind.SAME_AS.value, dataset="d1", run_name="r",
    ))
    conflicts = store.find_conflicts(dataset="d1")
    assert len(conflicts) == 1
    assert conflicts[0].kind == EdgeKind.CONFLICTS_WITH.value


def test_list_and_count(store: IdentityStore):
    for i in range(5):
        store.upsert_identity(IdentityNode(entity_id=new_entity_id(), dataset="d1"))
    for i in range(2):
        store.upsert_identity(IdentityNode(entity_id=new_entity_id(), dataset="d2"))
    assert store.count_identities() == 7
    assert store.count_identities(dataset="d1") == 5
    listed = store.list_identities(dataset="d1", limit=3)
    assert len(listed) == 3


def test_alias_roundtrip(store: IdentityStore):
    e = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=e))
    store.add_alias(IdentityAlias(alias="sf:003abc", entity_id=e, kind="external_id"))
    assert store.resolve_alias("sf:003abc") == e
    assert store.resolve_alias("missing") is None


def test_migration_idempotent(tmp_path):
    path = str(tmp_path / "m.db")
    s1 = IdentityStore(path=path)
    eid = new_entity_id()
    s1.upsert_identity(IdentityNode(entity_id=eid))
    s1.close()
    # Reopen -- schema should not be re-created destructively
    s2 = IdentityStore(path=path)
    assert s2.get_identity(eid) is not None
    s2.close()


def test_context_manager(tmp_path):
    path = str(tmp_path / "ctx.db")
    with IdentityStore(path=path) as s:
        s.upsert_identity(IdentityNode(entity_id=new_entity_id()))
        assert s.count_identities() == 1
