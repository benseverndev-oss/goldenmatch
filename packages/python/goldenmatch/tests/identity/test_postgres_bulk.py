"""Postgres-backend tests for IdentityStore: psycopg3 + bulk COPY."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime  # noqa: F401  -- datetime used by tests below
from pathlib import Path

import pytest

psycopg = pytest.importorskip("psycopg")
testing_pg = pytest.importorskip("testing.postgresql")


@pytest.fixture
def pg_url() -> Iterator[str]:
    """Yield a fresh testing.postgresql URL, suppressing the Windows
    SIGINT-teardown failure that otherwise marks the test as errored.

    The teardown noise is documented as harmless in CLAUDE.md; this fixture
    just keeps it from failing the test.
    """
    pg = testing_pg.Postgresql()
    try:
        yield pg.url()
    finally:
        try:
            pg.stop()
        except Exception:
            # Windows: testing.common.database send_signal(SIGINT) raises
            # ValueError("Unsupported signal: 2"). Process dies on GC anyway.
            pass


def test_postgres_backend_opens_via_psycopg3(pg_url: str) -> None:
    from goldenmatch.identity.store import IdentityStore

    store = IdentityStore(backend="postgres", connection=pg_url)
    # psycopg3 connection has .info.dsn; psycopg2 has .dsn
    assert hasattr(store._conn, "info")
    store.close()


def test_bulk_upsert_identities_inserts_then_conflict_updates(pg_url: str) -> None:
    import polars as pl
    from goldenmatch.identity.store import IdentityStore

    store = IdentityStore(backend="postgres", connection=pg_url)
    now = datetime.now(UTC)
    df1 = pl.DataFrame(
        {
            "entity_id": ["e1", "e2", "e3"],
            "status": ["active"] * 3,
            "merged_into": [None, None, None],
            "dataset": ["d"] * 3,
            "created_at": [now] * 3,
            "updated_at": [now] * 3,
        }
    )
    store.bulk_upsert_identities(df1)
    assert store.count_identities() == 3

    df2 = pl.DataFrame(
        {
            "entity_id": ["e2", "e4"],
            "status": ["retired", "active"],
            "merged_into": [None, None],
            "dataset": ["d"] * 2,
            "created_at": [now] * 2,
            "updated_at": [now] * 2,
        }
    )
    store.bulk_upsert_identities(df2)
    assert store.count_identities() == 4
    node = store.get_identity("e2")
    assert node is not None
    assert node.status == "retired"
    store.close()


def test_bulk_upsert_identities_raises_on_sqlite(tmp_path: Path) -> None:
    import polars as pl
    from goldenmatch.identity.store import IdentityStore

    store = IdentityStore(backend="sqlite", path=str(tmp_path / "id.db"))
    df = pl.DataFrame({"entity_id": ["e1"]})
    with pytest.raises(NotImplementedError, match="bulk_upsert_identities"):
        store.bulk_upsert_identities(df)
    store.close()


def test_bulk_upsert_identities_preserves_golden_record_and_confidence(
    pg_url: str,
) -> None:
    """The Phase 6 bulk method must round-trip ``golden_record`` (JSONB)
    and ``confidence`` (DOUBLE) just like the per-row ``upsert_identity``
    path. Earlier revisions dropped both, which would have silently
    lost cluster data when `resolve_clusters` went through the bulk
    fast-path. Closes the correctness gap in #368's follow-up."""
    import json

    import polars as pl
    from goldenmatch.identity.store import IdentityStore

    store = IdentityStore(backend="postgres", connection=pg_url)
    now = datetime.now(UTC)
    golden = {"name": "Alice", "email": "alice@example.com"}
    df = pl.DataFrame(
        {
            "entity_id": ["e_golden"],
            "status": ["active"],
            "merged_into": [None],
            "golden_record": [json.dumps(golden)],
            "confidence": [0.95],
            "dataset": ["d"],
            "created_at": [now],
            "updated_at": [now],
        }
    )
    store.bulk_upsert_identities(df)
    node = store.get_identity("e_golden")
    assert node is not None
    assert node.golden_record == golden
    assert node.confidence is not None
    assert abs(node.confidence - 0.95) < 1e-9
    store.close()


def test_resolve_clusters_bulk_fast_path_writes_brand_new(pg_url: str) -> None:
    """End-to-end: `resolve_clusters` against postgres takes the bulk
    fast-path for brand-new clusters. After the call: identity_nodes,
    source_records, evidence_edges, and identity_events all have the
    expected rows -- via 4 COPY batches instead of 5+ INSERTs per
    cluster. Closes #368 Phase 6 bench hang."""
    import polars as pl
    from goldenmatch.identity.resolve import resolve_clusters
    from goldenmatch.identity.store import IdentityStore

    store = IdentityStore(backend="postgres", connection=pg_url)

    # 10 brand-new 2-member clusters (same shape as bench_phase6_identity).
    n_clusters = 10
    n_rows = n_clusters * 2
    # Each row distinct (name keyed on i, not i//2) so derive_record_id
    # via payload-hash fallback returns distinct record_ids -- avoids
    # an ON CONFLICT cardinality violation when two cluster members would
    # otherwise share a record_id.
    df = pl.DataFrame(
        {
            "__row_id__": list(range(n_rows)),
            "__source__": ["bench"] * n_rows,
            "name": [f"person_{i}" for i in range(n_rows)],
            "email": [f"p{i}@x.com" for i in range(n_rows)],
        }
    )
    clusters = {
        i: {
            "members": [i * 2, i * 2 + 1],
            "size": 2,
            "confidence": 0.95,
            "pair_scores": {(i * 2, i * 2 + 1): 0.95},
        }
        for i in range(n_clusters)
    }
    scored_pairs = [(i * 2, i * 2 + 1, 0.95) for i in range(n_clusters)]

    summary = resolve_clusters(
        clusters, df, scored_pairs, "weighted", store,
        run_name="bulk-fast-test", source_pk_col=None,
    )

    assert summary.created == n_clusters
    assert summary.records_upserted == n_rows
    assert summary.edges_added == n_clusters
    assert summary.events_emitted == n_clusters
    assert store.count_identities() == n_clusters
    store.close()
