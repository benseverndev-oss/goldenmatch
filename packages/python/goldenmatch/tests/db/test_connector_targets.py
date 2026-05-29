"""Tests for the new ``db/`` sync-target connectors.

Mirror the test pattern from PR #566's ``connectors/`` tests: fake
drivers via ``sys.modules`` so CI needs no live MySQL / SQL Server /
Snowflake. We verify the same surface for each backend:

  * ``connect`` lazy-imports the driver, raises with an install hint
    when missing
  * ``read_table`` / ``read_query`` returns a DataFrame
  * ``write_dataframe`` dispatches by mode
  * ``execute`` / ``table_exists`` / ``get_row_count`` route correctly
  * ``create_connector`` factory resolves the right class by ``type``
"""
from __future__ import annotations

import sys
import types
from typing import Any

import polars as pl
import pytest


# ----- generic fakes ---------------------------------------------------------


class _FakeCursor:
    def __init__(
        self,
        rows: list[tuple] | None = None,
        columns: list[str] | None = None,
        scalar: Any = None,
    ) -> None:
        self._rows = list(rows or [])
        self._columns = columns or []
        self.description = [(c, None) for c in self._columns]
        self.executed: list[tuple[str, Any]] = []
        self.many_executed: list[tuple[str, list[tuple]]] = []
        self.fast_executemany = False
        self._scalar = scalar
        self._fetched = False

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append((sql, params))

    def executemany(self, sql: str, rows: list[tuple]) -> None:
        self.many_executed.append((sql, list(rows)))

    def fetchall(self) -> list[tuple]:
        return self._rows

    def fetchmany(self, n: int) -> list[tuple]:
        out, self._rows = self._rows[:n], self._rows[n:]
        return out

    def fetchone(self) -> tuple | None:
        if self._scalar is not None and not self._fetched:
            self._fetched = True
            return (self._scalar,)
        if self._rows:
            return self._rows[0]
        return None

    def close(self) -> None:
        return None


class _FakeConn:
    def __init__(self, cursors: list[_FakeCursor]) -> None:
        self._cursors = list(cursors)
        self.committed = False
        self.rolledback = False
        self.closed = False

    def cursor(self, *args: Any, **kwargs: Any) -> _FakeCursor:
        return self._cursors.pop(0) if self._cursors else _FakeCursor()

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolledback = True

    def close(self) -> None:
        self.closed = True


# ----- MySQL -----------------------------------------------------------------


@pytest.fixture
def install_pymysql(monkeypatch):
    captured: dict[str, Any] = {"kwargs": None, "conn": None}

    def install(cursors: list[_FakeCursor]) -> dict[str, Any]:
        def _connect(**kwargs: Any):
            captured["kwargs"] = kwargs
            conn = _FakeConn(cursors)
            captured["conn"] = conn
            return conn

        fake = types.ModuleType("pymysql")
        fake.connect = _connect  # type: ignore[attr-defined]
        cursors_mod = types.ModuleType("pymysql.cursors")
        cursors_mod.SSCursor = object  # type: ignore[attr-defined]
        fake.cursors = cursors_mod  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pymysql", fake)
        monkeypatch.setitem(sys.modules, "pymysql.cursors", cursors_mod)
        return captured

    return install


def test_mysql_connect_parses_uri(install_pymysql) -> None:
    from goldenmatch.db.connector_mysql import MySQLConnector

    captured = install_pymysql([])
    c = MySQLConnector("mysql://u:p@db.example.com:3307/myapp")
    c.connect()
    assert captured["kwargs"]["host"] == "db.example.com"
    assert captured["kwargs"]["port"] == 3307
    assert captured["kwargs"]["database"] == "myapp"
    assert captured["kwargs"]["autocommit"] is False


def test_mysql_connect_accepts_dict(install_pymysql) -> None:
    from goldenmatch.db.connector_mysql import MySQLConnector

    captured = install_pymysql([])
    kw = {"host": "h", "user": "u", "password": "p", "database": "d"}
    c = MySQLConnector(kw)
    c.connect()
    # The connector sets autocommit=False on the way through; everything
    # else passes verbatim.
    assert captured["kwargs"]["host"] == "h"
    assert captured["kwargs"]["autocommit"] is False


