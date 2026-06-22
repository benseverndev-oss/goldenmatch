"""Conflict mediation workflow -- Identity v3 (#1113, epic #1108)."""
from __future__ import annotations

import pytest
from goldenmatch.identity import (
    EdgeKind,
    EvidenceEdge,
    IdentityNode,
    IdentityStatus,
    IdentityStore,
    SourceRecord,
)
from goldenmatch.identity.mediation import (
    ConflictResolution,
    mediate_conflict,
    mediation_summary,
    open_conflicts,
    pair_verdict,
)


@pytest.fixture()
def store(tmp_path):
    s = IdentityStore(backend="sqlite", path=str(tmp_path / "id.db"))
    yield s
    s.close()


def _entity_with_conflict(store, eid="E", ra="r:a", rb="r:b", *, run="run1"):
    """One entity holding two records flagged as a weak (conflicts_with) pair."""
    store.upsert_identity(IdentityNode(
        entity_id=eid, status=IdentityStatus.ACTIVE.value,
    ))
    for rid in (ra, rb):
        store.upsert_record(SourceRecord(
            record_id=rid, source="s", source_pk=rid, record_hash=rid,
            entity_id=eid,
        ))
    store.add_edge(EvidenceEdge(
        entity_id=eid, record_a_id=ra, record_b_id=rb,
        kind=EdgeKind.CONFLICTS_WITH.value, score=0.4,
        negative_evidence={"reason": "weak_cluster_bottleneck"},
        run_name=run,
    ))
    return eid, ra, rb


# ── Queue ───────────────────────────────────────────────────────────────────


def test_open_conflicts_lists_unmediated(store):
    eid, ra, rb = _entity_with_conflict(store)
    items = open_conflicts(store)
    assert len(items) == 1
    it = items[0]
    assert {it.record_a_id, it.record_b_id} == {ra, rb}
    assert it.entity_id == eid
    assert it.reason == "weak_cluster_bottleneck"
    assert it.deferred is False


def test_open_conflicts_dedups_pair_across_runs(store):
    eid, ra, rb = _entity_with_conflict(store, run="run1")
    # same pair flagged again in a later run
    store.add_edge(EvidenceEdge(
        entity_id=eid, record_a_id=ra, record_b_id=rb,
        kind=EdgeKind.CONFLICTS_WITH.value, score=0.3, run_name="run2",
    ))
    assert len(open_conflicts(store)) == 1  # one item per canonical pair


# ── Adjudicate: same / distinct / defer ─────────────────────────────────────


def test_mediate_same_closes_without_split(store):
    eid, ra, rb = _entity_with_conflict(store)
    out = mediate_conflict(store, ra, rb, "same", steward="ben")
    assert out["resolution"] == "same"
    assert out["action"]["type"] == "none"
    # Conflict no longer open; both records still in the same entity.
    assert open_conflicts(store) == []
    assert store.find_entity_by_record(ra) == eid
    assert store.find_entity_by_record(rb) == eid
    assert pair_verdict(store, ra, rb) == ConflictResolution.SAME


def test_mediate_distinct_splits_record_out(store):
    eid, ra, rb = _entity_with_conflict(store)
    out = mediate_conflict(store, ra, rb, "distinct", reason="different people")
    assert out["action"]["type"] == "split"
    new_eid = out["action"]["new_entity_id"]
    # rb moved to a new identity; ra stays.
    assert store.find_entity_by_record(rb) == new_eid
    assert store.find_entity_by_record(ra) == eid
    assert new_eid != eid
    # conflict closed.
    assert open_conflicts(store) == []
    assert pair_verdict(store, ra, rb) == ConflictResolution.DISTINCT


def test_mediate_distinct_no_apply_records_verdict_only(store):
    eid, ra, rb = _entity_with_conflict(store)
    out = mediate_conflict(store, ra, rb, "distinct", apply=False)
    assert out["action"]["type"] == "none"
    # No split happened, but verdict recorded -> conflict closed from the queue.
    assert store.find_entity_by_record(rb) == eid
    assert pair_verdict(store, ra, rb) == ConflictResolution.DISTINCT
    assert open_conflicts(store) == []


def test_mediate_defer_keeps_open(store):
    eid, ra, rb = _entity_with_conflict(store)
    mediate_conflict(store, ra, rb, "defer", reason="need source check")
    assert pair_verdict(store, ra, rb) == ConflictResolution.DEFER
    items = open_conflicts(store)
    assert len(items) == 1
    assert items[0].deferred is True
    # ...and hidden when deferred are excluded.
    assert open_conflicts(store, include_deferred=False) == []


def test_invalid_resolution_raises(store):
    _entity_with_conflict(store)
    with pytest.raises(ValueError, match="Invalid resolution"):
        mediate_conflict(store, "r:a", "r:b", "bogus")


def test_remediation_latest_verdict_wins(store):
    eid, ra, rb = _entity_with_conflict(store)
    mediate_conflict(store, ra, rb, "defer")
    assert pair_verdict(store, ra, rb) == ConflictResolution.DEFER
    # Steward changes their mind.
    mediate_conflict(store, ra, rb, "same")
    assert pair_verdict(store, ra, rb) == ConflictResolution.SAME
    assert open_conflicts(store) == []


# ── Audit / summary ─────────────────────────────────────────────────────────


def test_mediation_summary_counts(store):
    _entity_with_conflict(store, eid="E1", ra="a1", rb="b1")
    _entity_with_conflict(store, eid="E2", ra="a2", rb="b2")
    _entity_with_conflict(store, eid="E3", ra="a3", rb="b3")
    mediate_conflict(store, "a1", "b1", "same")
    mediate_conflict(store, "a2", "b2", "defer")
    summary = mediation_summary(store)
    assert summary["total"] == 3
    assert summary["resolved_same"] == 1
    assert summary["deferred"] == 1
    # open = unmediated (E3) + deferred (E2).
    assert summary["open"] == 2


def test_mediation_emits_audit_event(store):
    eid, ra, rb = _entity_with_conflict(store)
    mediate_conflict(store, ra, rb, "same", steward="ben", reason="same person")
    kinds = [e.kind for e in store.history(eid)]
    assert "conflict_mediated" in kinds
