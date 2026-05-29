"""DuckDB source/sink connector.

Reads tables and queries from a user-maintained DuckDB database into
Polars and writes results back. This is the *connector* shape -- one of
many sources the pipeline can pull from / push to. It is distinct from
``goldenmatch.backends.duckdb_backend.DuckDBBackend``, which routes the
*entire* dedupe pipeline through DuckDB as an out-of-core execution
engine.

Pick the connector when DuckDB is just where the data happens to live;
pick the backend when you want DuckDB to run the dedupe itself.

Requires: ``pip install goldenmatch[duckdb]``.

## Connection sourcing

In order of precedence:

1. ``config['connection']`` -- path to the ``.duckdb`` file, or
   ``":memory:"`` for an ephemeral database
2. ``self._credentials['key']`` -- whatever ``credentials_env`` resolved
3. ``DUCKDB_PATH`` env var -- the package-wide default

A pre-built ``duckdb.DuckDBPyConnection`` can also be passed as
``config['conn']`` (an open connection) -- useful for tests and for
sharing one connection across multiple reads/writes. When passed, the
connector does NOT close it.

## Reading

  - ``config['query']`` -- raw SQL to execute (preferred)
  - ``config['table']`` -- table name; the connector reads
    ``SELECT * FROM <table>`` (with optional ``LIMIT``). Works on
    tables, views, and DuckDB's parquet/csv ``read_*`` functions.

Reads use DuckDB's native Polars integration (``.pl()``) so there's no
intermediate pandas materialization.

## Writing

``config['mode']`` is one of:

  - ``"append"`` (default) -- ``INSERT INTO ... SELECT * FROM df``
  - ``"upsert"``           -- ``INSERT ... ON CONFLICT (key) DO UPDATE``
                              (requires the target table to have a
                              ``UNIQUE`` / ``PRIMARY KEY`` covering the
                              upsert keys, which is DuckDB's contract)
  - ``"replace"``          -- ``CREATE OR REPLACE TABLE``
"""
from __future__ import annotations

import logging
import os
from typing import Any

import polars as pl

from goldenmatch.connectors._sql_common import (
    require_mode,
    require_table,
    require_upsert_key,
)
from goldenmatch.connectors.base import BaseConnector, ConnectorError

logger = logging.getLogger(__name__)


class DuckDBSourceConnector(BaseConnector):
    """Read/write rows from a DuckDB database file."""

    name = "duckdb"

    def _open(self, config: dict, *, read_only: bool):
        try:
            import duckdb  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ConnectorError(
                "DuckDB connector requires duckdb. "
                "Install with: pip install goldenmatch[duckdb]"
            ) from exc

        if config.get("conn") is not None:
            return config["conn"], False  # caller owns the lifetime

        path = (
            config.get("connection")
            or self._credentials.get("key")
            or os.environ.get("DUCKDB_PATH")
        )
        if not path:
            raise ConnectorError(
                "DuckDB connector needs a path. Pass it as "
                "config['connection'], set DUCKDB_PATH, or wire "
                "credentials_env. Use ':memory:' for an ephemeral DB."
            )
        return duckdb.connect(database=str(path), read_only=read_only), True

    def read(self, config: dict) -> pl.LazyFrame:
        sql = self._resolve_query(config)
        conn, owns = self._open(config, read_only=True)
        try:
            df = conn.sql(sql).pl()
            logger.info(
                "DuckDB: read %d rows (%d columns)", df.height, df.width
            )
            return df.lazy()
        finally:
            if owns:
                conn.close()

    def _resolve_query(self, config: dict) -> str:
        query = config.get("query")
        if query:
            return str(query)
        table = config.get("table")
        if not table:
            raise ConnectorError(
                "DuckDB connector requires 'query' or 'table'."
            )
        sql = f"SELECT * FROM {table}"
        limit = config.get("limit")
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return sql

    def write(self, df: pl.DataFrame, config: dict) -> None:
        if df.height == 0:
            logger.info("DuckDB: write skipped on empty DataFrame.")
            return

        mode = require_mode(config, "DuckDB")
        table = require_table(config, "DuckDB")
        conn, owns = self._open(config, read_only=False)
        try:
            # Register the DataFrame as a DuckDB view; this is zero-copy
            # via Arrow. The view name is intentionally local to this
            # call so concurrent writes don't collide.
            view_name = f"goldenmatch_write_{_short_id(table)}"
            conn.register(view_name, df)
            try:
                if mode == "replace":
                    conn.execute(
                        f"CREATE OR REPLACE TABLE {table} AS "
                        f"SELECT * FROM {view_name}"
                    )
                elif mode == "upsert":
                    keys = require_upsert_key(config, "DuckDB")
                    self._execute_upsert(conn, table, list(df.columns), keys, view_name)
                else:  # append
                    conn.execute(
                        f"INSERT INTO {table} SELECT * FROM {view_name}"
                    )
            finally:
                conn.unregister(view_name)
            logger.info(
                "DuckDB: wrote %d rows to %s (mode=%s)", df.height, table, mode
            )
        finally:
            if owns:
                conn.close()

    @staticmethod
    def _execute_upsert(
        conn: Any,
        table: str,
        cols: list[str],
        keys: list[str],
        view_name: str,
    ) -> None:
        col_list = ", ".join(_quote_ident(c) for c in cols)
        non_key = [c for c in cols if c not in keys]
        if non_key:
            assignments = ", ".join(
                f"{_quote_ident(c)} = EXCLUDED.{_quote_ident(c)}"
                for c in non_key
            )
            on_conflict = (
                f"ON CONFLICT ({', '.join(_quote_ident(k) for k in keys)}) "
                f"DO UPDATE SET {assignments}"
            )
        else:
            on_conflict = (
                f"ON CONFLICT ({', '.join(_quote_ident(k) for k in keys)}) "
                "DO NOTHING"
            )
        conn.execute(
            f"INSERT INTO {table} ({col_list}) "
            f"SELECT {col_list} FROM {view_name} {on_conflict}"
        )


def _quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _short_id(seed: str) -> str:
    h = 0
    for ch in seed:
        h = (h * 131 + ord(ch)) & 0xFFFFFFFF
    return f"{h:08x}"


__all__ = ["DuckDBSourceConnector"]
