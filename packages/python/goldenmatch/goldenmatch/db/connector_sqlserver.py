"""SQL Server / Azure SQL sync-target connector.

Implements ``DatabaseConnector`` so the incremental-sync orchestrator
can write golden records back to SQL Server / Azure SQL Database the
same way it does to Postgres. Sits alongside
``connectors/sqlserver.py`` (the read-side source/sink shape).

Requires: ``pip install goldenmatch[sqlserver]``. The Microsoft ODBC
Driver for SQL Server must also be installed on the host.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

import polars as pl

from goldenmatch.db.connector import DatabaseConnector

logger = logging.getLogger(__name__)


class SqlServerConnector(DatabaseConnector):
    """SQL Server / Azure SQL sync target via pyodbc.

    The connection string is an ODBC connection string, e.g.::

      DRIVER={ODBC Driver 18 for SQL Server};SERVER=...;
      DATABASE=...;UID=...;PWD=...;Encrypt=yes;

    Works against on-prem SQL Server and Azure SQL Database; the
    string handles the difference.
    """

    _conn: Any

    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self._conn = None

    def connect(self) -> None:
        try:
            import pyodbc  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "SQL Server support requires pyodbc. "
                "Install with: pip install 'goldenmatch[sqlserver]' "
                "(plus the Microsoft ODBC Driver for SQL Server)."
            ) from e

        # autocommit=False mirrors the Postgres + MySQL paths; writes
        # commit explicitly after the bulk insert.
        self._conn = pyodbc.connect(self.connection_string, autocommit=False)
        logger.info("Connected to SQL Server")

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
                data = {
                    col: [row[i] for row in rows] for i, col in enumerate(columns)
                }
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
        if df.height == 0:
            return 0

        cursor = self.conn.cursor()
        cursor.fast_executemany = True  # type: ignore[attr-defined]
        try:
            if mode == "replace":
                cursor.execute(f"TRUNCATE TABLE {_quote_ident(table)}")

            cols = df.columns
            col_list = ", ".join(_quote_ident(c) for c in cols)
            placeholders = ", ".join(["?"] * len(cols))
            insert_sql = (
                f"INSERT INTO {_quote_ident(table)} ({col_list}) "
                f"VALUES ({placeholders})"
            )
            cursor.executemany(insert_sql, list(df.iter_rows()))
            self.conn.commit()
            logger.info("Wrote %d rows to %s", df.height, table)
            return df.height
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def execute(self, sql: str, params: tuple | None = None) -> None:
        cursor = self.conn.cursor()
        try:
            if params:
                cursor.execute(sql, params)
            else:
                cursor.execute(sql)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def table_exists(self, table: str) -> bool:
        cursor = self.conn.cursor()
        try:
            # Strip any schema prefix for the existence check; SQL Server's
            # information_schema lookups want the unquoted name.
            bare = table.split(".")[-1]
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = ?",
                (bare,),
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
    """Quote a SQL Server identifier with ``[...]``; allow ``schema.table``
    and split on the first dot."""
    parts = name.split(".", 1)
    return ".".join("[" + p.replace("]", "]]") + "]" for p in parts)


__all__ = ["SqlServerConnector"]
