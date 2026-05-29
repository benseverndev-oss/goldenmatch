"""Tests for the MySQL / MariaDB connector.

Same shape as ``test_postgres.py`` -- ``pymysql`` is faked via
``sys.modules``. We verify read/write/upsert + the URI-style + kwargs-
style + env-var connection layers.
"""
from __future__ import annotations

import sys
import types
from typing import Any

import polars as pl
import pytest


class _FakeCursor:
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
def fake_pymysql(monkeypatch):
    captured: dict[str, Any] = {"conn": None, "cursor": None, "kwargs": None}

    def install(cursor: _FakeCursor) -> dict[str, Any]:
        def _connect(**kwargs: Any):
            captured["kwargs"] = kwargs
            conn = _FakeConn(cursor)
            captured["conn"] = conn
            captured["cursor"] = cursor
            return conn
        fake = types.ModuleType("pymysql")
        fake.connect = _connect  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pymysql", fake)
        return captured

    return install


# ----- connection sourcing ---------------------------------------------------


def test_connection_kwargs_dict(fake_pymysql) -> None:
    from goldenmatch.connectors.mysql import MySQLConnector

    cur = _FakeCursor(rows=[], columns=["x"])
    captured = fake_pymysql(cur)
    conn = MySQLConnector(config={})
    conn.read({
        "query": "SELECT 1",
        "connection": {
            "host": "db.example.com", "user": "u", "password": "p",
            "database": "myapp", "port": 3307,
        },
    }).collect()
    assert captured["kwargs"] == {
        "host": "db.example.com", "user": "u", "password": "p",
        "database": "myapp", "port": 3307,
    }


def test_connection_kwargs_uri(fake_pymysql) -> None:
    from goldenmatch.connectors.mysql import MySQLConnector

    cur = _FakeCursor(rows=[], columns=["x"])
    captured = fake_pymysql(cur)
    conn = MySQLConnector(config={})
    conn.read({
        "query": "SELECT 1",
        "connection": "mysql://u:p@db.example.com:3307/myapp",
    }).collect()
    assert captured["kwargs"]["host"] == "db.example.com"
    assert captured["kwargs"]["user"] == "u"
    assert captured["kwargs"]["password"] == "p"
    assert captured["kwargs"]["port"] == 3307
    assert captured["kwargs"]["database"] == "myapp"


def test_connection_kwargs_env(fake_pymysql, monkeypatch) -> None:
    from goldenmatch.connectors.mysql import MySQLConnector

    monkeypatch.setenv("MYSQL_URL", "mysql://root:@localhost/test")
    cur = _FakeCursor(rows=[], columns=["x"])
    captured = fake_pymysql(cur)
    conn = MySQLConnector(config={})
    conn.read({"query": "SELECT 1"}).collect()
    assert captured["kwargs"]["host"] == "localhost"
    assert captured["kwargs"]["user"] == "root"
    assert captured["kwargs"]["database"] == "test"


def test_connection_missing_raises(monkeypatch) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.mysql import MySQLConnector

    fake = types.ModuleType("pymysql")
    fake.connect = lambda **k: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pymysql", fake)
    monkeypatch.delenv("MYSQL_URL", raising=False)
    conn = MySQLConnector(config={})
    with pytest.raises(ConnectorError, match="needs a connection"):
        conn.read({"query": "SELECT 1"}).collect()


# ----- read ------------------------------------------------------------------


def test_read_query(fake_pymysql) -> None:
    from goldenmatch.connectors.mysql import MySQLConnector

    cur = _FakeCursor(rows=[(1, "a"), (2, "b")], columns=["id", "name"])
    fake_pymysql(cur)
    conn = MySQLConnector(config={})
    df = conn.read({
        "query": "SELECT id, name FROM t",
        "connection": {"host": "h", "user": "u"},
    }).collect()
    assert df.height == 2
    assert df.columns == ["id", "name"]


def test_read_table(fake_pymysql) -> None:
    from goldenmatch.connectors.mysql import MySQLConnector

    cur = _FakeCursor(rows=[], columns=["id"])
    fake_pymysql(cur)
    conn = MySQLConnector(config={})
    conn.read({"table": "people", "limit": 100,
               "connection": {"host": "h", "user": "u"}}).collect()
    assert cur.executed[0][0] == "SELECT * FROM people LIMIT 100"


