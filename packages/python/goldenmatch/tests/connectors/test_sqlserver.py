"""Tests for the SQL Server / Azure SQL connector.

Same shape as ``test_postgres.py``: a fake ``pyodbc`` module is
injected via ``sys.modules`` so no driver + no live database are
needed. We verify:

  * read() builds ``SELECT TOP N`` when ``limit`` is set
  * write() dispatches by mode
  * upsert builds a valid T-SQL MERGE
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
        self.fast_executemany = False

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
def fake_pyodbc(monkeypatch):
    captured: dict[str, Any] = {"conn": None, "cursor": None}

    def install(cursor: _FakeCursor) -> dict[str, Any]:
        def _connect(*args: Any, **kwargs: Any):
            conn = _FakeConn(cursor)
            captured["conn"] = conn
            captured["cursor"] = cursor
            return conn
        fake = types.ModuleType("pyodbc")
        fake.connect = _connect  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pyodbc", fake)
        return captured

    return install


# ----- read ------------------------------------------------------------------


def test_read_runs_query(fake_pyodbc) -> None:
    from goldenmatch.connectors.sqlserver import SqlServerConnector

    cur = _FakeCursor(rows=[(1, "alice")], columns=["id", "name"])
    fake_pyodbc(cur)
    conn = SqlServerConnector(config={})
    df = conn.read({"query": "SELECT id, name FROM dbo.users", "connection": "DSN=x"}).collect()
    assert df.height == 1
    assert df.columns == ["id", "name"]
    assert cur.executed[0][0] == "SELECT id, name FROM dbo.users"


def test_read_table_uses_select_top_when_limit_set(fake_pyodbc) -> None:
    from goldenmatch.connectors.sqlserver import SqlServerConnector

    cur = _FakeCursor(rows=[], columns=["id"])
    fake_pyodbc(cur)
    conn = SqlServerConnector(config={})
    conn.read({"table": "dbo.people", "limit": 10, "connection": "x"}).collect()
    assert cur.executed[0][0] == "SELECT TOP 10 * FROM dbo.people"


def test_read_table_no_limit_is_select_star(fake_pyodbc) -> None:
    from goldenmatch.connectors.sqlserver import SqlServerConnector

    cur = _FakeCursor(rows=[], columns=["id"])
    fake_pyodbc(cur)
    conn = SqlServerConnector(config={})
    conn.read({"table": "dbo.people", "connection": "x"}).collect()
    assert cur.executed[0][0] == "SELECT * FROM dbo.people"


def test_read_requires_connection(monkeypatch) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.sqlserver import SqlServerConnector

    monkeypatch.delenv("SQLSERVER_CONNECTION", raising=False)
    # Inject pyodbc so the import succeeds; the connection check fires first.
    fake = types.ModuleType("pyodbc")
    fake.connect = lambda *a, **k: None  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "pyodbc", fake)

    conn = SqlServerConnector(config={})
    with pytest.raises(ConnectorError, match="needs a connection"):
        conn.read({"query": "SELECT 1"}).collect()


# ----- write -----------------------------------------------------------------


def test_write_append_executemany(fake_pyodbc) -> None:
    from goldenmatch.connectors.sqlserver import SqlServerConnector

    cur = _FakeCursor()
    captured = fake_pyodbc(cur)
    conn = SqlServerConnector(config={})
    df = pl.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    conn.write(df, {"table": "dbo.people", "connection": "x"})

    sql, rows = cur.many_executed[0]
    assert sql == "INSERT INTO dbo.people ([id], [name]) VALUES (?, ?)"
    assert rows == [(1, "a"), (2, "b")]
    assert cur.fast_executemany is True
    assert captured["conn"].committed is True


def test_write_replace_truncates(fake_pyodbc) -> None:
    from goldenmatch.connectors.sqlserver import SqlServerConnector

    cur = _FakeCursor()
    fake_pyodbc(cur)
    conn = SqlServerConnector(config={})
    df = pl.DataFrame({"id": [1]})
    conn.write(df, {"table": "dbo.t", "mode": "replace", "connection": "x"})
    assert cur.executed[0][0] == "TRUNCATE TABLE dbo.t"


def test_write_upsert_builds_merge(fake_pyodbc) -> None:
    from goldenmatch.connectors.sqlserver import SqlServerConnector

    cur = _FakeCursor()
    fake_pyodbc(cur)
    conn = SqlServerConnector(config={})
    df = pl.DataFrame({"id": [1], "name": ["a"]})
    conn.write(df, {
        "table": "dbo.people", "mode": "upsert", "key": "id", "connection": "x",
    })
    sql, rows = cur.many_executed[0]
    assert sql.startswith("MERGE INTO dbo.people AS target USING ")
    assert "ON target.[id] = source.[id]" in sql
    assert "WHEN MATCHED THEN UPDATE SET [name] = source.[name]" in sql
    assert "WHEN NOT MATCHED THEN INSERT ([id], [name])" in sql
    assert rows == [(1, "a")]


def test_write_upsert_requires_key(fake_pyodbc) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.sqlserver import SqlServerConnector

    fake_pyodbc(_FakeCursor())
    conn = SqlServerConnector(config={})
    df = pl.DataFrame({"id": [1]})
    with pytest.raises(ConnectorError, match="upsert requires"):
        conn.write(df, {"table": "t", "mode": "upsert", "connection": "x"})


def test_write_empty_df_skips_connection(fake_pyodbc) -> None:
    from goldenmatch.connectors.sqlserver import SqlServerConnector

    cur = _FakeCursor()
    captured = fake_pyodbc(cur)
    conn = SqlServerConnector(config={})
    conn.write(pl.DataFrame(), {"table": "t", "connection": "x"})
    assert captured["conn"] is None


# ----- registry --------------------------------------------------------------


def test_load_connector_aliases() -> None:
    from goldenmatch.connectors.base import load_connector
    from goldenmatch.connectors.sqlserver import SqlServerConnector

    for alias in ("sqlserver", "mssql", "azure_sql"):
        conn = load_connector(alias, {})
        assert isinstance(conn, SqlServerConnector), alias


def test_missing_driver_gives_helpful_error(monkeypatch) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.sqlserver import SqlServerConnector

    monkeypatch.setitem(sys.modules, "pyodbc", None)
    conn = SqlServerConnector(config={})
    with pytest.raises(ConnectorError, match="pip install goldenmatch\\[sqlserver\\]"):
        conn.read({"query": "SELECT 1", "connection": "x"}).collect()