def test_mysql_missing_driver_raises_install_hint(monkeypatch) -> None:
    from goldenmatch.db.connector_mysql import MySQLConnector

    monkeypatch.setitem(sys.modules, "pymysql", None)
    with pytest.raises(ImportError, match="pip install 'goldenmatch\\[mysql\\]'"):
        MySQLConnector("mysql://u@h/d").connect()


def test_mysql_read_table_streams_chunks(install_pymysql) -> None:
    from goldenmatch.db.connector_mysql import MySQLConnector

    rows = [(i, f"n{i}") for i in range(5)]
    cur = _FakeCursor(rows=rows, columns=["id", "name"])
    install_pymysql([cur])
    c = MySQLConnector({"host": "h", "user": "u"})
    c.connect()
    chunks = list(c.read_table("people", chunk_size=2))
    assert len(chunks) == 3
    assert chunks[0].height == 2
    assert chunks[2].height == 1
    assert cur.executed[0][0] == "SELECT * FROM `people`"


def test_mysql_write_replace_truncates(install_pymysql) -> None:
    from goldenmatch.db.connector_mysql import MySQLConnector

    cur = _FakeCursor()
    captured = install_pymysql([cur])
    c = MySQLConnector({"host": "h", "user": "u"})
    c.connect()
    df = pl.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    n = c.write_dataframe(df, "people", mode="replace")

    assert n == 2
    assert cur.executed[0][0] == "TRUNCATE TABLE `people`"
    assert cur.many_executed[0][0] == (
        "INSERT INTO `people` (`id`, `name`) VALUES (%s, %s)"
    )
    assert captured["conn"].committed is True


def test_mysql_write_empty_df_returns_zero(install_pymysql) -> None:
    from goldenmatch.db.connector_mysql import MySQLConnector

    install_pymysql([_FakeCursor()])
    c = MySQLConnector({"host": "h", "user": "u"})
    c.connect()
    assert c.write_dataframe(pl.DataFrame(), "people") == 0


def test_mysql_table_exists_queries_information_schema(install_pymysql) -> None:
    from goldenmatch.db.connector_mysql import MySQLConnector

    cur = _FakeCursor(scalar=1)
    install_pymysql([cur])
    c = MySQLConnector({"host": "h", "user": "u"})
    c.connect()
    assert c.table_exists("people") is True
    assert "information_schema.tables" in cur.executed[0][0]
    assert cur.executed[0][1] == ("people",)


def test_mysql_row_count(install_pymysql) -> None:
    from goldenmatch.db.connector_mysql import MySQLConnector

    cur = _FakeCursor(scalar=42)
    install_pymysql([cur])
    c = MySQLConnector({"host": "h", "user": "u"})
    c.connect()
    assert c.get_row_count("people") == 42
    assert cur.executed[0][0] == "SELECT COUNT(*) FROM `people`"


# ----- SQL Server ------------------------------------------------------------


@pytest.fixture
def install_pyodbc(monkeypatch):
    captured: dict[str, Any] = {"conn": None}

    def install(cursors: list[_FakeCursor]) -> dict[str, Any]:
        def _connect(*args: Any, **kwargs: Any):
            conn = _FakeConn(cursors)
            captured["conn"] = conn
            return conn

        fake = types.ModuleType("pyodbc")
        fake.connect = _connect  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "pyodbc", fake)
        return captured

    return install


def test_sqlserver_missing_driver_raises_install_hint(monkeypatch) -> None:
    from goldenmatch.db.connector_sqlserver import SqlServerConnector

    monkeypatch.setitem(sys.modules, "pyodbc", None)
    with pytest.raises(ImportError, match="pip install 'goldenmatch\\[sqlserver\\]'"):
        SqlServerConnector("DSN=x").connect()


def test_sqlserver_read_query(install_pyodbc) -> None:
    from goldenmatch.db.connector_sqlserver import SqlServerConnector

    cur = _FakeCursor(rows=[(1, "alice")], columns=["id", "name"])
    install_pyodbc([cur])
    c = SqlServerConnector("DSN=x")
    c.connect()
    df = c.read_query("SELECT id, name FROM users")
    assert df.height == 1
    assert df["name"].to_list() == ["alice"]


