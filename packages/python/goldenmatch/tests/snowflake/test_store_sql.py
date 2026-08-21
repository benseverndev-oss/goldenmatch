"""Unit tests for the shared Snowflake store plumbing."""
from __future__ import annotations

import pytest

fakesnow = pytest.importorskip("fakesnow")


def test_live_mode_is_off_without_the_env_var(monkeypatch) -> None:
    monkeypatch.delenv("GOLDENMATCH_SNOWFLAKE_TEST_DSN", raising=False)
    from tests.snowflake.conftest import live_dsn

    assert live_dsn() is None


def test_live_mode_reads_the_env_var(monkeypatch) -> None:
    monkeypatch.setenv("GOLDENMATCH_SNOWFLAKE_TEST_DSN", "myacct")
    from tests.snowflake.conftest import live_dsn

    assert live_dsn() == "myacct"


def test_resolve_connection_passes_through_a_live_connection(sf_target) -> None:
    from goldenmatch.snowflake._store_sql import resolve_connection

    conn, database, schema = sf_target
    assert resolve_connection(conn, database=database, schema=schema) is conn


def test_resolve_connection_unwraps_a_snowpark_session(sf_target) -> None:
    from goldenmatch.snowflake._store_sql import resolve_connection

    conn, database, schema = sf_target

    class FakeSession:
        connection = conn

    assert resolve_connection(
        FakeSession(), database=database, schema=schema
    ) is conn


def test_resolve_connection_rejects_none() -> None:
    # Raises on the None check before it ever looks at database=/schema=, so
    # this is not a live write and needs no fixture -- the literals below are
    # placeholder values, never sent anywhere.
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


def test_ensure_schema_creates_every_identity_table(sf_target) -> None:
    from goldenmatch.snowflake._store_sql import (
        IDENTITY_DDL,
        ensure_schema,
        fetchall_rows,
    )

    conn, database, schema = sf_target
    ensure_schema(conn, IDENTITY_DDL, database=database, schema=schema, version=7)
    rows = fetchall_rows(
        conn,
        f"SELECT table_name FROM {database}.information_schema.tables "
        "WHERE table_schema = %s",
        (schema,),
    )
    names = {r["table_name"].lower() for r in rows}
    for expected in (
        "identity_nodes", "source_records", "evidence_edges",
        "identity_events", "audit_seals", "identity_aliases",
        "identity_record_block_keys", "identity_relationships",
        "identity_runs",
    ):
        assert expected in names, f"{expected} missing from {sorted(names)}"


def test_ensure_schema_is_idempotent(sf_target) -> None:
    from goldenmatch.snowflake._store_sql import (
        IDENTITY_DDL,
        ensure_schema,
        execute,
        fetchone_row,
        schema_version,
    )

    conn, database, schema = sf_target
    ensure_schema(conn, IDENTITY_DDL, database=database, schema=schema, version=7)
    execute(
        conn,
        "INSERT INTO identity_nodes (entity_id, status) VALUES (%s, %s)",
        ("e1", "active"),
    )
    # Re-running must not drop the row or duplicate the version marker.
    ensure_schema(conn, IDENTITY_DDL, database=database, schema=schema, version=7)
    row = fetchone_row(conn, "SELECT COUNT(*) AS n FROM identity_nodes")
    assert row is not None and row["n"] == 1
    assert schema_version(conn) == 7
    # Snowflake does not enforce the PRIMARY KEY on _gm_schema_version, so the
    # MERGE (not the constraint) is what prevents a duplicate marker row.
    marker_row = fetchone_row(
        conn, "SELECT COUNT(*) AS n FROM _gm_schema_version"
    )
    assert marker_row is not None and marker_row["n"] == 1


def test_autoincrement_ids_are_assigned(sf_target) -> None:
    from goldenmatch.snowflake._store_sql import (
        IDENTITY_DDL,
        ensure_schema,
        execute,
        fetchall_rows,
    )

    conn, database, schema = sf_target
    ensure_schema(conn, IDENTITY_DDL, database=database, schema=schema, version=7)
    for kind in ("created", "merged"):
        execute(
            conn,
            "INSERT INTO identity_events (entity_id, kind) VALUES (%s, %s)",
            ("e1", kind),
        )
    rows = fetchall_rows(
        conn, "SELECT event_id, kind FROM identity_events ORDER BY event_id"
    )
    assert [r["event_id"] for r in rows] == [1, 2]


@pytest.fixture
def schema_conn(sf_target):
    from goldenmatch.snowflake._store_sql import IDENTITY_DDL, ensure_schema

    conn, database, schema = sf_target
    ensure_schema(conn, IDENTITY_DDL, database=database, schema=schema, version=7)
    return conn


def test_merge_one_inserts_then_updates(schema_conn) -> None:
    from goldenmatch.snowflake._store_sql import fetchall_rows, merge_one

    for confidence in (0.5, 0.9):
        merge_one(
            schema_conn,
            "identity_nodes",
            ["entity_id"],
            {"entity_id": "e1", "status": "active", "confidence": confidence},
            update_cols=["status", "confidence"],
        )
    rows = fetchall_rows(
        schema_conn, "SELECT entity_id, confidence FROM identity_nodes"
    )
    assert len(rows) == 1, "MERGE must update in place, not append"
    assert rows[0]["confidence"] == 0.9


