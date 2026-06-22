"""Cross-run entity stabilization -- Identity v3 (#1112, epic #1108)."""
from __future__ import annotations

from datetime import datetime

import pytest
from goldenmatch.identity import (
    EdgeKind,
    EventKind,
    EvidenceEdge,
    IdentityEvent,
    IdentityNode,
    IdentityStatus,
    IdentityStore,
    SourceRecord,
)
from goldenmatch.identity.stabilize import (
    entity_version,
    find_persistent_overlaps,
    stabilize_identities,
)


@pytest.fixture()
def store(tmp_path):
    s = IdentityStore(backend="sqlite", path=str(tmp_path / "id.db"))
    yield s
    s.close()


def _entity(store, eid, records, *, created_at=None):
    now = created_at or datetime.now()
    store.upsert_identity(IdentityNode(
        entity_id=eid, status=IdentityStatus.ACTIVE.value,
        created_at=now, updated_at=now,
    ))
    store.emit_event(IdentityEvent(
        entity_id=eid, kind=EventKind.CREATED.value, run_name="seed",
    ))
    for rid in records:
        store.upsert_record(SourceRecord(
            record_id=rid, source="s", source_pk=rid, record_hash=rid,
            entity_id=eid,
        ))


def _link(store, holder, ra, rb, runs, *, kind=EdgeKind.SAME_AS.value, score=0.8):
    for run in runs:
        store.add_edge(EvidenceEdge(
            entity_id=holder, record_a_id=ra, record_b_id=rb,
            kind=kind, score=score, run_name=run,
        ))


# ── Overlap detection ───────────────────────────────────────────────────────


def test_find_overlap_across_runs(store):
    _entity(store, "A", ["a1"])
    _entity(store, "B", ["b1"])
    _link(store, "A", "a1", "b1", ["run1", "run2", "run3"])
    cands = find_persistent_overlaps(store, min_runs=3)
    assert len(cands) == 1
    c = cands[0]
    assert {c.entity_a, c.entity_b} == {"A", "B"}
    assert c.run_count == 3
    assert c.runs == ["run1", "run2", "run3"]
    assert c.max_score == 0.8


def test_min_runs_gates_overlap(store):
    _entity(store, "A", ["a1"])
    _entity(store, "B", ["b1"])
    _link(store, "A", "a1", "b1", ["run1", "run2"])  # only 2 runs
    assert find_persistent_overlaps(store, min_runs=3) == []
    assert len(find_persistent_overlaps(store, min_runs=2)) == 1


def test_distinct_runs_not_edge_count(store):
    # Many edges but all in ONE run -> not persistent.
    _entity(store, "A", ["a1", "a2"])
    _entity(store, "B", ["b1", "b2"])
    _link(store, "A", "a1", "b1", ["run1"])
    _link(store, "A", "a2", "b2", ["run1"])
    assert find_persistent_overlaps(store, min_runs=2) == []


def test_conflicts_with_edges_excluded(store):
    _entity(store, "A", ["a1"])
    _entity(store, "B", ["b1"])
    _link(store, "A", "a1", "b1", ["r1", "r2", "r3"],
          kind=EdgeKind.CONFLICTS_WITH.value)
    # Negative evidence must not drive consolidation.
    assert find_persistent_overlaps(store, min_runs=3) == []


def test_possible_same_as_counts(store):
    _entity(store, "A", ["a1"])
    _entity(store, "B", ["b1"])
    _link(store, "A", "a1", "b1", ["r1", "r2", "r3"],
          kind=EdgeKind.POSSIBLE_SAME_AS.value)
    assert len(find_persistent_overlaps(store, min_runs=3)) == 1


def test_min_score_filter(store):
    _entity(store, "A", ["a1"])
    _entity(store, "B", ["b1"])
    _link(store, "A", "a1", "b1", ["r1", "r2", "r3"], score=0.4)
    assert find_persistent_overlaps(store, min_runs=3, min_score=0.6) == []
    assert len(find_persistent_overlaps(store, min_runs=3, min_score=0.3)) == 1


# ── Consolidation ───────────────────────────────────────────────────────────


def test_dry_run_does_not_mutate(store):
    _entity(store, "A", ["a1"])
    _entity(store, "B", ["b1"])
    _link(store, "A", "a1", "b1", ["r1", "r2", "r3"])
    report = stabilize_identities(store, min_runs=3, apply=False)
    assert report.applied is False
    assert len(report.consolidations) == 1
    assert report.entities_consolidated == 0
    # Both entities still active.
    assert store.get_identity("A").status == IdentityStatus.ACTIVE.value
    assert store.get_identity("B").status == IdentityStatus.ACTIVE.value


