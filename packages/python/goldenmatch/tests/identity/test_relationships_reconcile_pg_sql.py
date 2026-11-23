"""The Postgres relationship reconcile's decisions, checked against the SQL it
issues -- no live Postgres needed, so this runs in every CI lane.

``test_relationships_reconcile_postgres.py`` proves the result equals the SQLite
reference against a real server, but it skips wherever no local Postgres can
start. This pins the parts a fake connection can see: what goes into the COPY,
when the delete/insert are skipped, and when the secondary indexes are dropped
and rebuilt around the insert.
"""
from __future__ import annotations

import contextlib
import re

from goldenmatch.identity.store import IdentityStore


class _FakeCopy:
    def __init__(self, rows: list):
        self._rows = rows

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def write_row(self, row):
        self._rows.append(row)


class _FakeCursor:
    """Answers the reconcile's count queries from a fixed table of numbers."""

    def __init__(self, conn: _FakeConn):
        self._conn = conn
        self._last = 0
        self.rowcount = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql: str, params=None):
        self._conn.sql.append(" ".join(sql.split()))
        c = self._conn.counts
        if sql.startswith("SELECT count(*) FROM _want w JOIN"):
            self._last = c["unchanged"]
        elif sql.startswith("SELECT count(*) FROM _want"):
            self._last = c["want"]
        elif sql.startswith("SELECT count(*) FROM identity_relationships r WHERE"):
            self._last = c["existing"]
        elif sql.startswith("SELECT count(*) FROM identity_relationships"):
            self._last = c["table"]
        elif sql.startswith("DELETE"):
            self.rowcount = c["deleted"]

    def fetchone(self):
        return (self._last,)

    def copy(self, sql: str):
        self._conn.sql.append(sql)
        return _FakeCopy(self._conn.copied)


class _FakeConn:
    def __init__(self, **counts):
        self.counts = {"want": 0, "unchanged": 0, "existing": 0, "table": 0, "deleted": 0}
        self.counts.update(counts)
        self.sql: list[str] = []
        self.copied: list[tuple] = []

    @contextlib.contextmanager
    def transaction(self):
        yield

    def cursor(self):
        return _FakeCursor(self)


def _store(conn: _FakeConn) -> IdentityStore:
    store = IdentityStore.__new__(IdentityStore)
    store._backend = "postgres"
    store._conn = conn
    store._active_pipeline = None
    return store


def _edges(n: int) -> list[tuple]:
    return [(f"e{i:06d}b", f"e{i:06d}a", "k", "f", str(i), "d") for i in range(n)]


def _ran(conn: _FakeConn, pattern: str) -> int:
    return sum(1 for s in conn.sql if re.search(pattern, s))


def test_copy_canonicalizes_endpoints_and_skips_self_and_null_edges() -> None:
    conn = _FakeConn(want=1, table=0)
    desired = [
        ("e2", "e1", "k", "f", "v", "d"),   # reversed -> canonicalized
        ("e3", "e3", "k", "f", "v", "d"),   # self edge -> skipped
        (None, "e4", "k", "f", "v", "d"),   # null endpoint -> skipped
    ]
    _store(conn).reconcile_relationships("d", "k", iter(desired))
    # ord carries the input position, so last-wins dedup sees the original order.
    assert conn.copied == [(0, "e1", "e2", "k", "f", "v")]


def test_warm_rerun_with_nothing_to_change_skips_delete_and_insert() -> None:
    conn = _FakeConn(want=5, unchanged=5, existing=5, table=5)
    assert _store(conn).reconcile_relationships("d", "k", iter(_edges(5))) == (0, 0, 5)
    assert _ran(conn, r"^DELETE") == 0
    assert _ran(conn, r"^INSERT INTO identity_relationships") == 0


def test_large_first_load_rebuilds_secondary_indexes_around_the_insert() -> None:
    conn = _FakeConn(want=200_000, unchanged=0, existing=0, table=0)
    result = _store(conn).reconcile_relationships("d", "k", iter(_edges(3)))
    assert result == (200_000, 0, 0)
    drops = [i for i, s in enumerate(conn.sql) if s.startswith("DROP INDEX")]
    insert = next(i for i, s in enumerate(conn.sql) if s.startswith("INSERT INTO identity_relationships"))
    creates = [i for i, s in enumerate(conn.sql) if s.startswith("CREATE INDEX")]
    assert len(drops) == 3 and len(creates) == 3
    assert max(drops) < insert < min(creates)
    assert {re.search(r"idx_rel_\w+", conn.sql[i]).group() for i in creates} == {
        "idx_rel_a", "idx_rel_b", "idx_rel_kind",
    }


def test_small_insert_into_a_large_table_keeps_the_indexes() -> None:
    # 150k new edges against a 9M-row table: well above the 100k floor, but
    # rebuilding 9M rows of index costs more than maintaining 150k.
    conn = _FakeConn(want=150_000, unchanged=0, existing=0, table=9_000_000)
    _store(conn).reconcile_relationships("d", "k", iter(_edges(3)))
    assert _ran(conn, r"^DROP INDEX") == 0
    assert _ran(conn, r"^CREATE INDEX") == 0
    assert _ran(conn, r"^INSERT INTO identity_relationships") == 1


def test_counts_follow_the_sqlite_contract() -> None:
    # inserted = desired - unchanged, deleted = the DELETE's rowcount.
    conn = _FakeConn(want=10, unchanged=4, existing=7, table=7, deleted=3)
    assert _store(conn).reconcile_relationships("d", "k", iter(_edges(10))) == (6, 3, 4)
