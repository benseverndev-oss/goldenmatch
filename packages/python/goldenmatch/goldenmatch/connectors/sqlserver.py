"""SQL Server / Azure SQL source/sink connector.

Reads from a T-SQL query or table into a Polars LazyFrame and writes
results back via ``pyodbc``. Works against both on-prem SQL Server and
Azure SQL Database -- the connection string handles the difference.

Requires: ``pip install goldenmatch[sqlserver]`` (pulls ``pyodbc``).
The ODBC Driver for SQL Server must also be installed on the host;
the Microsoft docs cover platform-specific install steps.

## Connection sourcing

In order of precedence:

1. ``config['connection']`` -- explicit ODBC connection string
2. ``self._credentials['key']`` -- whatever ``credentials_env`` resolved
3. ``SQLSERVER_CONNECTION`` env var -- the package-wide default

## Reading

  - ``config['query']`` -- raw T-SQL to execute (preferred)
  - ``config['table']`` -- table name; the connector builds
    ``SELECT * FROM <table>`` (or ``SELECT TOP N *`` when ``limit`` is set)
  - ``config['limit']`` -- optional row cap as ``TOP N`` (ignored when
    ``query`` is set)
  - ``config['params']`` -- optional positional params passed to the cursor

## Writing

``config['mode']`` is one of:

  - ``"append"`` (default) -- ``INSERT INTO ... VALUES (?, ?, ...)``
  - ``"upsert"``           -- ``MERGE`` keyed on ``config['key']``
  - ``"replace"``          -- ``TRUNCATE`` then insert
"""
from __future__ import annotations

import logging
import os

import polars as pl

from goldenmatch.connectors._sql_common import (
    df_to_rows,
    require_mode,
    require_table,
    require_upsert_key,
    rows_to_dataframe,
)
from goldenmatch.connectors.base import BaseConnector, ConnectorError

logger = logging.getLogger(__name__)


class SqlServerConnector(BaseConnector):
    """Read/write rows from SQL Server / Azure SQL via pyodbc."""

    name = "sqlserver"

    def _connect(self, config: dict):
        try:
            import pyodbc  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ConnectorError(
                "SQL Server connector requires pyodbc. "
                "Install with: pip install goldenmatch[sqlserver] "
                "(plus the Microsoft ODBC Driver for SQL Server)."
            ) from exc

        dsn = (
            config.get("connection")
            or self._credentials.get("key")
            or os.environ.get("SQLSERVER_CONNECTION")
        )
        if not dsn:
            raise ConnectorError(
                "SQL Server connector needs a connection string. Pass it as "
                "config['connection'], set SQLSERVER_CONNECTION, or wire "
                "credentials_env."
            )
        return pyodbc.connect(dsn)

    def _resolve_query(self, config: dict) -> str:
        query = config.get("query")
        if query:
            return str(query)
        table = config.get("table")
        if not table:
            raise ConnectorError(
                "SQL Server connector requires 'query' or 'table'."
            )
        limit = config.get("limit")
        if limit is not None:
            return f"SELECT TOP {int(limit)} * FROM {table}"
        return f"SELECT * FROM {table}"

    def read(self, config: dict) -> pl.LazyFrame:
        sql = self._resolve_query(config)
        params = config.get("params")
        conn = self._connect(config)
        try:
            cur = conn.cursor()
            cur.execute(sql, params) if params else cur.execute(sql)
            cols = [d[0] for d in (cur.description or [])]
            rows = cur.fetchall()
            df = rows_to_dataframe(cols, [tuple(r) for r in rows])
            logger.info(
                "SQL Server: read %d rows (%d columns)", df.height, df.width
            )
            return df.lazy()
        finally:
            conn.close()

    def write(self, df: pl.DataFrame, config: dict) -> None:
        if df.height == 0:
            logger.info("SQL Server: write skipped on empty DataFrame.")
            return

        mode = require_mode(config, "SQL Server")
        table = require_table(config, "SQL Server")
        cols, rows = df_to_rows(df)
        col_list = ", ".join(_quote_ident(c) for c in cols)
        placeholders = ", ".join(["?"] * len(cols))

        conn = self._connect(config)
        try:
            cur = conn.cursor()
            cur.fast_executemany = True  # type: ignore[attr-defined]
            if mode == "replace":
                cur.execute(f"TRUNCATE TABLE {table}")
            if mode == "upsert":
                keys = require_upsert_key(config, "SQL Server")
                merge_sql = _build_merge(table, cols, keys)
                cur.executemany(merge_sql, rows)
            else:
                cur.executemany(
                    f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                    rows,
                )
            conn.commit()
            logger.info(
                "SQL Server: wrote %d rows to %s (mode=%s)", df.height, table, mode
            )
        finally:
            conn.close()


def _build_merge(table: str, cols: list[str], keys: list[str]) -> str:
    src_cols = ", ".join(_quote_ident(c) for c in cols)
    src_placeholders = ", ".join(["?"] * len(cols))
    on_clause = " AND ".join(
        f"target.{_quote_ident(k)} = source.{_quote_ident(k)}" for k in keys
    )
    non_key = [c for c in cols if c not in keys]
    update_set = ", ".join(
        f"{_quote_ident(c)} = source.{_quote_ident(c)}" for c in non_key
    )
    insert_cols = ", ".join(_quote_ident(c) for c in cols)
    insert_vals = ", ".join(f"source.{_quote_ident(c)}" for c in cols)
    update_clause = (
        f"WHEN MATCHED THEN UPDATE SET {update_set} " if update_set else ""
    )
    return (
        f"MERGE INTO {table} AS target USING "
        f"(SELECT {src_cols} FROM (VALUES ({src_placeholders})) "
        f"AS v({src_cols})) AS source ON {on_clause} "
        f"{update_clause}"
        f"WHEN NOT MATCHED THEN INSERT ({insert_cols}) VALUES ({insert_vals});"
    )


def _quote_ident(name: str) -> str:
    """Quote a SQL Server identifier; escape embedded ] chars."""
    return "[" + name.replace("]", "]]") + "]"


__all__ = ["SqlServerConnector"]