def test_apply_consolidates_most_records(store):
    _entity(store, "A", ["a1", "a2"])   # more records -> winner
    _entity(store, "B", ["b1"])
    _link(store, "A", "a1", "b1", ["r1", "r2", "r3"])
    report = stabilize_identities(store, min_runs=3, winner_strategy="most_records",
                                  apply=True)
    assert report.entities_consolidated == 1
    g = report.consolidations[0]
    assert g.winner == "A"
    assert g.absorbed == ["B"]
    # B retired into A; B's record now points at A.
    b = store.get_identity("B")
    assert b.status == IdentityStatus.MERGED_INTO.value
    assert b.merged_into == "A"
    assert store.find_entity_by_record("b1") == "A"
    assert {r.record_id for r in store.get_records_for_entity("A")} == {"a1", "a2", "b1"}


def test_winner_strategy_lowest_id(store):
    _entity(store, "Z", ["z1", "z2"])
    _entity(store, "A", ["a1"])
    _link(store, "Z", "z1", "a1", ["r1", "r2", "r3"])
    report = stabilize_identities(store, min_runs=3, winner_strategy="lowest_id",
                                  apply=True)
    assert report.consolidations[0].winner == "A"  # 'A' < 'Z'


def test_winner_strategy_oldest(store):
    _entity(store, "new", ["n1", "n2", "n3"], created_at=datetime(2026, 6, 1))
    _entity(store, "old", ["o1"], created_at=datetime(2020, 1, 1))
    _link(store, "new", "n1", "o1", ["r1", "r2", "r3"])
    report = stabilize_identities(store, min_runs=3, winner_strategy="oldest",
                                  apply=True)
    # 'old' wins on age despite having fewer records.
    assert report.consolidations[0].winner == "old"


def test_chain_consolidates_into_one_component(store):
    _entity(store, "A", ["a1"])
    _entity(store, "B", ["b1"])
    _entity(store, "C", ["c1"])
    _link(store, "A", "a1", "b1", ["r1", "r2", "r3"])  # A-B
    _link(store, "B", "b1", "c1", ["r1", "r2", "r3"])  # B-C
    report = stabilize_identities(store, min_runs=3, apply=True)
    assert len(report.consolidations) == 1
    g = report.consolidations[0]
    assert g.size == 3
    assert {g.winner, *g.absorbed} == {"A", "B", "C"}
    assert report.entities_consolidated == 2


def test_apply_is_idempotent(store):
    _entity(store, "A", ["a1", "a2"])
    _entity(store, "B", ["b1"])
    _link(store, "A", "a1", "b1", ["r1", "r2", "r3"])
    stabilize_identities(store, min_runs=3, apply=True)
    # Second pass: B is retired, no active overlap remains.
    report2 = stabilize_identities(store, min_runs=3, apply=True)
    assert report2.candidates == []
    assert report2.entities_consolidated == 0


def test_invalid_winner_strategy_raises(store):
    with pytest.raises(ValueError, match="winner_strategy"):
        stabilize_identities(store, winner_strategy="bogus")


def test_config_defaults(store):
    from goldenmatch.config.schemas import StabilizationConfig

    _entity(store, "A", ["a1", "a2"])
    _entity(store, "B", ["b1"])
    _link(store, "A", "a1", "b1", ["r1", "r2"])  # only 2 runs
    cfg = StabilizationConfig(min_runs=2, winner_strategy="lowest_id")
    report = stabilize_identities(store, config=cfg, apply=False)
    # config.min_runs=2 makes the 2-run overlap qualify.
    assert len(report.consolidations) == 1
    assert report.consolidations[0].winner == "A"
    assert report.consolidations[0].strategy == "lowest_id"


# ── Versioning ──────────────────────────────────────────────────────────────


def test_entity_version_increments_on_consolidation(store):
    _entity(store, "A", ["a1", "a2"])   # version 1 (CREATED)
    _entity(store, "B", ["b1"])
    assert entity_version(store, "A") == 1
    _link(store, "A", "a1", "b1", ["r1", "r2", "r3"])
    stabilize_identities(store, min_runs=3, apply=True)
    # A gained a CONSOLIDATED event -> version 2.
    assert entity_version(store, "A") == 2
