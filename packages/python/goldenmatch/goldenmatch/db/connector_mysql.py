"""MySQL / MariaDB sync-target connector.

Implements ``DatabaseConnector`` so the incremental-sync orchestrator
(``goldenmatch.db.sync``) can write golden records back to MySQL the
same way it already does to Postgres. Sits alongside
``connectors/mysql.py``, which is the read-side source/sink shape.

Requires: ``pip install goldenmatch[mysql]``.
"""
from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import polars as pl

from goldenmatch.db.connector import DatabaseConnector

logger = logging.getLogger(__name__)


class MySQLConnector(DatabaseConnector):
    """MySQL / MariaDB sync target using pymysql.

    The connector accepts a connection string in any of these forms:

      - ``mysql://user:pw@host:port/database``
      - ``mysql+pymysql://...`` (SQLAlchemy-style; the leading driver
        suffix is ignored)
      - a kwargs dict (host/user/password/database/port)
    """

    _conn: Any

    def __init__(self, connection: str | dict[str, Any]):
        self.connection = connection
        self._conn = None

    def connect(self) -> None:
        try:
            import pymysql  # type: ignore[import-not-found]
        except ImportError as e:
            raise ImportError(
                "MySQL support requires pymysql. "
                "Install with: pip install 'goldenmatch[mysql]'"
            ) from e

        kwargs = (
            dict(self.connection)
            if isinstance(self.connection, dict)
            else _parse_uri(self.connection)
        )
        # autocommit=False to mirror the Postgres path; writes commit
        # explicitly after the bulk insert.
        kwargs.setdefault("autocommit", False)
        self._conn = pymysql.connect(**kwargs)
        logger.info("Connected to MySQL")

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
        """Read a table in chunks via ``SSCursor`` (server-side cursor).

        ``SSCursor`` streams rows from the server instead of buffering
        the full result set, mirroring the psycopg3 named-portal path
        on Postgres.
        """
        import pymysql.cursors  # type: ignore[import-not-found]

        cursor = self.conn.cursor(pymysql.cursors.SSCursor)
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
        if df.height == 0:
            return 0

        cursor = self.conn.cursor()
        try:
            if mode == "replace":
                cursor.execute(f"TRUNCATE TABLE {_quote_ident(table)}")

            cols = df.columns
            col_list = ", ".join(_quote_ident(c) for c in cols)
            placeholders = ", ".join(["%s"] * len(cols))
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
            cursor.execute(sql, params)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def table_exists(self, table: str) -> bool:
        cursor = self.conn.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema = DATABASE() AND table_name = %s",
                (table,),
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


def _parse_uri(uri: str) -> dict[str, Any]:
    if "+" in uri.split("://", 1)[0]:
        # mysql+pymysql:// -> mysql://
        scheme, rest = uri.split("://", 1)
        uri = scheme.split("+", 1)[0] + "://" + rest
    parsed = urlparse(uri)
    kwargs: dict[str, Any] = {
        "host": parsed.hostname or "localhost",
        "user": parsed.username or "",
        "password": parsed.password or "",
        "database": (parsed.path or "/").lstrip("/") or None,
    }
    if parsed.port:
        kwargs["port"] = int(parsed.port)
    return {k: v for k, v in kwargs.items() if v is not None}


def _quote_ident(name: str) -> str:
    """Quote a MySQL identifier; allow ``schema.table`` and split on
    the first dot."""
    parts = name.split(".", 1)
    return ".".join("`" + p.replace("`", "``") + "`" for p in parts)


__all__ = ["MySQLConnector"]
