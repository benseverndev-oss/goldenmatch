"""Warm-store re-resolve must not duplicate evidence edges / conflicts (#2197).

``run_name`` is part of the evidence_edges unique key (the audit spine), so a
re-resolve of an UNCHANGED cluster used to append a whole second copy of its
edges under the new run_name -- evidence_edges and conflicts doubled on every
run while identity_nodes / identity_events stayed idempotent. Edge + conflict
writes are now gated on actual change, mirroring the event layer: a no-op
re-resolve writes nothing, and an absorb writes only the newly-absorbed
records' edges.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.identity import IdentityStore, resolve_clusters
from goldenmatch.identity.model import EdgeKind


@pytest.fixture()
def store(tmp_path):
    s = IdentityStore(path=str(tmp_path / "identity.db"))
    yield s
    s.close()


def _df(rows):
    out = []
    for i, r in enumerate(rows):
        rec = {"__row_id__": i, "__source__": "src"}
        rec.update(r)
        out.append(rec)
    return pl.DataFrame(out)


def _edges(store, eid):
    return store.edges_for_entity(eid)


def _kind(edges, kind):
    return [e for e in edges if e.kind == kind]


def _resolve(store, df, clusters, run_name, **kw):
    return resolve_clusters(
        clusters, df, [], "mk", store,
        run_name=run_name, source_pk_col="id", **kw,
    )


# ── identical re-resolve ────────────────────────────────────────────────────

def test_reresolve_identical_cluster_adds_no_edges(store):
    """Same 3-record cluster resolved twice -> the edge set is unchanged."""
    df = _df([{"id": "1", "name": "Al"}, {"id": "2", "name": "Al"},
              {"id": "3", "name": "Al"}])
    clusters = {0: {"members": [0, 1, 2], "size": 3, "confidence": 1.0,
                    "pair_scores": {(0, 1): 0.99, (0, 2): 0.99, (1, 2): 0.99}}}

    s1 = _resolve(store, df, clusters, "run1")
    eid = store.find_entity_by_record("src:1")
    edges_after_1 = _kind(_edges(store, eid), EdgeKind.SAME_AS.value)
    assert s1.edges_added == 3          # 3-choose-2 pairs
    assert len(edges_after_1) == 3

    s2 = _resolve(store, df, clusters, "run2")
    edges_after_2 = _kind(_edges(store, eid), EdgeKind.SAME_AS.value)
    assert s2.edges_added == 0          # nothing changed -> nothing written
    assert len(edges_after_2) == 3      # NOT 6

    # and it stays flat on a third run
    _resolve(store, df, clusters, "run3")
    assert len(_kind(_edges(store, eid), EdgeKind.SAME_AS.value)) == 3


def test_reresolve_preserves_entity_and_records(store):
    """The entity_id and record membership are unchanged across re-resolve."""
    df = _df([{"id": "1", "name": "Al"}, {"id": "2", "name": "Al"}])
    clusters = {0: {"members": [0, 1], "size": 2, "confidence": 1.0,
                    "pair_scores": {(0, 1): 0.99}}}
    _resolve(store, df, clusters, "run1")
    eid = store.find_entity_by_record("src:1")
    before = store.count_identities()

    _resolve(store, df, clusters, "run2")
    assert store.find_entity_by_record("src:1") == eid      # stable id
    assert store.find_entity_by_record("src:2") == eid
    assert store.count_identities() == before               # no new entities


# ── absorb delta writes only the new records' edges ─────────────────────────

def test_absorb_writes_only_new_record_edges(store):
    """Seed {1,2}; re-resolve {1,2,3}. Only edges touching record 3 are added;
    the pre-existing 1-2 edge is NOT re-written."""
    df1 = _df([{"id": "1", "name": "Al"}, {"id": "2", "name": "Al"}])
    _resolve(store, df1,
             {0: {"members": [0, 1], "size": 2, "confidence": 1.0,
                  "pair_scores": {(0, 1): 0.99}}},
             "run1")
    eid = store.find_entity_by_record("src:1")
    assert len(_kind(_edges(store, eid), EdgeKind.SAME_AS.value)) == 1

    df2 = _df([{"id": "1", "name": "Al"}, {"id": "2", "name": "Al"},
               {"id": "3", "name": "Al"}])
    s2 = _resolve(store, df2,
                  {0: {"members": [0, 1, 2], "size": 3, "confidence": 1.0,
                       "pair_scores": {(0, 1): 0.99, (0, 2): 0.99, (1, 2): 0.99}}},
                  "run2")

    assert s2.absorbed_records == 1                 # record 3 absorbed
    assert s2.edges_added == 2                       # 1-3 and 2-3 only, NOT 1-2
    same = _kind(_edges(store, eid), EdgeKind.SAME_AS.value)
    assert len(same) == 3                            # 1-2 (kept) + 1-3 + 2-3
    pairs = {frozenset((e.record_a_id, e.record_b_id)) for e in same}
    assert frozenset(("src:1", "src:2")) in pairs
    assert frozenset(("src:1", "src:3")) in pairs
    assert frozenset(("src:2", "src:3")) in pairs


# ── conflicts do not double either ──────────────────────────────────────────

def test_reresolve_does_not_reflag_conflicts(store):
    """A weak cluster flags one conflicts_with edge; re-resolving the unchanged
    cluster must not flag it again."""
    df = _df([{"id": "1", "name": "Al"}, {"id": "2", "name": "Bo"}])
    clusters = {0: {"members": [0, 1], "size": 2, "confidence": 0.4,
                    "pair_scores": {(0, 1): 0.55}, "bottleneck_pair": (0, 1)}}

    s1 = _resolve(store, df, clusters, "run1", weak_confidence_threshold=0.6)
    assert s1.conflicts_flagged == 1
    assert len(store.find_conflicts()) == 1

    s2 = _resolve(store, df, clusters, "run2", weak_confidence_threshold=0.6)
    assert s2.conflicts_flagged == 0
    assert len(store.find_conflicts()) == 1          # NOT 2
