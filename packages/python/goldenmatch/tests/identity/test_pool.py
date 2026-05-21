"""Identity pool: concurrent acquire + singleton semantics."""
from __future__ import annotations

import threading
from collections.abc import Iterator

import pytest

psycopg_pool = pytest.importorskip("psycopg_pool")
testing_pg = pytest.importorskip("testing.postgresql")


@pytest.fixture
def pg_url() -> Iterator[str]:
    pg = testing_pg.Postgresql()
    try:
        yield pg.url()
    finally:
        try:
            pg.stop()
        except Exception:
            pass


def test_pool_concurrent_acquire_no_exhaustion(pg_url: str) -> None:
    from goldenmatch.identity.pool import get_identity_pool, reset_identity_pool

    reset_identity_pool()
    pool = get_identity_pool(pg_url, min_size=2, max_size=8)
    errors: list[Exception] = []

    def worker() -> None:
        try:
            for _ in range(20):
                with pool.connection() as conn:
                    conn.execute("SELECT 1").fetchone()
        except Exception as e:  # pragma: no cover - debug aid
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors, f"pool errors: {errors!r}"
    reset_identity_pool()


def test_pool_singleton_returns_same_pool(pg_url: str) -> None:
    from goldenmatch.identity.pool import get_identity_pool, reset_identity_pool

    reset_identity_pool()
    p1 = get_identity_pool(pg_url)
    p2 = get_identity_pool(pg_url)
    assert p1 is p2
    reset_identity_pool()


def test_reset_pool_closes_prior(pg_url: str) -> None:
    from goldenmatch.identity.pool import get_identity_pool, reset_identity_pool

    reset_identity_pool()
    p1 = get_identity_pool(pg_url)
    reset_identity_pool()
    p2 = get_identity_pool(pg_url)
    assert p1 is not p2
    reset_identity_pool()
