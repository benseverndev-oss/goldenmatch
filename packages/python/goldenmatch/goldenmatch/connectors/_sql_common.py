"""Shared helpers for the SQL-family source/sink connectors.

Postgres, SQL Server and MySQL all want the same three things:

* a connection (driver-specific URI + kwargs),
* a ``read()`` shaped like ``query / table -> pl.LazyFrame``, and
* a ``write()`` with the same ``append / upsert / replace`` modes the
  Mongo and Snowflake connectors expose.

The driver bits differ (psycopg vs pyodbc vs pymysql), and the
upsert dialect differs (``ON CONFLICT`` vs ``MERGE`` vs
``ON DUPLICATE KEY UPDATE``). Everything else is shared and lives here
so each per-DB module stays a thin adapter.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

import polars as pl

from goldenmatch.connectors.base import ConnectorError

logger = logging.getLogger(__name__)


VALID_WRITE_MODES = ("append", "upsert", "replace")


def rows_to_dataframe(columns: list[str], rows: Iterable[tuple]) -> pl.DataFrame:
    """Build a Polars DataFrame from a cursor's columns + rows."""
    materialized = list(rows)
    if not materialized:
        return pl.DataFrame({c: [] for c in columns})
    return pl.DataFrame(
        {c: [r[i] for r in materialized] for i, c in enumerate(columns)}
    )


def resolve_query(config: dict, connector_label: str) -> str:
    """Build the SELECT to run from either ``query`` or ``table`` keys."""
    query = config.get("query")
    if query:
        return str(query)
    table = config.get("table")
    if not table:
        raise ConnectorError(
            f"{connector_label} connector requires 'query' or 'table'."
        )
    limit = config.get("limit")
    sql = f"SELECT * FROM {table}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return sql


def require_table(config: dict, connector_label: str) -> str:
    """Read ``config['table']`` for writes, or raise."""
    table = config.get("table")
    if not table:
        raise ConnectorError(f"{connector_label} write requires 'table'.")
    return str(table)


def require_mode(config: dict, connector_label: str) -> str:
    """Validate and return ``config['mode']`` (defaults to ``append``)."""
    mode = config.get("mode", "append")
    if mode not in VALID_WRITE_MODES:
        raise ConnectorError(
            f"{connector_label} connector: unknown mode {mode!r}. "
            f"Choose one of: {', '.join(VALID_WRITE_MODES)}."
        )
    return mode


def require_upsert_key(config: dict, connector_label: str) -> list[str]:
    """Read ``config['key']`` (str or list[str]) for upserts, or raise."""
    key = config.get("key")
    if not key:
        raise ConnectorError(
            f"{connector_label} upsert requires config['key'] -- the column "
            "name (or list of column names) to use as the conflict target."
        )
    return [key] if isinstance(key, str) else list(key)


def df_to_rows(df: pl.DataFrame) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Return (columns, rows-as-tuples). Polars nulls become Python None."""
    cols = list(df.columns)
    rows = [tuple(row) for row in df.iter_rows()]
    return cols, rows


__all__ = [
    "VALID_WRITE_MODES",
    "df_to_rows",
    "require_mode",
    "require_table",
    "require_upsert_key",
    "resolve_query",
    "rows_to_dataframe",
]
