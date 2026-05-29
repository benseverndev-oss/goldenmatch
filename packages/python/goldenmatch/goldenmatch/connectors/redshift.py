"""Amazon Redshift source/sink connector.

Reads rows from a Redshift query or table into a Polars LazyFrame and
writes results back. Uses ``redshift_connector`` -- Amazon's official
driver -- which supports IAM auth, MFA, and the modern Data API patterns
on top of plain user/password.

Requires: ``pip install goldenmatch[redshift]`` (pulls ``redshift-connector``).

## Connection sourcing

In order of precedence:

1. ``config['connection']`` -- a kwargs dict passed straight to
   ``redshift_connector.connect`` (``host``, ``port``, ``database``,
   ``user``, ``password`` plus any IAM options)
2. ``self._credentials`` -- ``user``/``password``/``database`` plus
   ``key`` (host)
3. ``REDSHIFT_HOST`` + ``REDSHIFT_USER`` + ``REDSHIFT_PASSWORD`` +
   ``REDSHIFT_DATABASE`` env vars -- the package-wide default

## Reading

  - ``config['query']`` -- raw SQL to execute (preferred)
  - ``config['table']`` -- table name; connector builds
    ``SELECT * FROM <table>`` (with optional ``LIMIT``)
  - ``config['limit']``  -- optional row cap
  - ``config['params']`` -- positional params passed to the cursor

## Writing

``config['mode']`` is one of:

  - ``"append"`` (default) -- ``INSERT INTO ... VALUES (...)``
  - ``"upsert"``           -- staging-table + ``DELETE`` + ``INSERT``
                              (Redshift has no ``ON CONFLICT``)
  - ``"replace"``          -- ``TRUNCATE`` then insert

Upserts require ``config['key']`` (a column name or list of names).
The upsert is wrapped in a single transaction so concurrent reads see
either the old or the new row, never a partial state.
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


class RedshiftConnector(BaseConnector):
    """Read/write rows from Amazon Redshift via redshift_connector."""

    name = "redshift"

    def _connect(self, config: dict):
        try:
            import redshift_connector  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ConnectorError(
                "Redshift connector requires redshift-connector. "
                "Install with: pip install goldenmatch[redshift]"
            ) from exc

        kwargs = self._connection_kwargs(config)
        return redshift_connector.connect(**kwargs)

    def _connection_kwargs(self, config: dict) -> dict[str, Any]:
        explicit = config.get("connection")
        if isinstance(explicit, dict):
            return dict(explicit)

        host = (
            self._credentials.get("key")
            or os.environ.get("REDSHIFT_HOST")
        )
        user = (
            self._credentials.get("user")
            or os.environ.get("REDSHIFT_USER")
        )
        password = (
            self._credentials.get("password")
            or os.environ.get("REDSHIFT_PASSWORD")
        )
        database = (
            self._credentials.get("database")
            or os.environ.get("REDSHIFT_DATABASE")
        )
        if not host or not user or not database:
            raise ConnectorError(
                "Redshift connector needs a connection. Pass it as "
                "config['connection'] (kwargs dict), set REDSHIFT_HOST + "
                "REDSHIFT_USER + REDSHIFT_PASSWORD + REDSHIFT_DATABASE, "
                "or wire credentials_env."
            )
        return {
            "host": host,
            "user": user,
            "password": password or "",
            "database": database,
        }

    def read(self, config: dict) -> pl.LazyFrame:
        sql = resolve_query(config, "Redshift")
        params = config.get("params")
        conn = self._connect(config)
        try:
            cur = conn.cursor()
            cur.execute(sql, params) if params else cur.execute(sql)
            cols = [d[0] for d in (cur.description or [])]
            rows = cur.fetchall()
            df = rows_to_dataframe(cols, [tuple(r) for r in rows])
            logger.info(
                "Redshift: read %d rows (%d columns)", df.height, df.width
            )
            return df.lazy()
        finally:
            conn.close()

    def write(self, df: pl.DataFrame, config: dict) -> None:
        if df.height == 0:
            logger.info("Redshift: write skipped on empty DataFrame.")
            return

        mode = require_mode(config, "Redshift")
        table = require_table(config, "Redshift")
        cols, rows = df_to_rows(df)
        col_list = ", ".join(_quote_ident(c) for c in cols)
        placeholders = ", ".join(["%s"] * len(cols))

        conn = self._connect(config)
        try:
            cur = conn.cursor()
            if mode == "replace":
                cur.execute(f"TRUNCATE TABLE {table}")
                cur.executemany(
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                    rows,
                )
            elif mode == "upsert":
                keys = require_upsert_key(config, "Redshift")
                self._execute_upsert(
                    cur, table, cols, keys, col_list, placeholders, rows
                )
            else:  # append
                cur.executemany(
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                    rows,
                )
            conn.commit()
            logger.info(
                "Redshift: wrote %d rows to %s (mode=%s)", df.height, table, mode
            )
        finally:
            conn.close()

    @staticmethod
    def _execute_upsert(
        cur: Any,
        table: str,
        cols: list[str],
        keys: list[str],
        col_list: str,
        placeholders: str,
        rows: list[tuple[Any, ...]],
    ) -> None:
        """Redshift upsert via staging table.

        Redshift has no ``ON CONFLICT`` / ``MERGE``; the supported pattern
        is: create a temp staging table, bulk-load incoming rows, delete
        target rows whose keys match staging, insert staging into target,
        drop staging -- all in one transaction. See:
        https://docs.aws.amazon.com/redshift/latest/dg/merge-replacing-existing-rows.html
        """
        staging = f"#goldenmatch_upsert_staging_{_short_id(table)}"
        cur.execute(f"CREATE TEMP TABLE {staging} (LIKE {table})")
        cur.executemany(
            f"INSERT INTO {staging} ({col_list}) VALUES ({placeholders})",
            rows,
        )
        join_clause = " AND ".join(
            f"{table}.{_quote_ident(k)} = {staging}.{_quote_ident(k)}"
            for k in keys
        )
        cur.execute(
            f"DELETE FROM {table} USING {staging} WHERE {join_clause}"
        )
        cur.execute(f"INSERT INTO {table} SELECT * FROM {staging}")
        cur.execute(f"DROP TABLE {staging}")


def _quote_ident(name: str) -> str:
    """Quote a Redshift identifier; double any embedded quotes."""
    return '"' + name.replace('"', '""') + '"'


def _short_id(seed: str) -> str:
    """A short ASCII identifier deterministically derived from ``seed``.

    Used so a staging table name is stable per-target-table within a
    transaction. Not security-sensitive -- just needs to be a valid
    identifier suffix that's unlikely to collide.
    """
    h = 0
    for ch in seed:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return f"{h:08x}"


__all__ = ["RedshiftConnector"]
