"""Unit tests for the shared Snowflake store plumbing."""
from __future__ import annotations

import pytest

fakesnow = pytest.importorskip("fakesnow")
import snowflake.connector  # noqa: E402


@pytest.fixture
def sf_conn():
    """A fakesnow-backed Snowflake connection on GM.PUB."""
    with fakesnow.patch():
        conn = snowflake.connector.connect(database="GM", schema="PUB")
        conn.cursor().execute("CREATE SCHEMA IF NOT EXISTS GM.PUB")
        yield conn
        conn.close()


def test_resolve_connection_passes_through_a_live_connection(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import resolve_connection

    assert resolve_connection(sf_conn, database="GM", schema="PUB") is sf_conn


def test_resolve_connection_unwraps_a_snowpark_session(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import resolve_connection

    class FakeSession:
        connection = sf_conn

    assert resolve_connection(
        FakeSession(), database="GM", schema="PUB"
    ) is sf_conn


def test_resolve_connection_rejects_none() -> None:
    from goldenmatch.snowflake._store_sql import resolve_connection

    with pytest.raises(ValueError, match="requires connection="):
        resolve_connection(None, database="GM", schema="PUB")


def test_case_insensitive_row_reads_lowercase_keys(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import execute, fetchone_row

    execute(sf_conn, "CREATE TABLE t (entity_id STRING, confidence FLOAT)")
    execute(sf_conn, "INSERT INTO t VALUES (%s, %s)", ("e1", 0.75))
    row = fetchone_row(sf_conn, "SELECT entity_id, confidence FROM t")
    assert row is not None
    assert row["entity_id"] == "e1"
    assert row["ENTITY_ID"] == "e1"
    assert row["confidence"] == 0.75


def test_fetchone_row_returns_none_when_empty(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import execute, fetchone_row

    execute(sf_conn, "CREATE TABLE t2 (a STRING)")
    assert fetchone_row(sf_conn, "SELECT a FROM t2") is None


def test_fetchall_rows_returns_every_row(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import execute, fetchall_rows

    execute(sf_conn, "CREATE TABLE t3 (a STRING)")
    execute(sf_conn, "INSERT INTO t3 VALUES (%s)", ("x",))
    execute(sf_conn, "INSERT INTO t3 VALUES (%s)", ("y",))
    rows = fetchall_rows(sf_conn, "SELECT a FROM t3 ORDER BY a")
    assert [r["a"] for r in rows] == ["x", "y"]
