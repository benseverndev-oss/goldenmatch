"""Postgres-backend round-trip for migrate_record_ids."""
from __future__ import annotations

from collections.abc import Iterator

import pytest

psycopg = pytest.importorskip("psycopg")            # noqa: F841
testing_pg = pytest.importorskip("testing.postgresql")

from goldenmatch.identity import IdentityStore
from goldenmatch.identity.migrate_ids import migrate_record_ids

from tests.identity.test_migrate_ids import (  # reuse seed + recompute helpers
    _recompute_h1_id,
    _seed_legacy_record,
)


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


def test_migrate_postgres_roundtrip(pg_url: str) -> None:
    store = IdentityStore(backend="postgres", connection=pg_url)
    try:
        ra = _seed_legacy_record(store, "acme", {"name": "Ann"}, "ent-1")
        rpt = migrate_record_ids(store)
        assert rpt.rewritten == 1
        assert store.get_record(ra) is None
        new_a = _recompute_h1_id("acme", {"name": "Ann"})
        assert store.find_entity_by_record(new_a) == "ent-1"
    finally:
        store.close()
