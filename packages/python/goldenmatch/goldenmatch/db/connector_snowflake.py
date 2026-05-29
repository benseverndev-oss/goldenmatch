"""Snowflake sync-target connector.

Implements ``DatabaseConnector`` so the incremental-sync orchestrator
can write golden records back to Snowflake the same way it does to
Postgres. Closes the loop with the Snowflake source/sink at
``connectors/snowflake.py`` -- now both reading from AND writing to
Snowflake via the sync pipeline work end-to-end.

Requires: ``pip install goldenmatch[snowflake]``.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from typing import Any

import polars as pl

from goldenmatch.db.connector import DatabaseConnector

logger = logging.getLogger(__name__)


class SnowflakeConnector(DatabaseConnector):
    """Snowflake sync target via ``snowflake-connector-python``.

    The "connection string" here is either a kwargs dict (preferred --
    Snowflake's connector takes many distinct fields) or a single
    string that the connector pairs with environment variables
    (``SNOWFLAKE_USER``, ``SNOWFLAKE_PASSWORD``, ``SNOWFLAKE_ACCOUNT``,
    ``SNOWFLAKE_DATABASE``, ``SNOWFLAKE_SCHEMA``, ``SNOWFLAKE_WAREHOUSE``).
    """

    _conn: Any

    def __init__(self, connection: str | dict[str, Any]):
        self.connection = connection
        self._conn = None

    def connect(self) -> None:
        try:
            import snowflake.connector  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "Snowflake support requires snowflake-connector-python. "
                "Install with: pip install 'goldenmatch[snowflake]'"
            ) from e

        if isinstance(self.connection, dict):
            kwargs = dict(self.connection)
        else:
            # Single string: treat as account; pull rest from env.
            kwargs = {
                "account": self.connection,
                "user": os.environ.get("SNOWFLAKE_USER", ""),
                "password": os.environ.get("SNOWFLAKE_PASSWORD", ""),
                "database": os.environ.get("SNOWFLAKE_DATABASE", ""),
                "schema": os.environ.get("SNOWFLAKE_SCHEMA", ""),
                "warehouse": os.environ.get("SNOWFLAKE_WAREHOUSE", ""),
            }
            kwargs = {k: v for k, v in kwargs.items() if v}

        self._conn = snowflake.connector.connect(**kwargs)
        logger.info("Connected to Snowflake")

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> Any:
        if self._conn is None:
            raise RuntimeError("Not connected. Call connect() first.")
        return self._conn

    def read_table(self, table: str, chunk_size: int = 10000) -> Iterator[pl.DataFrame]:
        cursor = self.conn.cursor()
        try:
            cursor.execute(f"SELECT * FROM {_quote_ident(table)}")
            columns = [d[0] for d in cursor.description]
            while True:
                rows = cursor.fetchmany(chunk_size)
                if not rows:
                    break
                data = {col: [row[i] for row in rows] for i, col in enumerate(columns)}
                yield pl.DataFrame(data)
        finally:
            cursor.close()

    def read_query(self, query: str) -> pl.DataFrame:
        cursor = self.conn.cursor()
        try:
            cursor.execute(query)
            if cursor.description is None:
                return pl.DataFrame()
            columns = [d[0] for d in cursor.description]
            rows = cursor.fetchall()
            if not rows:
                return pl.DataFrame({c: [] for c in columns})
            data = {col: [row[i] for row in rows] for i, col in enumerate(columns)}
            return pl.DataFrame(data)
        finally:
            cursor.close()

    def write_dataframe(
        self, df: pl.DataFrame, table: str, mode: str = "append"
    ) -> int:
        """Bulk-write via ``write_pandas`` (the fastest supported path).

        snowflake-connector-python's ``write_pandas`` uses PUT + COPY
        under the hood -- the only practical option at scale; per-row
        INSERTs against Snowflake are an antipattern.
        """
        if df.height == 0:
            return 0

        try:
            from snowflake.connector.pandas_tools import (
                write_pandas,  # type: ignore[import-not-found]
            )
        except ImportError as e:
            raise ImportError(
                "Snowflake bulk write requires snowflake-connector-python "
                "with the pandas extra. "
                "Install with: pip install 'snowflake-connector-python[pandas]'"
            ) from e

        if mode == "replace":
            self.conn.cursor().execute(
                f"TRUNCATE TABLE IF EXISTS {_quote_ident(table)}"
            )

        pdf = df.to_pandas()
        success, nchunks, nrows, _ = write_pandas(
            self.conn,
            pdf,
            table,
            auto_create_table=(mode == "replace"),
        )
        if not success:
            raise RuntimeError(
                f"write_pandas reported failure writing to {table}"
            )
        logger.info("Wrote %d rows to %s (%d chunks)", nrows, table, nchunks)
        return int(nrows)

    def execute(self, sql: str, params: tuple | None = None) -> None:
        cursor = self.conn.cursor()
        try:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
        finally:
            cursor.close()

    def table_exists(self, table: str) -> bool:
        cursor = self.conn.cursor()
        try:
            bare = table.split(".")[-1]
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = %s",
                (bare.upper(),),
            )
            row = cursor.fetchone()
            return bool(row and row[0])
        finally:
            cursor.close()

    def get_row_count(self, table: str) -> int:
        cursor = self.conn.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {_quote_ident(table)}")
            row = cursor.fetchone()
            return int(row[0]) if row else 0
        finally:
            cursor.close()


def _quote_ident(name: str) -> str:
    """Quote a Snowflake identifier; allow ``DB.SCHEMA.TABLE`` (split on
    each dot)."""
    return ".".join('"' + p.replace('"', '""') + '"' for p in name.split("."))


__all__ = ["SnowflakeConnector"]
