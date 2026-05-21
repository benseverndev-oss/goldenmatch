"""Alembic-managed Identity Graph schema migrations."""
from __future__ import annotations

import pathlib
from collections.abc import Iterator

import pytest

psycopg = pytest.importorskip("psycopg")
testing_pg = pytest.importorskip("testing.postgresql")
alembic = pytest.importorskip("alembic")
sqlalchemy = pytest.importorskip("sqlalchemy")


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


def _alembic_cfg(dsn: str):
    from alembic.config import Config

    cfg_path = (
        pathlib.Path(__file__).resolve().parents[2]
        / "goldenmatch"
        / "db"
        / "alembic.ini"
    )
    cfg = Config(str(cfg_path))
    cfg.set_main_option("sqlalchemy.url", dsn)
    cfg.set_main_option(
        "script_location",
        str(cfg_path.parent / "alembic"),
    )
    return cfg


def _table_exists(dsn: str, table: str) -> bool:
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema='public' AND table_name=%s",
            (table,),
        )
        return cur.fetchone() is not None


def test_alembic_upgrade_from_empty_db(pg_url: str) -> None:
    from alembic import command

    cfg = _alembic_cfg(pg_url)
    command.upgrade(cfg, "head")

    for tbl in (
        "identity_nodes",
        "source_records",
        "evidence_edges",
        "identity_events",
        "identity_aliases",
        "alembic_version",
    ):
        assert _table_exists(pg_url, tbl), f"missing table after upgrade: {tbl}"


def test_alembic_upgrade_is_idempotent(pg_url: str) -> None:
    from alembic import command

    cfg = _alembic_cfg(pg_url)
    command.upgrade(cfg, "head")
    # Second upgrade should be a no-op (already at head); must not raise.
    command.upgrade(cfg, "head")
    assert _table_exists(pg_url, "identity_nodes")


def test_stamp_existing_v1_schema(pg_url: str) -> None:
    """A DB whose schema was created by ``_pg_init_schema`` (pre-Alembic)
    can be stamped at 0001 to bring it under Alembic management without
    re-creating tables."""
    from alembic import command

    # Pre-seed: open an IdentityStore which runs `_pg_init_schema`.
    from goldenmatch.identity.store import IdentityStore

    store = IdentityStore(backend="postgres", connection=pg_url)
    store.close()
    assert _table_exists(pg_url, "identity_nodes")
    assert not _table_exists(pg_url, "alembic_version")

    cfg = _alembic_cfg(pg_url)
    command.stamp(cfg, "0001")
    assert _table_exists(pg_url, "alembic_version")
    # Still has the original schema -- no drop happened.
    assert _table_exists(pg_url, "identity_nodes")