def test_sqlserver_write_append_uses_fast_executemany(install_pyodbc) -> None:
    from goldenmatch.db.connector_sqlserver import SqlServerConnector

    cur = _FakeCursor()
    captured = install_pyodbc([cur])
    c = SqlServerConnector("DSN=x")
    c.connect()
    df = pl.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    n = c.write_dataframe(df, "dbo.people")
    assert n == 2
    assert cur.fast_executemany is True
    assert cur.many_executed[0][0] == (
        "INSERT INTO [dbo].[people] ([id], [name]) VALUES (?, ?)"
    )
    assert captured["conn"].committed is True


def test_sqlserver_write_replace_truncates(install_pyodbc) -> None:
    from goldenmatch.db.connector_sqlserver import SqlServerConnector

    cur = _FakeCursor()
    install_pyodbc([cur])
    c = SqlServerConnector("DSN=x")
    c.connect()
    df = pl.DataFrame({"id": [1]})
    c.write_dataframe(df, "dbo.people", mode="replace")
    assert cur.executed[0][0] == "TRUNCATE TABLE [dbo].[people]"


def test_sqlserver_table_exists(install_pyodbc) -> None:
    from goldenmatch.db.connector_sqlserver import SqlServerConnector

    cur = _FakeCursor(scalar=1)
    install_pyodbc([cur])
    c = SqlServerConnector("DSN=x")
    c.connect()
    # Schema-qualified table strips to the bare name for the lookup.
    assert c.table_exists("dbo.people") is True
    assert cur.executed[0][1] == ("people",)


# ----- Snowflake -------------------------------------------------------------


@pytest.fixture
def install_snowflake(monkeypatch):
    captured: dict[str, Any] = {"conn": None, "kwargs": None, "wp_calls": []}

    def install(
        cursors: list[_FakeCursor],
        wp_result: tuple[bool, int, int, list[Any]] = (True, 1, 0, []),
    ) -> dict[str, Any]:
        def _connect(**kwargs: Any):
            captured["kwargs"] = kwargs
            conn = _FakeConn(cursors)
            captured["conn"] = conn
            return conn

        fake_root = types.ModuleType("snowflake")
        fake_conn_mod = types.ModuleType("snowflake.connector")
        fake_conn_mod.connect = _connect  # type: ignore[attr-defined]
        fake_root.connector = fake_conn_mod  # type: ignore[attr-defined]
        fake_pandas_tools = types.ModuleType("snowflake.connector.pandas_tools")

        def _write_pandas(conn, pdf, table, auto_create_table=False, **_kw):
            captured["wp_calls"].append({
                "table": table, "rows": len(pdf),
                "auto_create_table": auto_create_table,
            })
            return wp_result

        fake_pandas_tools.write_pandas = _write_pandas  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "snowflake", fake_root)
        monkeypatch.setitem(sys.modules, "snowflake.connector", fake_conn_mod)
        monkeypatch.setitem(
            sys.modules, "snowflake.connector.pandas_tools", fake_pandas_tools
        )
        return captured

    return install


def test_snowflake_missing_driver_raises_install_hint(monkeypatch) -> None:
    from goldenmatch.db.connector_snowflake import SnowflakeConnector

    monkeypatch.setitem(sys.modules, "snowflake", None)
    with pytest.raises(ImportError, match="pip install 'goldenmatch\\[snowflake\\]'"):
        SnowflakeConnector({"account": "x"}).connect()


def test_snowflake_connect_via_dict(install_snowflake) -> None:
    from goldenmatch.db.connector_snowflake import SnowflakeConnector

    captured = install_snowflake([])
    SnowflakeConnector({"account": "a", "user": "u", "password": "p"}).connect()
    assert captured["kwargs"]["account"] == "a"


def test_snowflake_connect_string_pulls_from_env(install_snowflake, monkeypatch) -> None:
    from goldenmatch.db.connector_snowflake import SnowflakeConnector

    monkeypatch.setenv("SNOWFLAKE_USER", "admin")
    monkeypatch.setenv("SNOWFLAKE_PASSWORD", "pw")
    monkeypatch.setenv("SNOWFLAKE_DATABASE", "DB")
    captured = install_snowflake([])
    SnowflakeConnector("my-account").connect()
    assert captured["kwargs"] == {
        "account": "my-account", "user": "admin",
        "password": "pw", "database": "DB",
    }


