"""Shared Snowflake test fixtures.

Every suite here runs against ``fakesnow`` by default. Setting
``GOLDENMATCH_SNOWFLAKE_TEST_DSN`` runs the SAME suites against a live
warehouse instead -- which is the only way to check the three things a DuckDB
fake can get wrong: MERGE semantics, VARIANT round-tripping, and constraint
non-enforcement (DuckDB *does* enforce primary keys, so a duplicate-insert bug
can pass here and fail there, or the reverse).

Live runs create and drop real tables. Point the DSN at a scratch schema --
each test gets its own throwaway ``GM_TEST_<uuid>`` schema under the target
database (``SNOWFLAKE_DATABASE``, default ``GOLDENMATCH``), created before the
test and dropped in a ``finally`` after, so a live run cannot collide with a
concurrent run, and a mid-test failure cannot strand the schema.
"""
from __future__ import annotations

import contextlib
import os
import uuid
from typing import Any

import pytest


def live_dsn() -> str | None:
    """The live-Snowflake account/DSN, or None for the fakesnow default."""
    return os.environ.get("GOLDENMATCH_SNOWFLAKE_TEST_DSN") or None


@contextlib.contextmanager
def _connection() -> Any:
    dsn = live_dsn()
    if dsn is None:
        fakesnow = pytest.importorskip("fakesnow")
        import snowflake.connector  # noqa: PLC0415

        with fakesnow.patch():
            conn = snowflake.connector.connect(database="GM", schema="PUB")
            try:
                conn.cursor().execute("CREATE SCHEMA IF NOT EXISTS GM.PUB")
                yield conn, "GM", "PUB"
            finally:
                conn.close()
        return

    from goldenmatch.snowflake._store_sql import execute, resolve_connection

    # A fresh schema per test so a live run cannot collide with itself (a
    # concurrent run, or a leftover from a prior failed run) and cannot leave
    # the previous test's rows behind -- the same reasoning as the uuid4
    # stage-table suffix in _store_sql.py.
    schema = f"GM_TEST_{uuid.uuid4().hex[:8]}".upper()
    database = os.environ.get("SNOWFLAKE_DATABASE", "GOLDENMATCH")
    # resolve_connection's string-account branch passes schema= straight
    # through to snowflake.connector.connect(), and the connector does NOT
    # validate that a database/schema pair actually exists at connect time --
    # it can silently land the session with no usable current schema instead
    # of raising (snowflakedb/snowflake-connector-python#26). The schema we
    # ask for here does not exist yet by construction, so an explicit
    # CREATE SCHEMA + USE SCHEMA below is required; do not rely on the
    # connect() kwargs alone to set session context.
    conn = resolve_connection(dsn, database=database, schema=schema)
    try:
        execute(conn, f"CREATE SCHEMA IF NOT EXISTS {database}.{schema}")
        execute(conn, f"USE SCHEMA {database}.{schema}")
        yield conn, database, schema
    finally:
        # try/finally, not contextlib.suppress, around the DROP: a real
        # cleanup failure (permissions, network) should surface as a visible
        # teardown error rather than be swallowed -- but conn.close() must
        # still run either way, or a raised DROP leaks the connection on top
        # of the schema.
        try:
            execute(conn, f"DROP SCHEMA IF EXISTS {database}.{schema} CASCADE")
        finally:
            conn.close()


@pytest.fixture
def sf_conn():
    """A bare Snowflake connection scoped to the fixture's database/schema."""
    with _connection() as (conn, _db, _schema):
        yield conn


@pytest.fixture
def sf_target():
    """(conn, database, schema) for tests that need the qualified names."""
    with _connection() as triple:
        yield triple


@pytest.fixture
def store(sf_target):
    """An ``IdentityStore(backend="snowflake")`` on the fixture's schema.

    Deliberately does NOT call ``s.close()`` at teardown. ``IdentityStore.close()``
    for this backend just closes the underlying connection
    (``SnowflakeIdentityStore.close`` -> ``self._conn.close()``), and that
    connection belongs to ``sf_target`` / ``_connection()``, not to this
    fixture. Fixture teardown runs in reverse dependency order, so closing it
    here would close it *before* ``_connection()``'s ``finally`` gets to run
    ``DROP SCHEMA`` on the live path -- stranding the scratch schema instead
    of dropping it. ``sf_target`` owns the connection end to end and closes it
    itself, after the drop.
    """
    from goldenmatch.identity.store import IdentityStore

    conn, database, schema = sf_target
    s = IdentityStore(
        backend="snowflake", connection=conn,
        database=database, schema=schema,
    )
    yield s
