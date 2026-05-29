"""Postgres source/sink connector.

Reads rows from a Postgres query or table into a Polars LazyFrame and
writes results back. Sits alongside the SaaS-shaped connectors
(``snowflake``, ``bigquery``, ``databricks``, ``mongo``); orthogonal
to ``goldenmatch.db.connector.PostgresConnector``, which targets the
incremental-sync write-back schema with Alembic migrations.

Requires: ``pip install goldenmatch[postgres]`` (pulls ``psycopg[binary]``).

## Connection sourcing

In order of precedence:

1. ``config['connection']`` -- explicit psycopg connection string /
   keyword dict at call time
2. ``self._credentials['key']`` -- whatever ``credentials_env`` resolved
3. ``DATABASE_URL`` env var -- the package-wide default

## Reading

  - ``config['query']`` -- raw SQL to execute (preferred)
  - ``config['table']`` -- table name; the connector builds
    ``SELECT * FROM <table>`` for you
  - ``config['limit']`` -- optional row cap appended as ``LIMIT N`` when
    using ``table`` (ignored when ``query`` is set)
  - ``config['params']`` -- optional positional params (tuple or list)
    passed to the cursor

## Writing

``config['mode']`` is one of:

  - ``"append"`` (default) -- ``INSERT INTO ... VALUES (...)``
  - ``"upsert"``           -- ``INSERT ... ON CONFLICT (key) DO UPDATE``
  - ``"replace"``          -- ``TRUNCATE`` then insert

Upserts require ``config['key']`` (a column name or list of names) to
use as the conflict target.
"""
from __future__ import annotations

import logging
import os
from typing import Any

import polars as pl

from goldenmatch.connectors._sql_common import (
    df_to_rows,
    require_mode,
    require_table,
    require_upsert_key,
    resolve_query,
    rows_to_dataframe,
)
from goldenmatch.connectors.base import BaseConnector, ConnectorError

logger = logging.getLogger(__name__)


class PostgresConnector(BaseConnector):
    """Read/write rows from a Postgres database."""

    name = "postgres"

    def _connect(self, config: dict):
        try:
            import psycopg  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ConnectorError(
                "Postgres connector requires psycopg. "
                "Install with: pip install goldenmatch[postgres]"
            ) from exc

        conn_arg = (
            config.get("connection")
            or self._credentials.get("key")
            or os.environ.get("DATABASE_URL")
        )
        if not conn_arg:
            raise ConnectorError(
                "Postgres connector needs a connection string. Pass it as "
                "config['connection'], set DATABASE_URL, or wire "
                "credentials_env."
            )
        if isinstance(conn_arg, dict):
            return psycopg.connect(**conn_arg)
        return psycopg.connect(conn_arg)

    def read(self, config: dict) -> pl.LazyFrame:
        sql = resolve_query(config, "Postgres")
        params = config.get("params")
        conn = self._connect(config)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params) if params else cur.execute(sql)  # type: ignore[arg-type]
                cols = [d[0] for d in (cur.description or [])]
                rows = cur.fetchall()
            df = rows_to_dataframe(cols, rows)
            logger.info(
                "Postgres: read %d rows (%d columns)", df.height, df.width
            )
            return df.lazy()
        finally:
            conn.close()

    def write(self, df: pl.DataFrame, config: dict) -> None:
        if df.height == 0:
            logger.info("Postgres: write skipped on empty DataFrame.")
            return

        mode = require_mode(config, "Postgres")
        table = require_table(config, "Postgres")
        cols, rows = df_to_rows(df)
        col_list = ", ".join(_quote_ident(c) for c in cols)
        placeholders = ", ".join(["%s"] * len(cols))

        conn = self._connect(config)
        try:
            with conn.cursor() as cur:
                if mode == "replace":
                    cur.execute(f"TRUNCATE TABLE {table}")  # type: ignore[arg-type]
                if mode == "upsert":
                    keys = require_upsert_key(config, "Postgres")
                    self._execute_upsert(
                        cur, table, cols, keys, placeholders, rows
                    )
                else:
                    cur.executemany(
                        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",  # type: ignore[arg-type]
                        rows,
                    )
            conn.commit()
            logger.info(
                "Postgres: wrote %d rows to %s (mode=%s)", df.height, table, mode
            )
        finally:
            conn.close()

    @staticmethod
    def _execute_upsert(
        cur: Any,
        table: str,
        cols: list[str],
        keys: list[str],
        placeholders: str,
        rows: list[tuple[Any, ...]],
    ) -> None:
        non_key = [c for c in cols if c not in keys]
        if not non_key:
            # All columns are part of the key -- nothing to update on conflict.
            # Use ON CONFLICT DO NOTHING so re-inserts are idempotent.
            on_conflict = (
                f"ON CONFLICT ({', '.join(_quote_ident(k) for k in keys)}) "
                "DO NOTHING"
            )
        else:
            assignments = ", ".join(
                f"{_quote_ident(c)} = EXCLUDED.{_quote_ident(c)}" for c in non_key
            )
            on_conflict = (
                f"ON CONFLICT ({', '.join(_quote_ident(k) for k in keys)}) "
                f"DO UPDATE SET {assignments}"
            )
        col_list = ", ".join(_quote_ident(c) for c in cols)
        sql = (
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"{on_conflict}"
        )
        cur.executemany(sql, rows)  # type: ignore[arg-type]


def _quote_ident(name: str) -> str:
    """Quote a Postgres identifier; double any embedded quotes."""
    return '"' + name.replace('"', '""') + '"'


__all__ = ["PostgresConnector"]
