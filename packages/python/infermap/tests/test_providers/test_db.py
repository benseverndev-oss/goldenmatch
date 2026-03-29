"""Tests for DBProvider (SQLite)."""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from infermap.errors import InferMapError
from infermap.providers.db import DBProvider


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
