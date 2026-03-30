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


def _pg_type_to_infermap(pg_type: str) -> str:
    """Map a PostgreSQL data_type string to an infermap dtype."""
    t = (pg_type or "").lower().strip()
    if t in ("integer", "bigint", "smallint", "serial", "bigserial"):
        return "integer"
    if t in ("real", "double precision", "numeric", "decimal", "money"):
        return "float"
    if t == "boolean":
        return "boolean"
    if t == "date":
        return "date"
    if t in ("timestamp", "timestamp with time zone", "timestamp without time zone"):
        return "datetime"
    return "string"


def _duckdb_type_to_infermap(duckdb_type: str) -> str:
    """Map a DuckDB data_type string to an infermap dtype."""
    t = (duckdb_type or "").upper().strip()
    if t in ("INTEGER", "BIGINT", "SMALLINT", "TINYINT", "HUGEINT", "INT4", "INT8", "INT2", "INT1"):
        return "integer"
    if t in ("REAL", "FLOAT", "DOUBLE", "DECIMAL", "NUMERIC", "FLOAT4", "FLOAT8"):
        return "float"
    if t == "BOOLEAN":
        return "boolean"
    if t == "DATE":
        return "date"
    if t in ("TIMESTAMP", "TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ"):
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
            return self._extract_postgres(conn_info, table, sample_size)

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
            return self._extract_duckdb(conn_info, table, sample_size)

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

    def _extract_postgres(self, conn_info: dict, table: str, sample_size: int) -> SchemaInfo:
        import psycopg2

        try:
            conn = psycopg2.connect(
                host=conn_info["host"],
                port=conn_info["port"],
                user=conn_info["user"],
                password=conn_info["password"],
                dbname=conn_info["database"],
            )
        except Exception as exc:
            raise InferMapError(
                f"Cannot connect to PostgreSQL at {conn_info['host']}:{conn_info['port']}: {exc}"
            ) from exc

        try:
            cur = conn.cursor()

            # Get column info from information_schema
            cur.execute("""
                SELECT column_name, data_type, is_nullable
                FROM information_schema.columns
                WHERE table_name = %s
                ORDER BY ordinal_position
            """, (table,))
            columns = cur.fetchall()

            if not columns:
                raise InferMapError(f"Table '{table}' not found or has no columns")

            # Total row count
            cur.execute(f'SELECT COUNT(*) FROM "{table}"')
            total = cur.fetchone()[0]

            # Sample rows
            cur.execute(f'SELECT * FROM "{table}" LIMIT %s', (sample_size,))
            sample_rows = cur.fetchall()

            fields = []
            for col_idx, (col_name, data_type, is_nullable) in enumerate(columns):
                raw_samples = [row[col_idx] for row in sample_rows if row[col_idx] is not None]
                sample_values = [str(v) for v in raw_samples[:sample_size]]

                # Count nulls in sample
                null_count = sum(1 for row in sample_rows if row[col_idx] is None)
                total_sampled = len(sample_rows)
                null_rate = null_count / total_sampled if total_sampled > 0 else 0.0

                unique_count = len(set(str(v) for v in raw_samples))
                non_null_count = len(raw_samples)
                unique_rate = unique_count / non_null_count if non_null_count > 0 else 0.0

                fields.append(FieldInfo(
                    name=col_name,
                    dtype=_pg_type_to_infermap(data_type),
                    sample_values=sample_values,
                    null_rate=round(null_rate, 4),
                    unique_rate=round(unique_rate, 4),
                    value_count=total - int(null_rate * total),
                    metadata={"db_type": data_type},
                ))

            return SchemaInfo(fields=fields, source_name=f"{conn_info['database']}.{table}")
        finally:
            conn.close()

    def _extract_duckdb(self, conn_info: dict, table: str, sample_size: int) -> SchemaInfo:
        import duckdb

        db_path = conn_info.get("path", ":memory:")
        try:
            conn = duckdb.connect(db_path, read_only=True) if db_path != ":memory:" else duckdb.connect(":memory:")
        except Exception as exc:
            raise InferMapError(f"Cannot connect to DuckDB at {db_path!r}: {exc}") from exc

        try:
            # Get column info
            result = conn.execute("""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = ?
                ORDER BY ordinal_position
            """, [table]).fetchall()

            if not result:
                raise InferMapError(f"Table '{table}' not found in DuckDB: {db_path}")

            # Total count
            total = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]

            # Sample
            sample_rows = conn.execute(f'SELECT * FROM "{table}" USING SAMPLE {sample_size}').fetchall()

            fields = []
            for col_idx, (col_name, data_type) in enumerate(result):
                raw_samples = [row[col_idx] for row in sample_rows if row[col_idx] is not None]
                sample_values = [str(v) for v in raw_samples[:sample_size]]

                null_count = sum(1 for row in sample_rows if row[col_idx] is None)
                total_sampled = len(sample_rows)
                null_rate = null_count / total_sampled if total_sampled > 0 else 0.0

                unique_count = len(set(str(v) for v in raw_samples))
                non_null_count = len(raw_samples)
                unique_rate = unique_count / non_null_count if non_null_count > 0 else 0.0

                fields.append(FieldInfo(
                    name=col_name,
                    dtype=_duckdb_type_to_infermap(data_type),
                    sample_values=sample_values,
                    null_rate=round(null_rate, 4),
                    unique_rate=round(unique_rate, 4),
                    value_count=total - int(null_rate * total),
                    metadata={"db_type": data_type},
                ))

            return SchemaInfo(fields=fields, source_name=f"{db_path}:{table}")
        finally:
            conn.close()
