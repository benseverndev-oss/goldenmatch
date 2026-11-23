"""Weak brand-new clusters take the bulk fast path, byte-identical to per-row.

A cluster below ``weak_confidence_threshold`` gets a ``conflicts_with`` edge on
its bottleneck pair. That edge used to exist only on the per-row path, so every
weak brand-new cluster fell back to it -- ~95% of clusters on a 14M-record run
with the default threshold. The bulk path now writes the same edge; these tests
lock store contents (down to the edge's negative_evidence and trust) and the
summary counts to the per-row reference, on SQLite and Postgres.
"""
from __future__ import annotations

from collections.abc import Iterator

import polars as pl
import pytest
from goldenmatch.identity import IdentityStore, resolve_clusters


def _df(n: int) -> pl.DataFrame:
    return pl.DataFrame({
        "__row_id__": list(range(n)),
        "__source__": ["src"] * n,
        "unique_id": [f"u{i}" for i in range(n)],
        "name": [f"name-{i}" for i in range(n)],
    })


def _cluster(members, score, bottleneck=None):
    pair_scores = {
        (min(a, b), max(a, b)): score
        for i, a in enumerate(members) for b in members[i + 1:]
    }
    if bottleneck is not None:
        pair_scores[bottleneck] = 0.31
    return {
        "members": list(members), "size": len(members), "oversized": False,
        "pair_scores": pair_scores, "confidence": score,
        "bottleneck_pair": bottleneck, "cluster_quality": "weak",
    }


# Strong, weak (with a bottleneck), weak-but-no-bottleneck, and a weak cluster
# whose bottleneck is reversed relative to the record-id order.
_CLUSTERS = {
    1: _cluster([0, 1, 2], 0.95),
    2: _cluster([3, 4, 5], 0.42, bottleneck=(3, 5)),
    3: _cluster([6, 7], 0.40),
    4: _cluster([12, 9, 10], 0.35, bottleneck=(9, 12)),
}


def _dump(store: IdentityStore) -> dict:
    out = {}
    for node in store.list_identities(limit=10_000):
        recs = store.get_records_for_entity(node.entity_id)
        key = tuple(sorted(r.record_id for r in recs))
        edges = sorted(
            (e.record_a_id, e.record_b_id, e.kind, e.score, e.trust,
             repr(e.negative_evidence), e.actor, e.run_name, e.matchkey_name)
            for e in store.edges_for_entity(node.entity_id)
        )
        out[key] = {
            "golden_record": node.golden_record,
            "confidence": node.confidence,
            "edges": edges,
            "events": sorted((ev.kind, str(ev.payload)) for ev in store.history(node.entity_id)),
        }
    return out


def _resolve(store: IdentityStore, *, bulk: bool, monkeypatch) -> tuple[dict, tuple]:
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_BULK", "1" if bulk else "0")
    if bulk:
        # Every cluster here is brand-new, so the bulk run must not fall back to
        # the per-row writers -- weak ones included. Without this the parity
        # check would pass trivially with weak clusters routed per-row.
        def _per_row_used(*_a, **_k):
            raise AssertionError("bulk run fell back to the per-row write path")

        for name in ("add_edge", "upsert_identity", "upsert_record"):
            monkeypatch.setattr(store, name, _per_row_used)
    summary = resolve_clusters(
        _CLUSTERS, _df(13), [], "mk", store, run_name="r1",
        source_pk_col="unique_id", weak_confidence_threshold=0.6,
    )
    counts = (summary.created, summary.edges_added, summary.conflicts_flagged,
              summary.events_emitted, summary.records_upserted)
    return _dump(store), counts


def _assert_parity(row, bulk) -> None:
    (row_dump, row_counts), (bulk_dump, bulk_counts) = row, bulk
    assert bulk_counts == row_counts
    assert bulk_dump == row_dump
    # Sanity on the reference: two weak clusters with a bottleneck -> 2 conflicts.
    assert row_counts[2] == 2
    conflicts = [e for v in row_dump.values() for e in v["edges"] if e[2] == "conflicts_with"]
    assert len(conflicts) == 2 and all("weak_cluster_bottleneck" in e[5] for e in conflicts)


def test_sqlite_weak_clusters_bulk_matches_row_path(tmp_path, monkeypatch) -> None:
    row_store = IdentityStore(path=str(tmp_path / "row.db"))
    bulk_store = IdentityStore(path=str(tmp_path / "bulk.db"))
    try:
        _assert_parity(
            _resolve(row_store, bulk=False, monkeypatch=monkeypatch),
            _resolve(bulk_store, bulk=True, monkeypatch=monkeypatch),
        )
    finally:
        row_store.close()
        bulk_store.close()


@pytest.fixture
def two_pg_urls() -> Iterator[tuple[str, str]]:
    pytest.importorskip("psycopg")
    testing_pg = pytest.importorskip("testing.postgresql")
    pgs = [testing_pg.Postgresql(), testing_pg.Postgresql()]
    try:
        yield pgs[0].url(), pgs[1].url()
    finally:
        for pg in pgs:
            try:
                pg.stop()
            except Exception:  # Windows SIGINT teardown noise (see CLAUDE.md)
                pass


@pytest.mark.parametrize("initial_load", ["0", "1"])
def test_postgres_weak_clusters_bulk_matches_row_path(
    two_pg_urls, monkeypatch, initial_load: str,
) -> None:
    # initial_load=1 routes bulk edges through the direct-COPY path.
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_INITIAL_LOAD", initial_load)
    row_url, bulk_url = two_pg_urls
    row_store = IdentityStore(backend="postgres", connection=row_url)
    bulk_store = IdentityStore(backend="postgres", connection=bulk_url)
    try:
        _assert_parity(
            _resolve(row_store, bulk=False, monkeypatch=monkeypatch),
            _resolve(bulk_store, bulk=True, monkeypatch=monkeypatch),
        )
    finally:
        row_store.close()
        bulk_store.close()
