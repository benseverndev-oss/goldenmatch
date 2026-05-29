"""Tests for the Postgres source/sink connector.

The connector lazy-imports ``psycopg`` inside ``_connect``; the tests
inject a fake module via ``sys.modules`` so no real driver -- and no
live database -- is needed in CI. We verify:

  * read() builds the right SELECT, returns a LazyFrame
  * write() dispatches by mode (append / upsert / replace)
  * upsert builds the right ``INSERT ... ON CONFLICT`` SQL
"""
from __future__ import annotations

import sys
import types
from typing import Any

import polars as pl
import pytest


class _FakeCursor:
    """In-memory stand-in for a psycopg cursor."""

    def __init__(self, rows: list[tuple] | None = None,
                 columns: list[str] | None = None) -> None:
        self._rows = rows or []
        self._columns = columns or []
        self.description = [(c, None) for c in self._columns]
        self.executed: list[tuple[str, Any]] = []
        self.many_executed: list[tuple[str, list[tuple]]] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        self.many_executed.append((sql, list(rows)))

    def fetchall(self) -> list[tuple]:
        return self._rows

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: Any) -> None:
        return None


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False
        self.closed = False

    def cursor(self) -> _FakeCursor:
        return self._cursor

    def commit(self) -> None:
        self.committed = True

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_psycopg(monkeypatch):
    """Install a fake ``psycopg`` module that records connection args."""
    captured: dict[str, Any] = {"conn": None, "cursor": None, "conn_arg": None}

    def make_connect(default_cursor: _FakeCursor):
        def _connect(*args: Any, **kwargs: Any):
            captured["conn_arg"] = (args, kwargs)
            conn = _FakeConn(default_cursor)
            captured["conn"] = conn
            captured["cursor"] = default_cursor
            return conn
        return _connect

    def install(cursor: _FakeCursor) -> dict[str, Any]:
        fake = types.ModuleType("psycopg")
        fake.connect = make_connect(cursor)  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "psycopg", fake)
        return captured

    return install


# ----- read ------------------------------------------------------------------


def test_read_runs_query_and_returns_lazyframe(fake_psycopg) -> None:
    from goldenmatch.connectors.postgres import PostgresConnector

    cur = _FakeCursor(
        rows=[(1, "alice"), (2, "bob")],
        columns=["id", "name"],
    )
    captured = fake_psycopg(cur)
    conn = PostgresConnector(config={})
    df = conn.read(
        {"query": "SELECT id, name FROM users", "connection": "postgresql://localhost/test"}
    ).collect()

    assert df.height == 2
    assert df.columns == ["id", "name"]
    assert df["name"].to_list() == ["alice", "bob"]
    assert cur.executed[0][0] == "SELECT id, name FROM users"
    assert captured["conn"].closed is True


def test_read_builds_select_when_table_given(fake_psycopg) -> None:
    from goldenmatch.connectors.postgres import PostgresConnector

    cur = _FakeCursor(rows=[], columns=["id"])
    fake_psycopg(cur)
    conn = PostgresConnector(config={})
    conn.read({"table": "people", "connection": "x"}).collect()
    assert cur.executed[0][0] == "SELECT * FROM people"


def test_read_appends_limit_to_table_select(fake_psycopg) -> None:
    from goldenmatch.connectors.postgres import PostgresConnector

    cur = _FakeCursor(rows=[], columns=["id"])
    fake_psycopg(cur)
    conn = PostgresConnector(config={})
    conn.read({"table": "people", "limit": 50, "connection": "x"}).collect()
    assert cur.executed[0][0] == "SELECT * FROM people LIMIT 50"


def test_read_requires_query_or_table(fake_psycopg) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.postgres import PostgresConnector

    fake_psycopg(_FakeCursor())
    conn = PostgresConnector(config={})
    with pytest.raises(ConnectorError, match="requires 'query' or 'table'"):
        conn.read({"connection": "x"}).collect()


def test_read_requires_connection(fake_psycopg, monkeypatch) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.postgres import PostgresConnector

    fake_psycopg(_FakeCursor())
    monkeypatch.delenv("DATABASE_URL", raising=False)
    conn = PostgresConnector(config={})
    with pytest.raises(ConnectorError, match="needs a connection"):
        conn.read({"query": "SELECT 1"}).collect()


def test_read_passes_params(fake_psycopg) -> None:
    from goldenmatch.connectors.postgres import PostgresConnector

    cur = _FakeCursor(rows=[], columns=["id"])
    fake_psycopg(cur)
    conn = PostgresConnector(config={})
    conn.read({
        "query": "SELECT id FROM t WHERE x = %s",
        "params": (42,),
        "connection": "x",
    }).collect()
    assert cur.executed[0][1] == (42,)


# ----- write -----------------------------------------------------------------


