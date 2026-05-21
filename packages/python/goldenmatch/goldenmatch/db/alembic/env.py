"""Alembic env for the GoldenMatch Identity Graph schema.

Uses SQLAlchemy minimally (just to satisfy alembic's online-mode contract).
Migration scripts themselves run plain DDL via ``op.execute(...)`` so we
don't depend on SQLAlchemy models.
"""
from __future__ import annotations

import os

from alembic import context


def _get_dsn() -> str:
    cfg = context.config
    url = cfg.get_main_option("sqlalchemy.url")
    if not url:
        url = os.environ.get("GOLDENMATCH_IDENTITY_DSN", "")
    if not url:
        raise RuntimeError(
            "Alembic env requires sqlalchemy.url config or "
            "GOLDENMATCH_IDENTITY_DSN environment variable.",
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=_get_dsn(),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    from sqlalchemy import create_engine

    # Translate psycopg2-style URL (postgresql://) to psycopg3 driver
    # if the user happened to pass a bare DSN. SQLAlchemy needs a
    # driver suffix when psycopg2 isn't installed.
    dsn = _get_dsn()
    if dsn.startswith("postgresql://") and "+psycopg" not in dsn:
        dsn = "postgresql+psycopg://" + dsn[len("postgresql://") :]
    elif dsn.startswith("postgres://") and "+psycopg" not in dsn:
        dsn = "postgresql+psycopg://" + dsn[len("postgres://") :]

    engine = create_engine(dsn, future=True)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            dialect_name="postgresql",
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