# ----- write -----------------------------------------------------------------


def test_write_append(fake_pymysql) -> None:
    from goldenmatch.connectors.mysql import MySQLConnector

    cur = _FakeCursor()
    fake_pymysql(cur)
    conn = MySQLConnector(config={})
    df = pl.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    conn.write(df, {"table": "people",
                    "connection": {"host": "h", "user": "u"}})
    sql, rows = cur.many_executed[0]
    assert sql == "INSERT INTO people (`id`, `name`) VALUES (%s, %s)"
    assert rows == [(1, "a"), (2, "b")]


def test_write_replace_truncates(fake_pymysql) -> None:
    from goldenmatch.connectors.mysql import MySQLConnector

    cur = _FakeCursor()
    fake_pymysql(cur)
    conn = MySQLConnector(config={})
    df = pl.DataFrame({"id": [1]})
    conn.write(df, {"table": "t", "mode": "replace",
                    "connection": {"host": "h", "user": "u"}})
    assert cur.executed[0][0] == "TRUNCATE TABLE t"


def test_write_upsert_uses_on_duplicate_key(fake_pymysql) -> None:
    from goldenmatch.connectors.mysql import MySQLConnector

    cur = _FakeCursor()
    fake_pymysql(cur)
    conn = MySQLConnector(config={})
    df = pl.DataFrame({"id": [1], "name": ["a"]})
    conn.write(df, {
        "table": "t", "mode": "upsert", "key": "id",
        "connection": {"host": "h", "user": "u"},
    })
    sql, rows = cur.many_executed[0]
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "`name` = VALUES(`name`)" in sql
    assert rows == [(1, "a")]


def test_write_upsert_all_key_idempotent(fake_pymysql) -> None:
    """With every column in the key, ON DUPLICATE KEY UPDATE still needs an
    assignment list. We assign the key column to itself so the row stays
    untouched -- semantically equivalent to ``DO NOTHING``."""
    from goldenmatch.connectors.mysql import MySQLConnector

    cur = _FakeCursor()
    fake_pymysql(cur)
    conn = MySQLConnector(config={})
    df = pl.DataFrame({"a": [1], "b": [2]})
    conn.write(df, {
        "table": "t", "mode": "upsert", "key": ["a", "b"],
        "connection": {"host": "h", "user": "u"},
    })
    sql, _ = cur.many_executed[0]
    assert "ON DUPLICATE KEY UPDATE `a` = `a`" in sql


def test_write_upsert_requires_key(fake_pymysql) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.mysql import MySQLConnector

    fake_pymysql(_FakeCursor())
    conn = MySQLConnector(config={})
    df = pl.DataFrame({"id": [1]})
    with pytest.raises(ConnectorError, match="upsert requires"):
        conn.write(df, {"table": "t", "mode": "upsert",
                        "connection": {"host": "h", "user": "u"}})


def test_write_unknown_mode_raises(fake_pymysql) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.mysql import MySQLConnector

    fake_pymysql(_FakeCursor())
    conn = MySQLConnector(config={})
    df = pl.DataFrame({"id": [1]})
    with pytest.raises(ConnectorError, match="unknown mode"):
        conn.write(df, {"table": "t", "mode": "merge",
                        "connection": {"host": "h", "user": "u"}})


# ----- registry --------------------------------------------------------------


def test_load_connector_aliases() -> None:
    from goldenmatch.connectors.base import load_connector
    from goldenmatch.connectors.mysql import MySQLConnector

    for alias in ("mysql", "mariadb"):
        conn = load_connector(alias, {})
        assert isinstance(conn, MySQLConnector), alias


def test_missing_driver_gives_helpful_error(monkeypatch) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.mysql import MySQLConnector

    monkeypatch.setitem(sys.modules, "pymysql", None)
    conn = MySQLConnector(config={})
    with pytest.raises(ConnectorError, match="pip install goldenmatch\\[mysql\\]"):
        conn.read({"query": "SELECT 1",
                   "connection": {"host": "h", "user": "u"}}).collect()
