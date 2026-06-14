"""SQL-injection hardening regression tests for DBProvider.

SQLite-only (stdlib) and deliberately in their OWN file: test_db.py gates the
whole module behind a top-level ``importorskip("psycopg2")``, so its sqlite
cases don't run in the infermap CI lane (which installs only ``.[dev]``). These
must run unconditionally — they guard the parameterization/identifier-quoting in
providers/db.py.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from infermap.errors import InferMapError
from infermap.providers.db import DBProvider, _quote_ident


@pytest.fixture
def sqlite_db(tmp_path):
    db_path = tmp_path / "inj.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("CREATE TABLE contacts (id INTEGER PRIMARY KEY, name TEXT)")
    conn.executemany(
        "INSERT INTO contacts (id, name) VALUES (?, ?)",
        [(1, "Alice"), (2, "Bob"), (3, "Carol")],
    )
    conn.commit()
    conn.close()
    return db_path


def _uri(db_path: Path) -> str:
    return f"sqlite:///{db_path}"


def _table_exists(db_path: Path, table: str) -> bool:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def test_quote_ident_doubles_embedded_quotes():
    assert _quote_ident("plain") == '"plain"'
    assert _quote_ident('we"ird') == '"we""ird"'
    # The classic break-out attempt is neutralised: the payload becomes a single
    # (absurd) quoted identifier, not closeable SQL.
    assert _quote_ident('x" ; DROP TABLE t; --') == '"x"" ; DROP TABLE t; --"'


def test_malicious_sample_size_is_rejected_not_executed(sqlite_db):
    """A non-numeric sample_size must raise (int coercion in extract) rather than
    reach a query — and the table must be untouched."""
    with pytest.raises((ValueError, InferMapError)):
        DBProvider().extract(
            _uri(sqlite_db), table="contacts", sample_size="100; DROP TABLE contacts"
        )
    assert _table_exists(sqlite_db, "contacts")


def test_non_string_table_is_rejected(sqlite_db):
    with pytest.raises(InferMapError):
        DBProvider().extract(_uri(sqlite_db), table=12345)  # type: ignore[arg-type]


def test_injection_table_name_does_not_execute(sqlite_db):
    """An injection-style table name doesn't match a real table, so the
    parameterized existence check raises — no DROP runs."""
    with pytest.raises(InferMapError):
        DBProvider().extract(
            _uri(sqlite_db), table='contacts"; DROP TABLE contacts; --'
        )
    assert _table_exists(sqlite_db, "contacts")


def test_table_name_with_embedded_quote_round_trips(sqlite_db):
    """A real table whose name contains a double quote extracts correctly —
    proving the identifier escaping (not just rejection) is sound."""
    conn = sqlite3.connect(str(sqlite_db))
    conn.execute('CREATE TABLE "we""ird" (a INTEGER, b TEXT)')
    conn.execute('INSERT INTO "we""ird" VALUES (1, \'x\'), (2, \'y\')')
    conn.commit()
    conn.close()

    schema = DBProvider().extract(_uri(sqlite_db), table='we"ird')
    assert {f.name for f in schema.fields} == {"a", "b"}
