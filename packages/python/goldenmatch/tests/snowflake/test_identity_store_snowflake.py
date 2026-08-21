"""IdentityStore(backend="snowflake") against fakesnow (or live -- see conftest)."""
from __future__ import annotations

import pytest

fakesnow = pytest.importorskip("fakesnow")


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


def test_retire_identity_without_merged_into_sets_retired(store) -> None:
    """The plain RETIRED branch -- ``merged_into=None`` must not become
    ``merged_into``, and must clear any prior merge pointer."""
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid, winner = new_entity_id(), new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid, merged_into=winner))
    store.retire_identity(eid)
    node = store.get_identity(eid)
    assert node is not None
    assert node.status == "retired"
    assert node.merged_into is None


def _seed_entity(store):
    from goldenmatch.identity.model import IdentityNode
    from goldenmatch.identity.store import new_entity_id

    eid = new_entity_id()
    store.upsert_identity(IdentityNode(entity_id=eid, dataset="c"))
    return eid


def test_add_edge_is_idempotent_on_replay(store) -> None:
    """The constraint trap: Snowflake does NOT enforce the edge UNIQUE key.

    Replaying a run must not duplicate the edge. A bare INSERT passes on
    SQLite (UNIQUE + INSERT OR IGNORE) and silently duplicates here.
    """
    from goldenmatch.identity.model import EvidenceEdge

    eid = _seed_entity(store)
    edge = EvidenceEdge(
        entity_id=eid, record_a_id="crm:1", record_b_id="crm:2",
        kind="same_as", score=0.95, run_name="run-1",
    )
    store.add_edge(edge)
    store.add_edge(edge)
    assert len(store.edges_for_entity(eid)) == 1


def test_add_edge_separates_kinds_on_the_same_pair(store) -> None:
    from goldenmatch.identity.model import EvidenceEdge

    eid = _seed_entity(store)
    for kind in ("same_as", "conflicts_with"):
        store.add_edge(EvidenceEdge(
            entity_id=eid, record_a_id="crm:1", record_b_id="crm:2",
            kind=kind, run_name="run-1",
        ))
    assert len(store.edges_for_entity(eid)) == 2
    assert len(store.find_conflicts(dataset=None)) == 1


def test_add_edge_canonicalizes_pair_order(store) -> None:
    from goldenmatch.identity.model import EvidenceEdge

    eid = _seed_entity(store)
    store.add_edge(EvidenceEdge(
        entity_id=eid, record_a_id="crm:2", record_b_id="crm:1",
        kind="same_as", run_name="run-1",
    ))
    store.add_edge(EvidenceEdge(
        entity_id=eid, record_a_id="crm:1", record_b_id="crm:2",
        kind="same_as", run_name="run-1",
    ))
    assert len(store.edges_for_entity(eid)) == 1


def test_add_edge_is_idempotent_on_replay_with_null_run_name(store) -> None:
    """Regression guard for the merge_one NULL-key bug this task's MERGE fix
    closes: ``run_name`` defaults to ``None`` on ``EvidenceEdge`` (model.py),
    so an edge with no run_name is the COMMON shape, not an edge case.

    Under the pre-fix ``t.run_name = s.run_name`` MERGE ``ON`` clause, ``NULL
    = NULL`` is not TRUE, so ``WHEN MATCHED`` never fires when run_name is
    unset and a replay silently duplicates the row. The three other
    ``add_edge`` idempotency tests all pass an explicit ``run_name="run-1"``
    and would not have caught this -- this test is what actually guards the
    fix (``IS NOT DISTINCT FROM``) in ``_store_sql.py::merge_one``.
    """
    from goldenmatch.identity.model import EvidenceEdge

    eid = _seed_entity(store)
    edge = EvidenceEdge(
        entity_id=eid, record_a_id="crm:1", record_b_id="crm:2",
        kind="same_as", score=0.95,
    )
    assert edge.run_name is None
    store.add_edge(edge)
    store.add_edge(edge)
    assert len(store.edges_for_entity(eid)) == 1


def test_add_alias_replaces_rather_than_duplicating(store) -> None:
    from goldenmatch.identity.model import IdentityAlias
    from goldenmatch.snowflake._store_sql import fetchone_row

    first, second = _seed_entity(store), _seed_entity(store)
    store.add_alias(IdentityAlias(alias="MDM-1", entity_id=first, kind="mdm"))
    store.add_alias(IdentityAlias(alias="MDM-1", entity_id=second, kind="mdm"))
    assert store.resolve_alias("MDM-1", kind="mdm") == second
    # resolve_alias's fetchone returning `second` is not, on its own, proof of
    # replacement -- duckdb row order for a tie is not guaranteed, so this
    # assertion alone could pass "by luck" even if the MERGE had duplicated
    # the row. Assert the row count directly.
    row = fetchone_row(
        store._sf._conn,
        "SELECT COUNT(*) AS n FROM identity_aliases WHERE alias = %s",
        ("MDM-1",),
    )
    assert row is not None and row["n"] == 1


def test_emit_event_returns_an_id_and_history_reads_back(store) -> None:
    from goldenmatch.identity.model import IdentityEvent

    eid = _seed_entity(store)
    event_id = store.emit_event(IdentityEvent(
        entity_id=eid, kind="created", run_name="run-1",
        payload={"reason": "new cluster"},
    ))
    assert isinstance(event_id, int)
    events = store.history(eid)
    assert [e.kind for e in events] == ["created"]
    assert events[0].payload == {"reason": "new cluster"}


def test_has_run_event_and_run_event_entities(store) -> None:
    from goldenmatch.identity.model import IdentityEvent

    eid = _seed_entity(store)
    store.emit_event(IdentityEvent(
        entity_id=eid, kind="merged", run_name="run-42",
    ))
    assert store.has_run_event(eid, "run-42", "merged") is True
    assert store.has_run_event(eid, "run-42", "split") is False
    assert store.has_run_event(eid, "run-other", "merged") is False
    assert store.run_event_entities("run-42", "merged") == {eid}
