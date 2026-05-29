"""Tests for the DuckDB source/sink connector.

DuckDB is a pure-Python install, so we use a real in-memory database
here rather than mocking. That's also a much better test of the
zero-copy Polars round-trip via ``conn.register`` / ``.pl()``.
"""
from __future__ import annotations

import polars as pl
import pytest

duckdb = pytest.importorskip("duckdb")


@pytest.fixture
def conn():
    """Fresh in-memory DuckDB; reused across the test via the connector's
    ``config['conn']`` escape hatch so we don't reopen for each call."""
    c = duckdb.connect(":memory:")
    yield c
    c.close()


# ----- read ------------------------------------------------------------------


def test_read_table(conn) -> None:
    from goldenmatch.connectors.duckdb_source import DuckDBSourceConnector

    conn.execute("CREATE TABLE people (id INT, name VARCHAR)")
    conn.execute("INSERT INTO people VALUES (1, 'alice'), (2, 'bob')")
    sink = DuckDBSourceConnector(config={})
    df = sink.read({"table": "people", "conn": conn}).collect()
    assert df.height == 2
    assert df["name"].to_list() == ["alice", "bob"]


def test_read_query(conn) -> None:
    from goldenmatch.connectors.duckdb_source import DuckDBSourceConnector

    sink = DuckDBSourceConnector(config={})
    df = sink.read({
        "query": "SELECT 1 AS x, 'hi' AS y",
        "conn": conn,
    }).collect()
    assert df.row(0) == (1, "hi")


def test_read_table_with_limit(conn) -> None:
    from goldenmatch.connectors.duckdb_source import DuckDBSourceConnector

    conn.execute("CREATE TABLE t AS SELECT * FROM range(100) AS r(id)")
    sink = DuckDBSourceConnector(config={})
    df = sink.read({"table": "t", "limit": 5, "conn": conn}).collect()
    assert df.height == 5


def test_read_requires_query_or_table(conn) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.duckdb_source import DuckDBSourceConnector

    sink = DuckDBSourceConnector(config={})
    with pytest.raises(ConnectorError, match="requires 'query' or 'table'"):
        sink.read({"conn": conn}).collect()


def test_read_requires_path_when_no_conn(monkeypatch) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.duckdb_source import DuckDBSourceConnector

    monkeypatch.delenv("DUCKDB_PATH", raising=False)
    sink = DuckDBSourceConnector(config={})
    with pytest.raises(ConnectorError, match="needs a path"):
        sink.read({"query": "SELECT 1"}).collect()


def test_read_opens_file_path(tmp_path) -> None:
    from goldenmatch.connectors.duckdb_source import DuckDBSourceConnector

    db_path = tmp_path / "test.duckdb"
    setup = duckdb.connect(str(db_path))
    setup.execute("CREATE TABLE t (x INT)")
    setup.execute("INSERT INTO t VALUES (42)")
    setup.close()

    sink = DuckDBSourceConnector(config={})
    df = sink.read({"table": "t", "connection": str(db_path)}).collect()
    assert df.height == 1
    assert df["x"].to_list() == [42]


# ----- write -----------------------------------------------------------------


def test_write_append(conn) -> None:
    from goldenmatch.connectors.duckdb_source import DuckDBSourceConnector

    conn.execute("CREATE TABLE t (id INT, name VARCHAR)")
    sink = DuckDBSourceConnector(config={})
    df = pl.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    sink.write(df, {"table": "t", "conn": conn})
    out = conn.sql("SELECT * FROM t ORDER BY id").pl()
    assert out["name"].to_list() == ["a", "b"]


def test_write_replace_creates_or_replaces(conn) -> None:
    from goldenmatch.connectors.duckdb_source import DuckDBSourceConnector

    conn.execute("CREATE TABLE t (old_col INT)")
    conn.execute("INSERT INTO t VALUES (1)")
    sink = DuckDBSourceConnector(config={})
    df = pl.DataFrame({"id": [9], "name": ["x"]})
    sink.write(df, {"table": "t", "mode": "replace", "conn": conn})

    cols = [r[0] for r in conn.sql("DESCRIBE t").fetchall()]
    assert "id" in cols
    assert "old_col" not in cols
    assert conn.sql("SELECT id FROM t").fetchall() == [(9,)]


def test_write_upsert(conn) -> None:
    from goldenmatch.connectors.duckdb_source import DuckDBSourceConnector

    conn.execute("CREATE TABLE t (id INT PRIMARY KEY, name VARCHAR)")
    conn.execute("INSERT INTO t VALUES (1, 'old')")
    sink = DuckDBSourceConnector(config={})
    df = pl.DataFrame({"id": [1, 2], "name": ["new", "added"]})
    sink.write(df, {"table": "t", "mode": "upsert", "key": "id", "conn": conn})

    rows = conn.sql("SELECT id, name FROM t ORDER BY id").fetchall()
    assert rows == [(1, "new"), (2, "added")]


def test_write_upsert_requires_key(conn) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.duckdb_source import DuckDBSourceConnector

    conn.execute("CREATE TABLE t (id INT, name VARCHAR)")
    sink = DuckDBSourceConnector(config={})
    df = pl.DataFrame({"id": [1], "name": ["a"]})
    with pytest.raises(ConnectorError, match="upsert requires"):
        sink.write(df, {"table": "t", "mode": "upsert", "conn": conn})


def test_write_unknown_mode_raises(conn) -> None:
    from goldenmatch.connectors.base import ConnectorError
    from goldenmatch.connectors.duckdb_source import DuckDBSourceConnector

    sink = DuckDBSourceConnector(config={})
    df = pl.DataFrame({"id": [1]})
    with pytest.raises(ConnectorError, match="unknown mode"):
        sink.write(df, {"table": "t", "mode": "merge", "conn": conn})


def test_write_empty_df_skips(conn) -> None:
    from goldenmatch.connectors.duckdb_source import DuckDBSourceConnector

    conn.execute("CREATE TABLE t (id INT)")
    sink = DuckDBSourceConnector(config={})
    sink.write(pl.DataFrame(), {"table": "t", "conn": conn})
    assert conn.sql("SELECT count(*) FROM t").fetchone()[0] == 0


# ----- registry --------------------------------------------------------------


def test_load_connector_duckdb() -> None:
    from goldenmatch.connectors.base import load_connector
    from goldenmatch.connectors.duckdb_source import DuckDBSourceConnector

    c = load_connector("duckdb", {})
    assert isinstance(c, DuckDBSourceConnector)
