"""identity_summary_stats / steward_worklist use grouped aggregates (#2198).

The SQL fast path (status_counts + active_record_stats) must return exactly what
the per-entity scan fallback returns -- it just gets there in a few GROUP BY
queries instead of one get_records_for_entity per entity.
"""
from __future__ import annotations

import polars as pl
import pytest
from goldenmatch.identity import IdentityStore, resolve_clusters
from goldenmatch.identity.profile import (
    _summary_stats_scan,
    identity_summary_stats,
    steward_worklist,
)


@pytest.fixture()
def store(tmp_path):
    s = IdentityStore(path=str(tmp_path / "identity.db"))
    yield s
    s.close()


def _df(rows):
    out = []
    for i, r in enumerate(rows):
        rec = {"__row_id__": i, "__source__": r.get("src", "a")}
        rec.update({k: v for k, v in r.items() if k != "src"})
        out.append(rec)
    return pl.DataFrame(out)


def _seed(store):
    """One 3-record entity across two sources, one singleton, one dataset."""
    df = _df([
        {"id": "1", "name": "Al", "src": "a"},
        {"id": "2", "name": "Al", "src": "a"},
        {"id": "3", "name": "Al", "src": "b"},
        {"id": "9", "name": "Zo", "src": "a"},
    ])
    clusters = {
        0: {"members": [0, 1, 2], "size": 3, "confidence": 1.0,
            "pair_scores": {(0, 1): 0.9, (0, 2): 0.9, (1, 2): 0.9}},
        1: {"members": [3], "size": 1, "confidence": 1.0, "pair_scores": {}},
    }
    resolve_clusters(clusters, df, [], "mk", store, run_name="r",
                     source_pk_col="id", dataset="d", emit_singletons=True)


def test_fast_path_matches_scan(store):
    _seed(store)
    fast = identity_summary_stats(store, "d")
    scan = _summary_stats_scan(store, "d")
    assert fast.as_dict() == scan.as_dict()


def test_summary_values(store):
    _seed(store)
    s = identity_summary_stats(store, "d")
    assert s.total_entities == 2
    assert s.total_records == 4
    assert s.multi_record_entities == 1
    assert s.singleton_entities == 1
    assert s.records_per_entity_max == 3
    assert s.source_breakdown == {"a": 3, "b": 1}
    assert s.largest_entities[0]["record_count"] == 3


def test_dataset_scoping(store):
    _seed(store)
    # A second dataset the summary must not see (distinct record_id so it does
    # not collide with the seed's records and cross datasets).
    df2 = _df([{"id": "99", "name": "Q", "src": "c"}])
    resolve_clusters(
        {0: {"members": [0], "size": 1, "confidence": 1.0, "pair_scores": {}}},
        df2, [], "mk", store, run_name="r2", source_pk_col="id",
        dataset="other", emit_singletons=True,
    )
    s = identity_summary_stats(store, "d")
    assert s.total_entities == 2          # only dataset "d"
    assert s.total_records == 4


def test_status_counts_and_active_record_stats(store):
    _seed(store)
    assert store.status_counts("d") == {"active": 2}
    per_entity, sources = store.active_record_stats("d")
    assert sorted(per_entity.values()) == [1, 3]
    assert sources == {"a": 3, "b": 1}


def test_steward_worklist_record_count(store):
    """Weak cluster -> a worklist item whose record_count comes from the bulk
    aggregate, matching what a per-entity read would give."""
    df = _df([{"id": "1", "name": "Al"}, {"id": "2", "name": "Bo"}])
    resolve_clusters(
        {0: {"members": [0, 1], "size": 2, "confidence": 0.4,
             "pair_scores": {(0, 1): 0.55}, "bottleneck_pair": (0, 1)}},
        df, [], "mk", store, run_name="r", source_pk_col="id",
        dataset="w", weak_confidence_threshold=0.6, emit_singletons=True,
    )
    wl = steward_worklist(store, "w")
    assert len(wl) == 1
    assert wl[0].conflict_count == 1
    assert wl[0].record_count == 2
