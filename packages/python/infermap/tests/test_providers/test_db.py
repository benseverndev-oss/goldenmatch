"""Tests for DBProvider (SQLite, PostgreSQL, DuckDB)."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from infermap.errors import InferMapError
from infermap.providers.db import DBProvider, _parse_connection


@pytest.fixture
def sqlite_db(tmp_path):
    """Create a SQLite DB with a 'contacts' table and 3 rows (1 null in 'email')."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """
        CREATE TABLE contacts (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            email TEXT,
            score REAL
        )
        """
    )
    conn.executemany(
        "INSERT INTO contacts (id, name, email, score) VALUES (?, ?, ?, ?)",
        [
            (1, "Alice", "alice@example.com", 9.5),
            (2, "Bob", None, 8.0),
            (3, "Carol", "carol@example.com", 7.5),
            # 1 null email out of 3 total (excluding PK auto) => null_rate ~ 0.333
        ],
    )
    conn.commit()
    conn.close()
    return db_path


def sqlite_uri(db_path: Path) -> str:
    # On Windows paths look like C:/Users/... — sqlite:/// + path
    return f"sqlite:///{db_path}"


def test_field_extraction(sqlite_db):
    provider = DBProvider()
    schema = provider.extract(sqlite_uri(sqlite_db), table="contacts")
    names = [f.name for f in schema.fields]
    assert "id" in names
    assert "name" in names
    assert "email" in names
    assert "score" in names


def test_samples_present(sqlite_db):
    provider = DBProvider()
    schema = provider.extract(sqlite_uri(sqlite_db), table="contacts")
    name_field = next(f for f in schema.fields if f.name == "name")
    assert len(name_field.sample_values) > 0


def test_null_rate(sqlite_db):
    """email has 1 null out of 3 rows => null_rate ~ 0.333."""
    provider = DBProvider()
    schema = provider.extract(sqlite_uri(sqlite_db), table="contacts")
    email_field = next(f for f in schema.fields if f.name == "email")
    assert abs(email_field.null_rate - (1 / 3)) < 0.01


def test_value_count(sqlite_db):
    provider = DBProvider()
    schema = provider.extract(sqlite_uri(sqlite_db), table="contacts")
    id_field = next(f for f in schema.fields if f.name == "id")
    assert id_field.value_count == 3


def test_dtype_integer(sqlite_db):
    provider = DBProvider()
    schema = provider.extract(sqlite_uri(sqlite_db), table="contacts")
    id_field = next(f for f in schema.fields if f.name == "id")
    assert id_field.dtype == "integer"


def test_dtype_float(sqlite_db):
    provider = DBProvider()
    schema = provider.extract(sqlite_uri(sqlite_db), table="contacts")
    score_field = next(f for f in schema.fields if f.name == "score")
    assert score_field.dtype == "float"


def test_missing_table_raises(sqlite_db):
    provider = DBProvider()
    with pytest.raises(InferMapError):
        provider.extract(sqlite_uri(sqlite_db), table="nonexistent_table")


def test_source_name_from_table(sqlite_db):
    provider = DBProvider()
    schema = provider.extract(sqlite_uri(sqlite_db), table="contacts")
    assert schema.source_name == "contacts"


psycopg2 = pytest.importorskip("psycopg2", reason="psycopg2 not installed")


class TestDBProviderPostgres:
    """PostgreSQL tests using mocks (no live server available)."""

    def test_parse_postgres_uri(self):
        info = _parse_connection("postgresql://user:pass@localhost:5432/mydb")
        assert info["driver"] == "postgresql"
        assert info["host"] == "localhost"
        assert info["port"] == 5432
        assert info["database"] == "mydb"

    def test_pg_type_mapping(self):
        from infermap.providers.db import _pg_type_to_infermap
        assert _pg_type_to_infermap("integer") == "integer"
        assert _pg_type_to_infermap("bigint") == "integer"
        assert _pg_type_to_infermap("double precision") == "float"
        assert _pg_type_to_infermap("boolean") == "boolean"
        assert _pg_type_to_infermap("date") == "date"
        assert _pg_type_to_infermap("timestamp without time zone") == "datetime"
        assert _pg_type_to_infermap("character varying") == "string"
        assert _pg_type_to_infermap("text") == "string"

    def test_postgres_extract_mocked(self):
        """Full extraction test using mocked psycopg2 connection."""
        from infermap.providers.db import _pg_type_to_infermap

        provider = DBProvider()

        # Build mock objects
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur

        # information_schema.columns response: (col_name, data_type, is_nullable)
        mock_cur.fetchall.side_effect = [
            [
                ("id", "integer", "NO"),
                ("name", "character varying", "YES"),
                ("score", "double precision", "YES"),
            ],
            # sample rows: fetchall for SELECT *
            [
                (1, "Alice", 9.5),
                (2, "Bob", None),
                (3, "Carol", 7.5),
            ],
        ]
        # fetchone calls: COUNT(*), then nothing more
        mock_cur.fetchone.return_value = (3,)
        # description for SELECT * cursor
        mock_cur.description = [
            MagicMock(name="col_desc_id"),
            MagicMock(name="col_desc_name"),
            MagicMock(name="col_desc_score"),
        ]
        mock_cur.description[0][0] = "id"
        mock_cur.description[1][0] = "name"
        mock_cur.description[2][0] = "score"

        with patch("psycopg2.connect", return_value=mock_conn):
            conn_info = {
                "host": "localhost",
                "port": 5432,
                "user": "user",
                "password": "pass",
                "database": "testdb",
            }
            schema = provider._extract_postgres(conn_info, "users", 500)

        names = [f.name for f in schema.fields]
        assert "id" in names
        assert "name" in names
        assert "score" in names
        id_field = next(f for f in schema.fields if f.name == "id")
        assert id_field.dtype == "integer"
        score_field = next(f for f in schema.fields if f.name == "score")
        assert score_field.dtype == "float"
        assert schema.source_name == "testdb.users"

    def test_postgres_connection_error(self):
        """Connection failure raises InferMapError."""
        provider = DBProvider()

        with patch("psycopg2.connect", side_effect=Exception("connection refused")):
            conn_info = {
                "host": "localhost",
                "port": 5432,
                "user": "user",
                "password": "pass",
                "database": "testdb",
            }
            with pytest.raises(InferMapError, match="Cannot connect to PostgreSQL"):
                provider._extract_postgres(conn_info, "users", 500)

    def test_postgres_missing_table(self):
        """Empty column result raises InferMapError."""
        provider = DBProvider()

        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value = mock_cur
        mock_cur.fetchall.return_value = []  # no columns found

        with patch("psycopg2.connect", return_value=mock_conn):
            conn_info = {
                "host": "localhost",
                "port": 5432,
                "user": "user",
                "password": "pass",
                "database": "testdb",
            }
            with pytest.raises(InferMapError, match="not found or has no columns"):
                provider._extract_postgres(conn_info, "nonexistent", 500)


duckdb = pytest.importorskip("duckdb", reason="duckdb not installed")


class TestDBProviderDuckDB:
    """DuckDB tests using in-memory database."""

    def setup_method(self):
        self.provider = DBProvider()

    def test_duckdb_extraction_file(self, tmp_path):
        import duckdb
        db_path = str(tmp_path / "test.duckdb")
        conn = duckdb.connect(db_path)
        conn.execute("CREATE TABLE customers (name VARCHAR, age INTEGER, email VARCHAR)")
        conn.execute("INSERT INTO customers VALUES ('Alice', 30, 'alice@test.com')")
        conn.execute("INSERT INTO customers VALUES ('Bob', 25, 'bob@test.com')")
        conn.execute("INSERT INTO customers VALUES (NULL, NULL, NULL)")
        conn.close()

        schema = self.provider.extract(f"duckdb:///{db_path}", table="customers")
        names = [f.name for f in schema.fields]
        assert "name" in names
        assert "age" in names
        assert "email" in names

    def test_duckdb_samples(self, tmp_path):
        import duckdb
        db_path = str(tmp_path / "test.duckdb")
        conn = duckdb.connect(db_path)
        conn.execute("CREATE TABLE t (val VARCHAR)")
        conn.execute("INSERT INTO t VALUES ('hello')")
        conn.execute("INSERT INTO t VALUES ('world')")
        conn.close()

        schema = self.provider.extract(f"duckdb:///{db_path}", table="t")
        assert len(schema.fields[0].sample_values) == 2

    def test_duckdb_null_rate(self, tmp_path):
        import duckdb
        db_path = str(tmp_path / "test.duckdb")
        conn = duckdb.connect(db_path)
        conn.execute("CREATE TABLE t (val VARCHAR)")
        conn.execute("INSERT INTO t VALUES ('a')")
        conn.execute("INSERT INTO t VALUES (NULL)")
        conn.execute("INSERT INTO t VALUES ('c')")
        conn.close()

        schema = self.provider.extract(f"duckdb:///{db_path}", table="t")
        assert schema.fields[0].null_rate == pytest.approx(1 / 3, abs=0.01)

    def test_duckdb_type_mapping(self):
        from infermap.providers.db import _duckdb_type_to_infermap
        assert _duckdb_type_to_infermap("INTEGER") == "integer"
        assert _duckdb_type_to_infermap("BIGINT") == "integer"
        assert _duckdb_type_to_infermap("DOUBLE") == "float"
        assert _duckdb_type_to_infermap("BOOLEAN") == "boolean"
        assert _duckdb_type_to_infermap("DATE") == "date"
        assert _duckdb_type_to_infermap("TIMESTAMP") == "datetime"
        assert _duckdb_type_to_infermap("VARCHAR") == "string"

    def test_duckdb_missing_table(self, tmp_path):
        import duckdb
        db_path = str(tmp_path / "empty.duckdb")
        conn = duckdb.connect(db_path)
        conn.close()

        with pytest.raises(InferMapError):
            self.provider.extract(f"duckdb:///{db_path}", table="nonexistent")

    def test_duckdb_dtype_integer(self, tmp_path):
        import duckdb
        db_path = str(tmp_path / "test.duckdb")
        conn = duckdb.connect(db_path)
        conn.execute("CREATE TABLE t (n INTEGER)")
        conn.execute("INSERT INTO t VALUES (42)")
        conn.close()

        schema = self.provider.extract(f"duckdb:///{db_path}", table="t")
        assert schema.fields[0].dtype == "integer"

    def test_duckdb_source_name(self, tmp_path):
        import duckdb
        db_path = str(tmp_path / "mydb.duckdb")
        conn = duckdb.connect(db_path)
        conn.execute("CREATE TABLE t (val VARCHAR)")
        conn.execute("INSERT INTO t VALUES ('x')")
        conn.close()

        schema = self.provider.extract(f"duckdb:///{db_path}", table="t")
        assert schema.source_name.endswith(":t")
