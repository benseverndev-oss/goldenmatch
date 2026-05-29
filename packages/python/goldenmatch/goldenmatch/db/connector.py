"""Database connector interface and Postgres implementation."""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from collections.abc import Iterator
from typing import Any

import polars as pl

logger = logging.getLogger(__name__)


class DatabaseConnector(ABC):
    """Abstract interface for database connections."""

    @abstractmethod
    def connect(self) -> None:
        """Establish connection."""

    @abstractmethod
    def close(self) -> None:
        """Close connection."""

    @abstractmethod
    def read_table(self, table: str, chunk_size: int = 10000) -> Iterator[pl.DataFrame]:
        """Read table in chunks. Yields DataFrames."""

    @abstractmethod
    def read_query(self, query: str) -> pl.DataFrame:
        """Execute a SELECT query and return results as DataFrame."""

    @abstractmethod
    def write_dataframe(self, df: pl.DataFrame, table: str, mode: str = "append") -> int:
        """Write DataFrame to table. Returns rows written."""

    @abstractmethod
    def execute(self, sql: str, params: tuple | None = None) -> None:
        """Execute a SQL statement (DDL/DML)."""

    @abstractmethod
    def table_exists(self, table: str) -> bool:
        """Check if a table exists."""

    @abstractmethod
    def get_row_count(self, table: str) -> int:
        """Get row count for a table."""

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()


class PostgresConnector(DatabaseConnector):
    """PostgreSQL connector using psycopg3.

    psycopg3 is the supported driver (the [postgres] extra pins
    `psycopg[binary]>=3.1` since the Phase 6 IdentityStore migration).
    The default psycopg3 server-side portal cursor streams rows from
    Postgres instead of buffering the full result set client-side,
    which is what bit a 16 GB sandbox on a 1.13M-row read (#368).
    """

    # _conn is typed Any to short-circuit psycopg3's LiteralString-narrowing
    # on cursor.execute -- the dynamic SQL we build via f-strings would
    # otherwise need a Composed wrapper at every call site.
    _conn: Any

    def __init__(self, connection_string: str):
        self.connection_string = connection_string
        self._conn = None

    def connect(self) -> None:
        try:
            import psycopg
        except ImportError as e:
            raise ImportError(
                "Postgres support requires psycopg3. "
                "Install with: pip install 'goldenmatch[postgres]'"
            ) from e
        # autocommit=False matches the prior psycopg2 default; commits are
        # explicit via self.conn.commit() in write paths.
        self._conn = psycopg.connect(self.connection_string, autocommit=False)
        logger.info("Connected to PostgreSQL")

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
        """Read table in chunks via a server-side cursor.

        The named cursor (`name="gm_sync_read"`) tells Postgres to declare
        a server-side portal -- rows stream into the client `chunk_size`
        at a time instead of materializing the full result set in the
        psycopg connection buffer. Required to avoid OOM on multi-million
        row reads (#368). Server-side cursors only work inside a
        transaction; autocommit=False from connect() satisfies that.
        """
        cursor = self.conn.cursor(name="gm_sync_read")
        cursor.itersize = chunk_size
        try:
            cursor.execute(f"SELECT * FROM {_quote_ident(table)}")
            columns = [desc[0] for desc in cursor.description]

            while True:
                rows = cursor.fetchmany(chunk_size)
                if not rows:
                    break
                data = {col: [row[i] for row in rows] for i, col in enumerate(columns)}
                yield _normalize_chunk_schema(pl.DataFrame(data))
        finally:
            cursor.close()

    def read_query(self, query: str) -> pl.DataFrame:
        """Execute SELECT and return as DataFrame."""
        cursor = self.conn.cursor()
        try:
            cursor.execute(query)
            if cursor.description is None:
                return pl.DataFrame()
            columns = [desc[0] for desc in cursor.description]
            rows = cursor.fetchall()
            if not rows:
                return pl.DataFrame({col: [] for col in columns})
            data = {col: [row[i] for row in rows] for i, col in enumerate(columns)}
            return pl.DataFrame(data)
        finally:
            cursor.close()

    def write_dataframe(self, df: pl.DataFrame, table: str, mode: str = "append") -> int:
        """Write DataFrame to table using COPY for performance."""

        if df.height == 0:
            return 0

        cursor = self.conn.cursor()
        try:
            if mode == "replace":
                cursor.execute(f"TRUNCATE TABLE {_quote_ident(table)}")

            # Use COPY FROM for fast bulk insert
            columns = df.columns
            # Build INSERT statements for compatibility
            placeholders = ", ".join(["%s"] * len(columns))
            col_list = ", ".join(_quote_ident(c) for c in columns)
            insert_sql = f"INSERT INTO {_quote_ident(table)} ({col_list}) VALUES ({placeholders})"

            for row in df.iter_rows():
                cursor.execute(insert_sql, row)
            self.conn.commit()

            logger.info("Wrote %d rows to %s", df.height, table)
            return df.height
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cursor.close()

    def execute(self, sql: str, params: tuple | None = None) -> None:
        """Execute DDL/DML statement."""
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
                "SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = %s)",
                (table,),
            )
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    def get_row_count(self, table: str) -> int:
        cursor = self.conn.cursor()
        try:
            cursor.execute(f"SELECT COUNT(*) FROM {_quote_ident(table)}")
            return cursor.fetchone()[0]
        finally:
            cursor.close()


