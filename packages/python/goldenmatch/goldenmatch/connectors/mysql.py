"""MySQL / MariaDB source/sink connector.

Reads from a MySQL query or table into a Polars LazyFrame and writes
results back via ``pymysql`` (pure Python, no native build step).

Requires: ``pip install goldenmatch[mysql]`` (pulls ``pymysql``).

## Connection sourcing

In order of precedence:

1. ``config['connection']`` -- explicit kwargs dict
   (``host``/``user``/``password``/``database``/``port``)
2. ``self._credentials`` -- ``user``/``password``/``database`` plus
   ``key`` (host)
3. ``MYSQL_URL`` env var -- a ``mysql://user:pw@host:port/db`` URI

## Reading

  - ``config['query']`` -- raw SQL to execute (preferred)
  - ``config['table']`` -- table name; connector builds
    ``SELECT * FROM `<table>``` (with optional ``LIMIT``)
  - ``config['limit']``  -- optional row cap
  - ``config['params']`` -- positional params passed to the cursor

## Writing

``config['mode']`` is one of:

  - ``"append"`` (default) -- ``INSERT INTO ...``
  - ``"upsert"``           -- ``INSERT ... ON DUPLICATE KEY UPDATE``
  - ``"replace"``          -- ``TRUNCATE`` then insert
"""
from __future__ import annotations

import logging
import os
from typing import Any
from urllib.parse import urlparse

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


class MySQLConnector(BaseConnector):
    """Read/write rows from MySQL or MariaDB via pymysql."""

    name = "mysql"

    def _connect(self, config: dict):
        try:
            import pymysql  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ConnectorError(
                "MySQL connector requires pymysql. "
                "Install with: pip install goldenmatch[mysql]"
            ) from exc

        kwargs = self._connection_kwargs(config)
        return pymysql.connect(**kwargs)

    def _connection_kwargs(self, config: dict) -> dict[str, Any]:
        explicit = config.get("connection")
        if isinstance(explicit, dict):
            return dict(explicit)

        url = (
            (explicit if isinstance(explicit, str) else None)
            or os.environ.get("MYSQL_URL")
        )
        if url:
            parsed = urlparse(url)
            kwargs: dict[str, Any] = {
                "host": parsed.hostname or "localhost",
                "user": parsed.username or "",
                "password": parsed.password or "",
                "database": (parsed.path or "/").lstrip("/") or None,
            }
            if parsed.port:
                kwargs["port"] = int(parsed.port)
            return {k: v for k, v in kwargs.items() if v is not None}

        host = self._credentials.get("key")
        user = self._credentials.get("user")
        password = self._credentials.get("password")
        database = self._credentials.get("database")
        if not host or not user:
            raise ConnectorError(
                "MySQL connector needs a connection. Pass it as "
                "config['connection'] (URI or kwargs dict), set MYSQL_URL, "
                "or wire credentials_env."
            )
        return {
            "host": host,
            "user": user,
            "password": password or "",
            "database": database,
        }

    def read(self, config: dict) -> pl.LazyFrame:
        sql = resolve_query(config, "MySQL")
        params = config.get("params")
        conn = self._connect(config)
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params) if params else cur.execute(sql)
                cols = [d[0] for d in (cur.description or [])]
                rows = cur.fetchall()
            df = rows_to_dataframe(cols, [tuple(r) for r in rows])
            logger.info(
                "MySQL: read %d rows (%d columns)", df.height, df.width
            )
            return df.lazy()
        finally:
            conn.close()

    def write(self, df: pl.DataFrame, config: dict) -> None:
        if df.height == 0:
            logger.info("MySQL: write skipped on empty DataFrame.")
            return

        mode = require_mode(config, "MySQL")
        table = require_table(config, "MySQL")
        cols, rows = df_to_rows(df)
        col_list = ", ".join(_quote_ident(c) for c in cols)
        placeholders = ", ".join(["%s"] * len(cols))

        conn = self._connect(config)
        try:
            with conn.cursor() as cur:
                if mode == "replace":
                    cur.execute(f"TRUNCATE TABLE {table}")
                if mode == "upsert":
                    keys = require_upsert_key(config, "MySQL")
                    non_key = [c for c in cols if c not in keys]
                    update_clause = ", ".join(
                        f"{_quote_ident(c)} = VALUES({_quote_ident(c)})"
                        for c in non_key
                    ) or f"{_quote_ident(keys[0])} = {_quote_ident(keys[0])}"
                    sql = (
                        f"INSERT INTO {table} ({col_list}) "
                        f"VALUES ({placeholders}) "
                        f"ON DUPLICATE KEY UPDATE {update_clause}"
                    )
                    cur.executemany(sql, rows)
                else:
                    cur.executemany(
                        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders})",
                        rows,
                    )
            conn.commit()
            logger.info(
                "MySQL: wrote %d rows to %s (mode=%s)", df.height, table, mode
            )
        finally:
            conn.close()


def _quote_ident(name: str) -> str:
    """Quote a MySQL identifier with backticks; escape embedded backticks."""
    return "`" + name.replace("`", "``") + "`"


__all__ = ["MySQLConnector"]