def test_merge_one_without_update_cols_is_insert_if_absent(schema_conn) -> None:
    from goldenmatch.snowflake._store_sql import fetchall_rows, merge_one

    for confidence in (0.5, 0.9):
        merge_one(
            schema_conn,
            "identity_nodes",
            ["entity_id"],
            {"entity_id": "e2", "status": "active", "confidence": confidence},
        )
    rows = fetchall_rows(
        schema_conn,
        "SELECT confidence FROM identity_nodes WHERE entity_id = %s",
        ("e2",),
    )
    assert len(rows) == 1
    assert rows[0]["confidence"] == 0.5, "second merge must not overwrite"


def test_merge_one_round_trips_a_variant_column(schema_conn) -> None:
    import json

    from goldenmatch.snowflake._store_sql import fetchone_row, merge_one

    merge_one(
        schema_conn,
        "identity_nodes",
        ["entity_id"],
        {
            "entity_id": "e3",
            "status": "active",
            "golden_record": json.dumps({"name": "Ada", "n": 1}),
        },
        update_cols=["status", "golden_record"],
        json_cols=["golden_record"],
    )
    row = fetchone_row(
        schema_conn,
        "SELECT golden_record FROM identity_nodes WHERE entity_id = %s",
        ("e3",),
    )
    assert row is not None
    assert json.loads(row["golden_record"]) == {"name": "Ada", "n": 1}


def test_stage_and_merge_upserts_a_batch(schema_conn, sf_target) -> None:
    from goldenmatch.snowflake._store_sql import fetchall_rows, stage_and_merge

    _conn, database, schema = sf_target
    first = [
        {"entity_id": "b1", "status": "active", "confidence": 0.1},
        {"entity_id": "b2", "status": "active", "confidence": 0.2},
    ]
    assert stage_and_merge(
        schema_conn, "identity_nodes", first, ["entity_id"],
        update_cols=["status", "confidence"], database=database, schema=schema,
    ) == 2
    second = [
        {"entity_id": "b2", "status": "retired", "confidence": 0.9},
        {"entity_id": "b3", "status": "active", "confidence": 0.3},
    ]
    stage_and_merge(
        schema_conn, "identity_nodes", second, ["entity_id"],
        update_cols=["status", "confidence"], database=database, schema=schema,
    )
    rows = fetchall_rows(
        schema_conn,
        "SELECT entity_id, status, confidence FROM identity_nodes "
        "ORDER BY entity_id",
    )
    assert [r["entity_id"] for r in rows] == ["b1", "b2", "b3"]
    assert rows[1]["status"] == "retired"
    assert rows[1]["confidence"] == 0.9


def test_stage_and_merge_on_empty_input_is_a_noop(schema_conn, sf_target) -> None:
    from goldenmatch.snowflake._store_sql import stage_and_merge

    _conn, database, schema = sf_target
    assert stage_and_merge(
        schema_conn, "identity_nodes", [], ["entity_id"],
        database=database, schema=schema,
    ) == 0


def test_stage_tables_do_not_survive(schema_conn, sf_target) -> None:
    from goldenmatch.snowflake._store_sql import fetchall_rows, stage_and_merge

    _conn, database, schema = sf_target
    stage_and_merge(
        schema_conn, "identity_nodes",
        [{"entity_id": "s1", "status": "active"}], ["entity_id"],
        database=database, schema=schema,
    )
    rows = fetchall_rows(
        schema_conn,
        f"SELECT table_name FROM {database}.information_schema.tables "
        "WHERE table_schema = %s",
        (schema,),
    )
    leaked = [r["table_name"] for r in rows if "STAGE" in r["table_name"].upper()]
    assert leaked == [], f"stage tables leaked: {leaked}"


def test_stage_and_merge_round_trips_a_variant_column(
    schema_conn, sf_target
) -> None:
    import json

    from goldenmatch.snowflake._store_sql import fetchone_row, stage_and_merge

    _conn, database, schema = sf_target
    rows = [
        {
            "entity_id": "v1",
            "status": "active",
            "golden_record": json.dumps({"name": "Grace", "n": 2}),
        },
    ]
    stage_and_merge(
        schema_conn, "identity_nodes", rows, ["entity_id"],
        update_cols=["status", "golden_record"],
        json_cols=["golden_record"],
        database=database, schema=schema,
    )
    row = fetchone_row(
        schema_conn,
        "SELECT golden_record FROM identity_nodes WHERE entity_id = %s",
        ("v1",),
    )
    assert row is not None
    assert json.loads(row["golden_record"]) == {"name": "Grace", "n": 2}


def test_stage_and_merge_cleans_up_stage_table_on_merge_failure(
    schema_conn, sf_target,
) -> None:
    from goldenmatch.snowflake._store_sql import fetchall_rows, stage_and_merge

    _conn, database, schema = sf_target
    # A key column that does not exist on the target forces the MERGE
    # statement itself to fail after the stage table has been created and
    # populated, exercising the `finally` cleanup path (not just the happy
    # path covered by test_stage_tables_do_not_survive).
    with pytest.raises(Exception):  # noqa: B017, PT011
        stage_and_merge(
            schema_conn, "identity_nodes",
            [{"entity_id": "f1", "status": "active"}],
            ["nonexistent_column"],
            database=database, schema=schema,
        )
    rows = fetchall_rows(
        schema_conn,
        f"SELECT table_name FROM {database}.information_schema.tables "
        "WHERE table_schema = %s",
        (schema,),
    )
    leaked = [r["table_name"] for r in rows if "STAGE" in r["table_name"].upper()]
    assert leaked == [], f"stage tables leaked after a MERGE failure: {leaked}"
