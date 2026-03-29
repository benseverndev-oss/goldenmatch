"""DBProvider — connects to databases and extracts SchemaInfo."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from infermap.errors import InferMapError
from infermap.types import FieldInfo, SchemaInfo

_DEFAULT_SAMPLE_SIZE = 500


def _sqlite_type_to_infermap(sqlite_type: str) -> str:
    """Map a SQLite declared type string to an infermap dtype."""
    t = (sqlite_type or "").upper().strip()
    if any(k in t for k in ("INT",)):
        return "integer"
    if any(k in t for k in ("REAL", "FLOAT", "DOUBLE", "NUMERIC", "DECIMAL")):
        return "float"
    if any(k in t for k in ("BOOL",)):
        return "boolean"
    if any(k in t for k in ("DATE",)) and "TIME" not in t:
        return "date"
    if any(k in t for k in ("DATETIME", "TIMESTAMP")):
        return "datetime"
    return "string"


def _parse_connection(uri: str) -> dict:
    """Parse a database URI and return a dict with driver and connection info."""
    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()

    if scheme == "sqlite":
        # sqlite:///path  -> parsed.path starts with /path on Unix
        # On Windows sqlite:///C:/path -> parsed.path = /C:/path
        # Strip exactly one leading slash per spec
        db_path = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
        return {"driver": "sqlite", "path": db_path}

    if scheme in ("postgresql", "postgres"):
        return {
            "driver": "postgresql",
            "host": parsed.hostname,
            "port": parsed.port or 5432,
            "user": parsed.username,
            "password": parsed.password,
            "database": parsed.path.lstrip("/"),
        }

    if scheme == "mysql":
        return {
            "driver": "mysql",
            "host": parsed.hostname,
            "port": parsed.port or 3306,
            "user": parsed.username,
            "password": parsed.password,
            "database": parsed.path.lstrip("/"),
        }

    if scheme == "duckdb":
        db_path = parsed.path[1:] if parsed.path.startswith("/") else parsed.path
        return {"driver": "duckdb", "path": db_path}

    raise InferMapError(f"Unsupported database scheme: {scheme!r}")


class DBProvider:
    """Extracts SchemaInfo from a database table."""

    def extract(self, source: Any, *, table: str, sample_size: int = _DEFAULT_SAMPLE_SIZE, **kwargs) -> SchemaInfo:
        conn_info = _parse_connection(str(source))
        driver = conn_info["driver"]

        if driver == "sqlite":
            return self._extract_sqlite(conn_info["path"], table, sample_size)

        if driver == "postgresql":
            try:
                import psycopg2  # noqa: F401
            except ImportError as exc:
                raise NotImplementedError(
                    "psycopg2 is required for PostgreSQL. Install with: pip install psycopg2-binary"
                ) from exc
            raise NotImplementedError("PostgreSQL support is not yet implemented.")

        if driver == "mysql":
            try:
                import mysql.connector  # noqa: F401
            except ImportError as exc:
                raise NotImplementedError(
                    "mysql-connector-python is required for MySQL. "
                    "Install with: pip install mysql-connector-python"
                ) from exc
            raise NotImplementedError("MySQL support is not yet implemented.")

        if driver == "duckdb":
            try:
                import duckdb  # noqa: F401
            except ImportError as exc:
                raise NotImplementedError(
                    "duckdb is required for DuckDB connections. Install with: pip install duckdb"
                ) from exc
            raise NotImplementedError("DuckDB support is not yet implemented.")

        raise InferMapError(f"Unsupported driver: {driver!r}")

    def _extract_sqlite(self, db_path: str, table: str, sample_size: int) -> SchemaInfo:
        import sqlite3

        try:
            conn = sqlite3.connect(db_path)
        except Exception as exc:
            raise InferMapError(f"Cannot connect to SQLite database at {db_path!r}: {exc}") from exc

        try:
            # Verify table exists
            cur = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            )
            if cur.fetchone() is None:
                raise InferMapError(f"Table {table!r} not found in SQLite database: {db_path}")

            # Get column info via PRAGMA
            pragma = conn.execute(f"PRAGMA table_info({table})").fetchall()
            # PRAGMA columns: (cid, name, type, notnull, dflt_value, pk)

            # Total row count
            total = conn.execute(f"SELECT COUNT(*) FROM \"{table}\"").fetchone()[0]

            # Fetch sample rows
            sample_rows = conn.execute(
                f"SELECT * FROM \"{table}\" LIMIT {sample_size}"
            ).fetchall()
            col_names = [row[1] for row in pragma]
            col_types = [row[2] for row in pragma]

            fields: list[FieldInfo] = []
            for col_idx, (col_name, col_type) in enumerate(zip(col_names, col_types)):
                # Null count for this column
                null_count = conn.execute(
                    f"SELECT COUNT(*) FROM \"{table}\" WHERE \"{col_name}\" IS NULL"
                ).fetchone()[0]

                null_rate = null_count / total if total > 0 else 0.0

                # Non-null samples
                raw_samples = [row[col_idx] for row in sample_rows if row[col_idx] is not None]
                sample_values = [str(v) for v in raw_samples[:sample_size]]

                unique_count = len(set(raw_samples))
                unique_rate = unique_count / total if total > 0 else 0.0

                fields.append(
                    FieldInfo(
                        name=col_name,
                        dtype=_sqlite_type_to_infermap(col_type),
                        sample_values=sample_values,
                        null_rate=null_rate,
                        unique_rate=unique_rate,
                        value_count=total,
                    )
                )

        finally:
            conn.close()

        return SchemaInfo(fields=fields, source_name=table)
