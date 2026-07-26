"""The Postgres bulk write paths must carry the same provenance the per-row
paths and the SQLite bulk paths carry -- the #2132 payload-drop trap, which was
closed for SQLite but left open on the Postgres bulk COPY paths.

Regression under audit (thesis conformance, decision 0047, weakness
``no-versioned-resolution-batch-contract``): the Postgres bulk_* methods
selected a "leaner column list" that dropped columns the schema HAS and the
per-row path WRITES:
  - bulk_upsert_records dropped ``payload`` (source_records.payload JSONB)
  - bulk_add_edges dropped ``controller_snapshot`` / ``actor`` / ``trust``
  - bulk_emit_events dropped ``payload`` / ``actor`` / ``trust``

So which evidence survived the compute->control seam depended on the storage
backend + flush path. These tests inspect the generated COPY + INSERT SQL (no
live Postgres needed) and assert the provenance columns are carried.
"""
from __future__ import annotations

import contextlib

import polars as pl
from goldenmatch.identity.store import IdentityStore


class _FakeCopy:
    def __init__(self, sql: str, rows: list):
        self.sql = sql
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def write_row(self, row):
        self._rows.append(row)


class _FakeCursor:
    def __init__(self, log: list[str], copies: list[str], rows: list):
        self._log = log
        self._copies = copies
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql: str, *a):
        self._log.append(sql)

    def copy(self, sql: str):
        self._copies.append(sql)
        return _FakeCopy(sql, self._rows)


class _FakeConn:
    def __init__(self):
        self.executed: list[str] = []
        self.copies: list[str] = []
        self.rows: list = []

    @contextlib.contextmanager
    def transaction(self):
        yield

    def cursor(self):
        return _FakeCursor(self.executed, self.copies, self.rows)


def _pg_store() -> tuple[IdentityStore, _FakeConn]:
    store = IdentityStore.__new__(IdentityStore)
    store._backend = "postgres"
    conn = _FakeConn()
    store._conn = conn
    return store, conn


def _all_sql(conn: _FakeConn) -> str:
    return "\n".join(conn.executed + conn.copies)


def test_bulk_add_edges_postgres_carries_edge_provenance():
    store, conn = _pg_store()
    df = pl.DataFrame({
        "entity_id": ["e1"],
        "record_a_id": ["crm:a"],
        "record_b_id": ["crm:b"],
        "kind": ["same_as"],
        "score": [0.99],
        "matchkey_name": ["mk"],
        "controller_snapshot": ['{"stop_reason": "converged"}'],
        "run_name": ["run1"],
        "dataset": ["crm"],
        "actor": ["pipeline"],
        "trust": [0.99],
        "recorded_at": ["2026-07-25T00:00:00"],
    })
    store.bulk_add_edges(df)
    sql = _all_sql(conn)
    for col in ("controller_snapshot", "actor", "trust"):
        assert col in sql, f"bulk_add_edges dropped {col!r} on Postgres:\n{sql}"
    # The staged row carries the actual provenance values, not NULLs.
    assert conn.rows and '{"stop_reason": "converged"}' in conn.rows[0]


def test_bulk_emit_events_postgres_carries_audit_spine():
    store, conn = _pg_store()
    df = pl.DataFrame({
        "entity_id": ["e1"],
        "kind": ["same_as"],
        "payload": ['{"reason": "brand new"}'],
        "run_name": ["run1"],
        "dataset": ["crm"],
        "actor": ["pipeline"],
        "trust": [1.0],
        "recorded_at": ["2026-07-25T00:00:00"],
    })
    store.bulk_emit_events(df)
    sql = _all_sql(conn)
    for col in ("payload", "actor", "trust"):
        assert col in sql, f"bulk_emit_events dropped {col!r} on Postgres:\n{sql}"
    assert conn.rows and '{"reason": "brand new"}' in conn.rows[0]


def test_bulk_upsert_records_postgres_carries_payload():
    store, conn = _pg_store()
    df = pl.DataFrame({
        "record_id": ["crm:a"],
        "source": ["crm"],
        "source_pk": ["a"],
        "record_hash": ["h"],
        "entity_id": ["e1"],
        "payload": ['{"name": "Ada"}'],
        "dataset": ["crm"],
        "first_seen_at": ["2026-07-25T00:00:00"],
        "last_seen_at": ["2026-07-25T00:00:00"],
    })
    store.bulk_upsert_records(df)
    sql = _all_sql(conn)
    assert "payload" in sql, f"bulk_upsert_records dropped payload on Postgres:\n{sql}"
    # ON CONFLICT must refresh payload too, else re-resolves keep a stale/NULL payload.
    assert "payload = EXCLUDED.payload" in sql or "payload=EXCLUDED.payload" in sql
    assert conn.rows and '{"name": "Ada"}' in conn.rows[0]
