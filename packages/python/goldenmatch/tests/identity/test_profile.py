"""Entity profiles + stewardship ops views -- MDM ops (#1114, epic #1108)."""
from __future__ import annotations

import pytest
from goldenmatch.identity import (
    EvidenceEdge,
    IdentityNode,
    IdentityStatus,
    IdentityStore,
    SourceRecord,
    entity_profile,
    identity_summary_stats,
    steward_worklist,
)
from goldenmatch.identity.model import EventKind, IdentityEvent


@pytest.fixture()
def store(tmp_path):
    s = IdentityStore(backend="sqlite", path=str(tmp_path / "id.db"))
    yield s
    s.close()


def _seed(store):
    # E1: two records from two sources, one conflict, mid confidence.
    store.upsert_identity(IdentityNode(entity_id="E1", dataset="d", confidence=0.5))
    store.emit_event(IdentityEvent(entity_id="E1", kind=EventKind.CREATED.value))
    store.upsert_record(SourceRecord("crm:1", "crm", "1", "h1", entity_id="E1", dataset="d"))
    store.upsert_record(SourceRecord("web:9", "web", "9", "h2", entity_id="E1", dataset="d"))
    store.add_edge(EvidenceEdge(
        entity_id="E1", record_a_id="crm:1", record_b_id="web:9",
        kind="conflicts_with", score=0.4, run_name="r1", dataset="d",
    ))
    # E2: one record, high confidence, clean.
    store.upsert_identity(IdentityNode(entity_id="E2", dataset="d", confidence=0.95))
    store.emit_event(IdentityEvent(entity_id="E2", kind=EventKind.CREATED.value))
    store.upsert_record(SourceRecord("crm:2", "crm", "2", "h3", entity_id="E2", dataset="d"))
    # E3: retired (merged into E1) -- no records.
    store.upsert_identity(IdentityNode(
        entity_id="E3", dataset="d", status=IdentityStatus.MERGED_INTO.value,
        merged_into="E1",
    ))


# ── entity_profile ──────────────────────────────────────────────────────────


def test_entity_profile_full(store):
    _seed(store)
    p = entity_profile(store, "E1")
    assert p is not None
    assert p.record_count == 2
    assert p.sources == ["crm", "web"]
    assert p.source_counts == {"crm": 1, "web": 1}
    assert p.conflict_count == 1
    assert p.edge_count == 1
    assert p.confidence == 0.5
    assert p.version == 1  # one CREATED structural event
    assert p.first_seen is not None and p.last_seen is not None


def test_entity_profile_missing(store):
    assert entity_profile(store, "nope") is None


def test_entity_profile_version_counts_structural_events(store):
    _seed(store)
    store.emit_event(IdentityEvent(entity_id="E1", kind=EventKind.ABSORBED_RECORD.value))
    store.emit_event(IdentityEvent(entity_id="E1", kind=EventKind.RETIRED.value))
    # CREATED + ABSORBED_RECORD count; RETIRED does not.
    assert entity_profile(store, "E1").version == 2


def test_entity_profile_as_dict_serializable(store):
    _seed(store)
    import json
    d = entity_profile(store, "E1").as_dict()
    json.dumps(d)  # must not raise (datetimes are isoformatted)
    assert d["entity_id"] == "E1"


# ── identity_summary_stats ──────────────────────────────────────────────────


def test_summary_stats(store):
    _seed(store)
    s = identity_summary_stats(store, dataset="d")
    assert s.total_entities == 3
    assert s.by_status["active"] == 2
    assert s.by_status["merged_into"] == 1
    assert s.total_records == 3            # E1(2) + E2(1); E3 has none
    assert s.singleton_entities == 1       # E2
    assert s.multi_record_entities == 1    # E1
    assert s.records_per_entity_max == 2
    assert s.total_conflicts == 1
    assert s.source_breakdown == {"crm": 2, "web": 1}
    assert s.largest_entities[0] == {"entity_id": "E1", "record_count": 2}


def test_summary_stats_empty(store):
    s = identity_summary_stats(store)
    assert s.total_entities == 0
    assert s.total_records == 0
    assert s.records_per_entity_avg == 0.0
    assert s.largest_entities == []


# ── steward_worklist ────────────────────────────────────────────────────────


def test_worklist_flags_conflicts_and_low_confidence(store):
    _seed(store)
    items = steward_worklist(store, dataset="d", weak_confidence=0.6)
    # E1 has a conflict AND confidence 0.5 (< 0.6); E2 is clean+high -> excluded.
    assert len(items) == 1
    it = items[0]
    assert it.entity_id == "E1"
    assert set(it.reasons) == {"has_conflicts", "low_confidence"}
    assert it.conflict_count == 1


def test_worklist_excludes_healthy(store):
    store.upsert_identity(IdentityNode(entity_id="ok", confidence=0.99))
    store.upsert_record(SourceRecord("s:1", "s", "1", "h", entity_id="ok"))
    assert steward_worklist(store) == []


def test_worklist_sorted_by_priority(store):
    # A: 2 conflicts; B: 1 conflict; C: low confidence only.
    for eid, conf in [("A", 0.9), ("B", 0.9), ("C", 0.4)]:
        store.upsert_identity(IdentityNode(entity_id=eid, confidence=conf))
        store.upsert_record(SourceRecord(f"{eid}:1", "s", "1", eid, entity_id=eid))
    store.add_edge(EvidenceEdge(entity_id="A", record_a_id="A:1", record_b_id="x",
                                kind="conflicts_with", run_name="r1"))
    store.add_edge(EvidenceEdge(entity_id="A", record_a_id="A:1", record_b_id="y",
                                kind="conflicts_with", run_name="r2"))
    store.add_edge(EvidenceEdge(entity_id="B", record_a_id="B:1", record_b_id="z",
                                kind="conflicts_with", run_name="r1"))
    items = steward_worklist(store, weak_confidence=0.6)
    assert [it.entity_id for it in items] == ["A", "B", "C"]
    assert items[0].conflict_count == 2


def test_worklist_limit(store):
    for i in range(5):
        store.upsert_identity(IdentityNode(entity_id=f"E{i}", confidence=0.1))
        store.upsert_record(SourceRecord(f"E{i}:1", "s", "1", "h", entity_id=f"E{i}"))
    assert len(steward_worklist(store, limit=3)) == 3
