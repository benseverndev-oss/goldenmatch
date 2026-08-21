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


def test_ensure_schema_creates_every_identity_table(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import (
        IDENTITY_DDL,
        ensure_schema,
        fetchall_rows,
    )

    ensure_schema(sf_conn, IDENTITY_DDL, database="GM", schema="PUB", version=7)
    rows = fetchall_rows(
        sf_conn,
        "SELECT table_name FROM GM.information_schema.tables "
        "WHERE table_schema = %s",
        ("PUB",),
    )
    names = {r["table_name"].lower() for r in rows}
    for expected in (
        "identity_nodes", "source_records", "evidence_edges",
        "identity_events", "audit_seals", "identity_aliases",
        "identity_record_block_keys", "identity_relationships",
        "identity_runs",
    ):
        assert expected in names, f"{expected} missing from {sorted(names)}"


def test_ensure_schema_is_idempotent(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import (
        IDENTITY_DDL,
        ensure_schema,
        execute,
        fetchone_row,
        schema_version,
    )

    ensure_schema(sf_conn, IDENTITY_DDL, database="GM", schema="PUB", version=7)
    execute(
        sf_conn,
        "INSERT INTO identity_nodes (entity_id, status) VALUES (%s, %s)",
        ("e1", "active"),
    )
    # Re-running must not drop the row or duplicate the version marker.
    ensure_schema(sf_conn, IDENTITY_DDL, database="GM", schema="PUB", version=7)
    row = fetchone_row(sf_conn, "SELECT COUNT(*) AS n FROM identity_nodes")
    assert row is not None and row["n"] == 1
    assert schema_version(sf_conn) == 7


def test_autoincrement_ids_are_assigned(sf_conn) -> None:
    from goldenmatch.snowflake._store_sql import (
        IDENTITY_DDL,
        ensure_schema,
        execute,
        fetchall_rows,
    )

    ensure_schema(sf_conn, IDENTITY_DDL, database="GM", schema="PUB", version=7)
    for kind in ("created", "merged"):
        execute(
            sf_conn,
            "INSERT INTO identity_events (entity_id, kind) VALUES (%s, %s)",
            ("e1", kind),
        )
    rows = fetchall_rows(
        sf_conn, "SELECT event_id, kind FROM identity_events ORDER BY event_id"
    )
    assert [r["event_id"] for r in rows] == [1, 2]