def test_write_append_uses_executemany(fake_psycopg) -> None:
    from goldenmatch.connectors.postgres import PostgresConnector

    cur = _FakeCursor()
    captured = fake_psycopg(cur)
    conn = PostgresConnector(config={})
    df = pl.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    conn.write(df, {"table": "people", "connection": "x"})

    assert len(cur.many_executed) == 1
    sql, rows = cur.many_executed[0]
    assert sql == 'INSERT INTO people ("id", "name") VALUES (%s, %s)'
    assert rows == [(1, "a"), (2, "b")]
    assert captured["conn"].committed is True


def test_write_replace_truncates_first(fake_psycopg) -> None:
    from goldenmatch.connectors.postgres import PostgresConnector

    cur = _FakeCursor()
    fake_psycopg(cur)
    conn = PostgresConnector(config={})
    df = pl.DataFrame({"id": [1]})
    conn.write(df, {"table": "people", "mode": "replace", "connection": "x"})
    assert cur.executed[0][0] == "TRUNCATE TABLE people"
    assert len(cur.many_executed) == 1


def test_write_upsert_builds_on_conflict(fake_psycopg) -> None:
    from goldenmatch.connectors.postgres import PostgresConnector

    cur = _FakeCursor()
    fake_psycopg(cur)
    conn = PostgresConnector(config={})
    df = pl.DataFrame({"id": [1], "name": ["a"]})
    conn.write(df, {
        "table": "people", "mode": "upsert", "key": "id", "connection": "x",
    })
    sql, rows = cur.many_executed[0]
    assert "ON CONFLICT (\"id\")" in sql
    assert "DO UPDATE SET \"name\" = EXCLUDED.\"name\"" in sql
    assert rows == [(1, "a")]


def test_write_upsert_composite_key(fake_psycopg) -> None:
    from goldenmatch.connectors.postgres import PostgresConnector

    cur = _FakeCursor()
    fake_psycopg(cur)
    conn = PostgresConnector(config={})
    df = pl.DataFrame({"a": [1], "b": [2], "v": [3]})
    conn.write(df, {
        "table": "t", "mode": "upsert", "key": ["a", "b"], "connection": "x",
    })
    sql, _ = cur.many_executed[0]
    assert "ON CONFLICT (\"a\", \"b\")" in sql
    assert "DO UPDATE SET \"v\" = EXCLUDED.\"v\"" in sql


def test_write_upsert_all_columns_are_key_uses_do_nothing(fake_psycopg) -> None:
    from goldenmatch.connectors.postgres import PostgresConnector

    cur = _FakeCursor()
    fake_psycopg(cur)
    conn = PostgresConnector(config={})
    df = pl.DataFrame({"a": [1], "b": [2]})
    conn.write(df, {
        "table": "t", "mode": "upsert", "key": ["a", "b"], "connection": "x",
    })
    sql, _ = cur.many_executed[0]
    assert "DO NOTHING" in sql


def test_write_upsert_requires_key(fake_psycopg) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.postgres import PostgresConnector

    fake_psycopg(_FakeCursor())
    conn = PostgresConnector(config={})
    df = pl.DataFrame({"id": [1]})
    with pytest.raises(ConnectorError, match="upsert requires"):
        conn.write(df, {"table": "t", "mode": "upsert", "connection": "x"})


def test_write_unknown_mode_raises(fake_psycopg) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.postgres import PostgresConnector

    fake_psycopg(_FakeCursor())
    conn = PostgresConnector(config={})
    df = pl.DataFrame({"id": [1]})
    with pytest.raises(ConnectorError, match="unknown mode"):
        conn.write(df, {"table": "t", "mode": "merge", "connection": "x"})


def test_write_skips_empty_dataframe(fake_psycopg) -> None:
    from goldenmatch.connectors.postgres import PostgresConnector

    cur = _FakeCursor()
    captured = fake_psycopg(cur)
    conn = PostgresConnector(config={})
    conn.write(pl.DataFrame(), {"table": "t", "connection": "x"})
    # _connect never called -> captured["conn"] still None
    assert captured["conn"] is None


# ----- registry --------------------------------------------------------------


def test_load_connector_postgres_alias() -> None:
    from goldenmatch.connectors.base import load_connector
    from goldenmatch.connectors.postgres import PostgresConnector

    conn = load_connector("postgres", {})
    assert isinstance(conn, PostgresConnector)
    conn2 = load_connector("postgresql", {})
    assert isinstance(conn2, PostgresConnector)


def test_missing_driver_gives_helpful_error(monkeypatch) -> None:
    """When psycopg isn't installed, ConnectorError says how to fix it."""
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.postgres import PostgresConnector

    # Force-hide psycopg from importlib.
    monkeypatch.setitem(sys.modules, "psycopg", None)
    conn = PostgresConnector(config={})
    with pytest.raises(ConnectorError, match="pip install goldenmatch\\[postgres\\]"):
        conn.read({"query": "SELECT 1", "connection": "x"}).collect()
