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
