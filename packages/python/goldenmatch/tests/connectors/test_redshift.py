"""Tests for the Redshift source/sink connector.

``redshift_connector`` is faked via ``sys.modules`` so CI needs no
Redshift cluster. We verify:

  * read() runs the query, decodes rows into a LazyFrame
  * write() dispatches by mode (append / replace / upsert)
  * upsert builds the staging-table + DELETE + INSERT sequence
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
def fake_redshift(monkeypatch):
    captured: dict[str, Any] = {"conn": None, "kwargs": None}

    def install(cursor: _FakeCursor) -> dict[str, Any]:
        def _connect(**kwargs: Any):
            captured["kwargs"] = kwargs
            conn = _FakeConn(cursor)
            captured["conn"] = conn
            return conn
        fake = types.ModuleType("redshift_connector")
        fake.connect = _connect  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "redshift_connector", fake)
        return captured

    return install


# ----- connection sourcing ---------------------------------------------------


def test_connection_via_kwargs_dict(fake_redshift) -> None:
    from goldenmatch.connectors.redshift import RedshiftConnector

    cur = _FakeCursor(rows=[], columns=["id"])
    captured = fake_redshift(cur)
    conn = RedshiftConnector(config={})
    conn.read({
        "query": "SELECT 1",
        "connection": {
            "host": "cluster.region.redshift.amazonaws.com",
            "port": 5439, "user": "admin", "password": "p",
            "database": "dev",
        },
    }).collect()
    assert captured["kwargs"]["host"].endswith(".redshift.amazonaws.com")
    assert captured["kwargs"]["port"] == 5439


def test_connection_via_env(fake_redshift, monkeypatch) -> None:
    from goldenmatch.connectors.redshift import RedshiftConnector

    monkeypatch.setenv("REDSHIFT_HOST", "h")
    monkeypatch.setenv("REDSHIFT_USER", "u")
    monkeypatch.setenv("REDSHIFT_PASSWORD", "p")
    monkeypatch.setenv("REDSHIFT_DATABASE", "d")
    cur = _FakeCursor(rows=[], columns=["x"])
    captured = fake_redshift(cur)
    conn = RedshiftConnector(config={})
    conn.read({"query": "SELECT 1"}).collect()
    assert captured["kwargs"] == {
        "host": "h", "user": "u", "password": "p", "database": "d",
    }


def test_missing_connection_raises(monkeypatch) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.redshift import RedshiftConnector

    fake = types.ModuleType("redshift_connector")
    fake.connect = lambda **k: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "redshift_connector", fake)
    for var in ("REDSHIFT_HOST", "REDSHIFT_USER",
                "REDSHIFT_PASSWORD", "REDSHIFT_DATABASE"):
        monkeypatch.delenv(var, raising=False)
    conn = RedshiftConnector(config={})
    with pytest.raises(ConnectorError, match="needs a connection"):
        conn.read({"query": "SELECT 1"}).collect()


# ----- read ------------------------------------------------------------------


def test_read_query(fake_redshift) -> None:
    from goldenmatch.connectors.redshift import RedshiftConnector

    cur = _FakeCursor(rows=[(1, "alice")], columns=["id", "name"])
    fake_redshift(cur)
    conn = RedshiftConnector(config={})
    df = conn.read({
        "query": "SELECT id, name FROM users",
        "connection": {"host": "h", "user": "u", "database": "d"},
    }).collect()
    assert df.height == 1
    assert df.columns == ["id", "name"]
    assert cur.executed[0][0] == "SELECT id, name FROM users"


def test_read_table_with_limit(fake_redshift) -> None:
    from goldenmatch.connectors.redshift import RedshiftConnector

    cur = _FakeCursor(rows=[], columns=["id"])
    fake_redshift(cur)
    conn = RedshiftConnector(config={})
    conn.read({"table": "events", "limit": 100,
               "connection": {"host": "h", "user": "u", "database": "d"}}).collect()
    assert cur.executed[0][0] == "SELECT * FROM events LIMIT 100"


# ----- write -----------------------------------------------------------------


def test_write_append(fake_redshift) -> None:
    from goldenmatch.connectors.redshift import RedshiftConnector

    cur = _FakeCursor()
    captured = fake_redshift(cur)
    conn = RedshiftConnector(config={})
    df = pl.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    conn.write(df, {"table": "people",
                    "connection": {"host": "h", "user": "u", "database": "d"}})
    sql, rows = cur.many_executed[0]
    assert sql == 'INSERT INTO people ("id", "name") VALUES (%s, %s)'
    assert rows == [(1, "a"), (2, "b")]
    assert captured["conn"].committed is True


def test_write_replace_truncates(fake_redshift) -> None:
    from goldenmatch.connectors.redshift import RedshiftConnector

    cur = _FakeCursor()
    fake_redshift(cur)
    conn = RedshiftConnector(config={})
    df = pl.DataFrame({"id": [1]})
    conn.write(df, {"table": "t", "mode": "replace",
                    "connection": {"host": "h", "user": "u", "database": "d"}})
    assert cur.executed[0][0] == "TRUNCATE TABLE t"


def test_write_upsert_staging_sequence(fake_redshift) -> None:
    """Redshift upsert: CREATE TEMP -> INSERT staging -> DELETE target
    USING staging -> INSERT target SELECT staging -> DROP staging."""
    from goldenmatch.connectors.redshift import RedshiftConnector

    cur = _FakeCursor()
    fake_redshift(cur)
    conn = RedshiftConnector(config={})
    df = pl.DataFrame({"id": [1], "name": ["a"]})
    conn.write(df, {
        "table": "people", "mode": "upsert", "key": "id",
        "connection": {"host": "h", "user": "u", "database": "d"},
    })

    sql_steps = [s for s, _ in cur.executed]
    # The staging INSERT goes through executemany; the surrounding DDL/DML
    # are executes.
    assert any(s.startswith("CREATE TEMP TABLE") for s in sql_steps)
    assert any("DELETE FROM people USING" in s for s in sql_steps)
    assert any(s.startswith("INSERT INTO people SELECT * FROM") for s in sql_steps)
    assert any(s.startswith("DROP TABLE") for s in sql_steps)
    # The bulk insert into staging used executemany.
    assert any("goldenmatch_upsert_staging" in s for s, _ in cur.many_executed)


def test_write_upsert_requires_key(fake_redshift) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.redshift import RedshiftConnector

    fake_redshift(_FakeCursor())
    conn = RedshiftConnector(config={})
    df = pl.DataFrame({"id": [1]})
    with pytest.raises(ConnectorError, match="upsert requires"):
        conn.write(df, {"table": "t", "mode": "upsert",
                        "connection": {"host": "h", "user": "u", "database": "d"}})


def test_write_empty_df_skips(fake_redshift) -> None:
    from goldenmatch.connectors.redshift import RedshiftConnector

    cur = _FakeCursor()
    captured = fake_redshift(cur)
    conn = RedshiftConnector(config={})
    conn.write(pl.DataFrame(), {"table": "t",
                                "connection": {"host": "h", "user": "u", "database": "d"}})
    assert captured["conn"] is None


# ----- registry --------------------------------------------------------------


def test_load_connector_redshift() -> None:
    from goldenmatch.connectors.base import load_connector
    from goldenmatch.connectors.redshift import RedshiftConnector

    conn = load_connector("redshift", {})
    assert isinstance(conn, RedshiftConnector)


def test_missing_driver_gives_helpful_error(monkeypatch) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.redshift import RedshiftConnector

    monkeypatch.setitem(sys.modules, "redshift_connector", None)
    conn = RedshiftConnector(config={})
    with pytest.raises(ConnectorError, match="pip install goldenmatch\\[redshift\\]"):
        conn.read({"query": "SELECT 1",
                   "connection": {"host": "h", "user": "u", "database": "d"}}).collect()
