"""Shared SQL plumbing for the Snowflake-backed IdentityStore and MemoryStore.

Snowflake differs from the SQLite/Postgres paths in three ways that every
caller here has to respect, so they are handled once, in this module:

1. Only ``NOT NULL`` is enforced. ``PRIMARY KEY`` / ``UNIQUE`` / ``FOREIGN KEY``
   are metadata, so idempotency must come from an explicit ``MERGE``.
2. Unquoted identifiers are uppercased, so a ``DictCursor`` yields
   ``{'ENTITY_ID': ...}`` while every ``_row_to_*`` helper in the stores indexes
   ``row["entity_id"]``. ``CaseInsensitiveRow`` reconciles that.
3. There is no ``?`` placeholder; the connector default is ``pyformat``.
"""
from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from typing import Any


class CaseInsensitiveRow(Mapping[str, Any]):
    """One result row, indexable by either case.

    Snowflake returns ``ENTITY_ID`` for an unquoted ``entity_id`` column. The
    store row-mappers are shared with SQLite/Postgres and index lowercase, so
    every read goes through this rather than quoting identifiers in the DDL --
    quoted identifiers would force anyone hand-querying the tables to type
    ``SELECT "entity_id"``.
    """

    __slots__ = ("_data",)

    def __init__(self, data: Mapping[str, Any]) -> None:
        self._data = {k.lower(): v for k, v in data.items()}

    def __getitem__(self, key: str) -> Any:
        return self._data[key.lower()]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def __repr__(self) -> str:
        return f"CaseInsensitiveRow({self._data!r})"


def resolve_connection(connection: Any, *, database: str, schema: str) -> Any:
    """Return a live ``SnowflakeConnection`` from any of the accepted shapes.

    Accepts, in order of preference:

    - a Snowpark ``Session`` (anything exposing ``.connection``), which is what
      a stored procedure is handed for free;
    - a live ``SnowflakeConnection`` (has ``.cursor``), returned as-is;
    - a dict of ``snowflake.connector.connect`` kwargs;
    - an account-name string, with the rest read from ``SNOWFLAKE_*`` env vars,
      matching ``db/connector_snowflake.py``.
    """
    if connection is None:
        raise ValueError(
            "snowflake backend requires connection= (a Snowpark Session, a "
            "SnowflakeConnection, a kwargs dict, or an account name)"
        )
    # Snowpark Session -- check before .cursor so a Session that also proxies
    # cursor() still unwraps to the real connection.
    inner = getattr(connection, "connection", None)
    if inner is not None and hasattr(inner, "cursor"):
        return inner
    if hasattr(connection, "cursor"):
        return connection

    try:
        import snowflake.connector  # noqa: PLC0415
    except ImportError as e:
        raise ImportError(
            "snowflake backend requires snowflake-connector-python: "
            "pip install 'goldenmatch[snowflake]'"
        ) from e

    if isinstance(connection, dict):
        kwargs = dict(connection)
    elif isinstance(connection, str):
        kwargs = {
            "account": connection,
            "user": os.environ.get("SNOWFLAKE_USER", ""),
            "password": os.environ.get("SNOWFLAKE_PASSWORD", ""),
            "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE", ""),
            "role": os.environ.get("SNOWFLAKE_ROLE", ""),
        }
        kwargs = {k: v for k, v in kwargs.items() if v}
    else:
        raise TypeError(
            f"unsupported connection type for snowflake backend: "
            f"{type(connection).__name__}"
        )
    kwargs.setdefault("database", database)
    kwargs.setdefault("schema", schema)
    return snowflake.connector.connect(**kwargs)


def execute(conn: Any, sql: str, params: tuple = ()) -> None:
    """Run a statement, discarding any result."""
    with conn.cursor() as cur:
        cur.execute(sql, params)


def fetchone_row(
    conn: Any, sql: str, params: tuple = ()
) -> CaseInsensitiveRow | None:
    from snowflake.connector import DictCursor  # noqa: PLC0415

    with conn.cursor(DictCursor) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
    return CaseInsensitiveRow(row) if row else None


def fetchall_rows(
    conn: Any, sql: str, params: tuple = ()
) -> list[CaseInsensitiveRow]:
    from snowflake.connector import DictCursor  # noqa: PLC0415

    with conn.cursor(DictCursor) as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    return [CaseInsensitiveRow(r) for r in rows]