def _normalize_chunk_schema(df: pl.DataFrame) -> pl.DataFrame:
    """Cast Null-dtype columns to Utf8 so vstack across chunks succeeds.

    When a chunked Postgres read hits a chunk where a column is 100%
    NULL, Polars infers ``Null`` dtype for it. A later chunk with real
    values infers ``String``, and ``pl.concat`` rejects the mismatch
    with 'type String is incompatible with expected type Null'.

    Casting all-null ``Null`` columns to ``Utf8`` at the chunk boundary
    sidesteps the issue. Utf8 is the lowest-cost fallback when we can't
    know the true Postgres type from the cursor alone -- subsequent
    chunks carry the real values. See #363.
    """
    null_cols = [c for c, dt in df.schema.items() if dt == pl.Null]
    if not null_cols:
        return df
    return df.with_columns([pl.col(c).cast(pl.Utf8) for c in null_cols])


def _quote_ident(name: str) -> str:
    """Quote a SQL identifier to prevent injection.

    Splits a single ``schema.table`` arg into ``"schema"."table"`` so
    callers can pass non-public schemas via ``sync --table gm.foo``.
    See #365.

    Edge case: tables with literal dots in the name are split on the
    FIRST dot only -- yielding ``"odd_schema"."weird.name"``. Two-dot
    identifiers are vanishingly rare in practice and there is no
    portable escape syntax for them through a CLI flag.
    """
    parts = name.split(".", 1)  # max one split: schema.table
    return ".".join('"' + p.replace('"', '""') + '"' for p in parts)


def create_connector(config: dict) -> DatabaseConnector:
    """Factory to create a connector from config."""
    source_type = config.get("type", "postgres")
    connection = config.get("connection") or os.environ.get("GOLDENMATCH_DATABASE_URL")

    if not connection:
        raise ValueError(
            "Database connection string required. "
            "Set in config (source.connection) or env var GOLDENMATCH_DATABASE_URL."
        )

    if source_type == "postgres":
        return PostgresConnector(connection)
    if source_type in ("mysql", "mariadb"):
        from goldenmatch.db.connector_mysql import MySQLConnector
        return MySQLConnector(connection)
    if source_type in ("sqlserver", "mssql", "azure_sql"):
        from goldenmatch.db.connector_sqlserver import SqlServerConnector
        return SqlServerConnector(connection)
    if source_type == "snowflake":
        from goldenmatch.db.connector_snowflake import SnowflakeConnector
        return SnowflakeConnector(connection)
    raise ValueError(f"Unsupported database type: {source_type}")
