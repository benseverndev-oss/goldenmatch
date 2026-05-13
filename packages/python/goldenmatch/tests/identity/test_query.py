"""Tests for goldenmatch.identity.query."""
from __future__ import annotations

import pytest
from goldenmatch.identity import (
    IdentityNode,
    IdentityStore,
    SourceRecord,
    find_by_record,
    find_conflicts,
    get_entity,
    history,
    list_entities,
    manual_merge,
    manual_split,
    new_entity_id,
)
from goldenmatch.identity.model import EdgeKind, EventKind, EvidenceEdge


@pytest.fixture()
def store(tmp_path):
    s = IdentityStore(path=str(tmp_path / "q.db"))
    yield s
    s.close()


_RID_COUNTER = [0]


def _seed_entity_with_records(s, n=2, dataset="d"):
    eid = new_entity_id()
    s.upsert_identity(IdentityNode(entity_id=eid, dataset=dataset, confidence=0.9))
    base = _RID_COUNTER[0]
    _RID_COUNTER[0] += n
    for i in range(n):
        rid = base + i
        s.upsert_record(SourceRecord(
            record_id=f"src:{rid}", source="src", source_pk=str(rid),
            record_hash=f"h{rid}", entity_id=eid, dataset=dataset,
            payload={"v": rid},
        ))
    return eid


def test_get_entity_returns_view(store):
    eid = _seed_entity_with_records(store, n=3)
    store.add_edge(EvidenceEdge(entity_id=eid, record_a_id="src:0", record_b_id="src:1", run_name="r"))
    view = get_entity(store, eid)
    assert view is not None
    assert view.node.entity_id == eid
    assert len(view.records) == 3
    assert len(view.edges) == 1


def test_get_entity_unknown(store):
    assert get_entity(store, "nope") is None


def test_find_by_record(store):
    _RID_COUNTER[0] = 0
    eid = _seed_entity_with_records(store)
    view = find_by_record(store, "src:1")
    assert view is not None and view.node.entity_id == eid
    assert find_by_record(store, "src:404") is None


def test_list_entities_pagination(store):
    for _ in range(7):
        _seed_entity_with_records(store, n=1, dataset="d1")
    assert len(list_entities(store, dataset="d1")) == 7
    page = list_entities(store, dataset="d1", limit=3)
    assert len(page) == 3


def test_history_serialized(store):
    eid = _seed_entity_with_records(store)
    from goldenmatch.identity.model import IdentityEvent
    store.emit_event(IdentityEvent(entity_id=eid, kind=EventKind.CREATED.value, run_name="r"))
    out = history(store, eid)
    assert out and out[0]["kind"] == EventKind.CREATED.value


def test_find_conflicts(store):
    eid = _seed_entity_with_records(store)
    store.add_edge(EvidenceEdge(
        entity_id=eid, record_a_id="src:0", record_b_id="src:1",
        kind=EdgeKind.CONFLICTS_WITH.value, dataset="d", run_name="r",
    ))
    out = find_conflicts(store, dataset="d")
    assert len(out) == 1


def test_manual_merge(store):
    a = _seed_entity_with_records(store, n=2, dataset="d")
    b = _seed_entity_with_records(store, n=2, dataset="d")
    out = manual_merge(store, keep_entity_id=a, absorb_entity_id=b, reason="dup")
    assert out["keep"] == a and out["absorbed"] == b
    # All b's records now point to a
    recs_b_old = store.get_records_for_entity(b)
    assert len(recs_b_old) == 0
    recs_a = store.get_records_for_entity(a)
    assert len(recs_a) == 4
    # Loser status flipped
    assert store.get_identity(b).status == "merged_into"


def test_manual_split(store):
    eid = _seed_entity_with_records(store, n=4, dataset="d")
    rec_ids = [r.record_id for r in store.get_records_for_entity(eid)]
    out = manual_split(store, eid, rec_ids[2:], reason="bad merge")
    assert len(out["moved"]) == 2
    assert len(store.get_records_for_entity(eid)) == 2
    assert len(store.get_records_for_entity(out["new_entity_id"])) == 2


def test_manual_merge_validates(store):
    a = _seed_entity_with_records(store)
    with pytest.raises(ValueError):
        manual_merge(store, a, "missing")
    with pytest.raises(ValueError):
        manual_merge(store, "missing", a)


def test_view_serialization(store):
    eid = _seed_entity_with_records(store)
    view = get_entity(store, eid)
    d = view.to_dict()
    assert d["entity_id"] == eid
    assert "records" in d and "events" in d and "edges" in d