def test_snowflake_write_uses_write_pandas(install_snowflake) -> None:
    from goldenmatch.db.connector_snowflake import SnowflakeConnector

    captured = install_snowflake([_FakeCursor()], wp_result=(True, 1, 3, []))
    c = SnowflakeConnector({"account": "a"})
    c.connect()
    df = pl.DataFrame({"id": [1, 2, 3]})
    n = c.write_dataframe(df, "T")
    assert n == 3
    assert captured["wp_calls"][0]["table"] == "T"
    assert captured["wp_calls"][0]["rows"] == 3


def test_snowflake_write_replace_truncates_first(install_snowflake) -> None:
    from goldenmatch.db.connector_snowflake import SnowflakeConnector

    truncate_cur = _FakeCursor()
    install_snowflake([truncate_cur], wp_result=(True, 1, 1, []))
    c = SnowflakeConnector({"account": "a"})
    c.connect()
    df = pl.DataFrame({"id": [1]})
    c.write_dataframe(df, "DB.SCHEMA.T", mode="replace")
    assert truncate_cur.executed[0][0] == (
        'TRUNCATE TABLE IF EXISTS "DB"."SCHEMA"."T"'
    )


def test_snowflake_write_pandas_failure_raises(install_snowflake) -> None:
    from goldenmatch.db.connector_snowflake import SnowflakeConnector

    install_snowflake([_FakeCursor()], wp_result=(False, 0, 0, []))
    c = SnowflakeConnector({"account": "a"})
    c.connect()
    df = pl.DataFrame({"id": [1]})
    with pytest.raises(RuntimeError, match="write_pandas reported failure"):
        c.write_dataframe(df, "T")


def test_snowflake_table_exists_uppercases(install_snowflake) -> None:
    from goldenmatch.db.connector_snowflake import SnowflakeConnector

    cur = _FakeCursor(scalar=1)
    install_snowflake([cur])
    c = SnowflakeConnector({"account": "a"})
    c.connect()
    assert c.table_exists("DB.PUBLIC.my_table") is True
    # information_schema treats Snowflake names as uppercase by default.
    assert cur.executed[0][1] == ("MY_TABLE",)


# ----- factory ---------------------------------------------------------------


def test_create_connector_resolves_each_type(monkeypatch) -> None:
    from goldenmatch.db.connector import create_connector
    from goldenmatch.db.connector_mysql import MySQLConnector
    from goldenmatch.db.connector_snowflake import SnowflakeConnector
    from goldenmatch.db.connector_sqlserver import SqlServerConnector

    monkeypatch.delenv("GOLDENMATCH_DATABASE_URL", raising=False)

    c_my = create_connector({"type": "mysql", "connection": "mysql://u@h/d"})
    assert isinstance(c_my, MySQLConnector)

    c_ms = create_connector({"type": "sqlserver", "connection": "DSN=x"})
    assert isinstance(c_ms, SqlServerConnector)

    c_sf = create_connector({"type": "snowflake", "connection": {"account": "a"}})
    assert isinstance(c_sf, SnowflakeConnector)


def test_create_connector_aliases(monkeypatch) -> None:
    from goldenmatch.db.connector import create_connector
    from goldenmatch.db.connector_mysql import MySQLConnector
    from goldenmatch.db.connector_sqlserver import SqlServerConnector

    monkeypatch.delenv("GOLDENMATCH_DATABASE_URL", raising=False)

    assert isinstance(
        create_connector({"type": "mariadb", "connection": "mysql://u@h/d"}),
        MySQLConnector,
    )
    assert isinstance(
        create_connector({"type": "mssql", "connection": "DSN=x"}),
        SqlServerConnector,
    )
    assert isinstance(
        create_connector({"type": "azure_sql", "connection": "DSN=y"}),
        SqlServerConnector,
    )


def test_create_connector_unknown_type_raises() -> None:
    from goldenmatch.db.connector import create_connector

    with pytest.raises(ValueError, match="Unsupported database type"):
        create_connector({"type": "oracle", "connection": "x"})
