"""IdentityStore(backend="snowflake") against fakesnow."""
from __future__ import annotations

import pytest

fakesnow = pytest.importorskip("fakesnow")
import snowflake.connector  # noqa: E402


@pytest.fixture
def store():
    from goldenmatch.identity.store import IdentityStore

    with fakesnow.patch():
        conn = snowflake.connector.connect(database="GM", schema="PUB")
        s = IdentityStore(
            backend="snowflake", connection=conn, database="GM", schema="PUB"
        )
        yield s
        s.close()


def test_upsert_identity_and_get(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(
        entity_id=eid, dataset="customers", status="active", confidence=0.99,
    ))
    node = store.get_identity(eid)
    assert node is not None
    assert node.entity_id == eid
    assert node.dataset == "customers"
    assert node.confidence == 0.99


def test_upsert_identity_is_idempotent(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    node = IdentityNode(entity_id=new_entity_id(), dataset="c", status="active")
    store.upsert_identity(node)
    store.upsert_identity(node)
    assert store.count_identities() == 1


def test_golden_record_round_trips(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(
        entity_id=eid, golden_record={"name": "Ada", "score": 1.5},
    ))
    node = store.get_identity(eid)
    assert node is not None
    assert node.golden_record == {"name": "Ada", "score": 1.5}


def test_upsert_record_and_lookup(store) -> None:
    from goldenmatch.identity.model import IdentityNode, SourceRecord
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid))
    store.upsert_record(SourceRecord(
        record_id="crm:1", source="crm", source_pk="1",
        record_hash="h1", entity_id=eid, payload={"email": "a@b.c"},
    ))
    rec = store.get_record("crm:1")
    assert rec is not None
    assert rec.entity_id == eid
    assert rec.payload == {"email": "a@b.c"}
    assert store.find_entity_by_record("crm:1") == eid
    assert store.lookup_entity_ids(["crm:1", "crm:missing"]) == {"crm:1": eid}


def test_get_identities_batches(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    ids = [new_entity_id() for _ in range(3)]
    for eid in ids:
        store.upsert_identity(IdentityNode(entity_id=eid, dataset="c"))
    got = store.get_identities(ids)
    assert set(got) == set(ids)


def test_list_and_count_filter_by_dataset(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    for ds in ("customers", "customers", "vendors"):
        store.upsert_identity(IdentityNode(entity_id=new_entity_id(), dataset=ds))
    assert store.count_identities(dataset="customers") == 2
    assert len(store.list_identities(dataset="customers")) == 2


def test_retire_identity_sets_status_and_merged_into(store) -> None:
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    loser, winner = new_entity_id(), new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=loser))
    store.upsert_identity(IdentityNode(entity_id=winner))
    store.retire_identity(loser, merged_into=winner, run_name="r1")
    node = store.get_identity(loser)
    assert node is not None
    assert node.status == "merged_into"
    assert node.merged_into == winner
