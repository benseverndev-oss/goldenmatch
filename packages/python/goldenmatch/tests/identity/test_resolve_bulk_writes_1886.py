"""#1886: resolve_clusters must run its writes inside ONE transaction.

The Postgres IdentityStore connects with ``autocommit=True``. The bulk fast-path
already batches brand-new clusters into 4 COPY transactions, but the per-record
path (absorb/merge into existing identities, weak multi-member clusters) issued
every write on its own autocommit -- one COMMIT + network round-trip per write,
which is minutes of latency against a remote DB for ~20k records.

These tests use a fake ``_backend="postgres"`` store whose ``bulk_writes()``
records enter/exit and every write records its call order, then assert that
EVERY write on both the per-record and the bulk path falls inside a single
``bulk_writes()`` scope.
"""
from __future__ import annotations

import contextlib

import polars as pl
from goldenmatch.identity.resolve import resolve_clusters

_PER_RECORD_WRITES = {
    "upsert_identity", "upsert_record", "emit_event", "add_edge",
}
_BULK_WRITES = {
    "bulk_upsert_identities", "bulk_upsert_records",
    "bulk_add_edges", "bulk_emit_events",
}


class _TxnRecordingStore:
    """Fake postgres-backed store. Records the order of bulk_writes enter/exit
    and every write call so a test can assert writes are transaction-scoped."""

    _backend = "postgres"

    def __init__(self, preexisting: dict[str, str] | None = None):
        self.events: list[str] = []
        self._preexisting = preexisting or {}
        self._identity_nodes: dict[str, object] = {}

    @contextlib.contextmanager
    def bulk_writes(self):
        self.events.append("ENTER")
        try:
            yield
        finally:
            self.events.append("EXIT")

    @contextlib.contextmanager
    def write_pipeline(self):
        # #1912: the per-record write loop runs inside this (nested in
        # bulk_writes). A passthrough here keeps the transaction-scope
        # assertion valid; the real store opens conn.pipeline().
        self.events.append("PIPE-ENTER")
        try:
            yield
        finally:
            self.events.append("PIPE-EXIT")

    # --- reads ---
    def lookup_entity_ids(self, ids):
        return {i: self._preexisting[i] for i in ids if i in self._preexisting}

    def get_identity(self, eid):
        return self._identity_nodes.get(eid)

    def get_identities(self, ids):
        # Batched get_identity (#1912 pre-flight). Mirror get_identity: only
        # nodes written this run are known to the fake.
        return {i: self._identity_nodes[i] for i in ids if i in self._identity_nodes}

    def has_run_event(self, *a):
        return False

    # --- per-record writes ---
    def upsert_identity(self, node):
        self.events.append("upsert_identity")
        self._identity_nodes[node.entity_id] = node

    def upsert_record(self, rec):
        self.events.append("upsert_record")

    def emit_event(self, ev, *, return_id=True):
        self.events.append("emit_event")

    def add_edge(self, edge, *, return_id=True):
        self.events.append("add_edge")

    # --- bulk writes ---
    def bulk_upsert_identities(self, df):
        self.events.append("bulk_upsert_identities")

    def bulk_upsert_records(self, df):
        self.events.append("bulk_upsert_records")

    def bulk_add_edges(self, df):
        self.events.append("bulk_add_edges")

    def bulk_emit_events(self, df):
        self.events.append("bulk_emit_events")

    def close(self):
        pass


def _assert_writes_transaction_scoped(events: list[str]) -> None:
    assert events.count("ENTER") == 1, f"expected 1 bulk_writes scope, got {events}"
    lo, hi = events.index("ENTER"), events.index("EXIT")
    writes = _PER_RECORD_WRITES | _BULK_WRITES
    for i, ev in enumerate(events):
        if ev in writes:
            assert lo < i < hi, f"write {ev!r} at {i} outside bulk_writes {events}"


def _singleton_df(n: int) -> pl.DataFrame:
    return pl.DataFrame({
        "__row_id__": list(range(n)),
        "__source__": ["crm"] * n,
        "raw_id": [f"r{i}" for i in range(n)],
        "name": [f"person {i}" for i in range(n)],
    })


def _singleton_clusters(n: int) -> dict[int, dict]:
    return {
        i: {"members": [i], "size": 1, "pair_scores": {},
            "confidence": 1.0, "bottleneck_pair": None, "oversized": False}
        for i in range(n)
    }


def test_bulk_path_writes_inside_one_transaction():
    # Brand-new singletons -> bulk fast-path. The 4 COPY calls must be inside
    # the single bulk_writes() scope.
    store = _TxnRecordingStore()
    resolve_clusters(
        clusters=_singleton_clusters(5), df=_singleton_df(5), scored_pairs=[],
        store=store, run_name="run1", dataset="crm", source_pk_col="raw_id",
    )
    _assert_writes_transaction_scoped(store.events)
    assert any(e in _BULK_WRITES for e in store.events)


def test_bulk_writes_dispatch_postgres_uses_conn_transaction():
    # The store's bulk_writes() must open a real conn.transaction() on postgres
    # and be a no-op elsewhere. Build stores via __new__ to skip DB connect.
    from goldenmatch.identity.store import IdentityStore

    class _FakeTxn:
        def __init__(self, log): self.log = log
        def __enter__(self): self.log.append("txn-enter")
        def __exit__(self, *a): self.log.append("txn-exit")

    class _FakeConn:
        def __init__(self): self.log = []
        def transaction(self): return _FakeTxn(self.log)

    pg = IdentityStore.__new__(IdentityStore)
    pg._backend = "postgres"
    pg._conn = _FakeConn()
    with pg.bulk_writes():
        pg._conn.log.append("body")
    assert pg._conn.log == ["txn-enter", "body", "txn-exit"]


def test_bulk_writes_dispatch_sqlite_opens_a_transaction(monkeypatch):
    # #2105: sqlite used to be a deliberate no-op here ("already local + WAL"),
    # which left every statement to autocommit and pay its own WAL sync. It now
    # opens an explicit transaction, with a kill-switch back to the old path.
    from goldenmatch.identity.store import IdentityStore

    class _FakeConn:
        def __init__(self):
            self.log = []
            self.in_transaction = False
        def execute(self, sql, params=None):
            self.log.append(sql)
            if sql == "BEGIN":
                self.in_transaction = True
            elif sql in ("COMMIT", "ROLLBACK"):
                self.in_transaction = False

    monkeypatch.delenv("GOLDENMATCH_IDENTITY_SQLITE_BATCH", raising=False)
    lite = IdentityStore.__new__(IdentityStore)
    lite._backend = "sqlite"
    lite._conn = _FakeConn()
    with lite.bulk_writes():
        lite._conn.log.append("body")
    assert lite._conn.log == ["BEGIN", "body", "COMMIT"]

    # Kill-switch: back to per-statement autocommit, conn untouched.
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_SQLITE_BATCH", "0")
    lite2 = IdentityStore.__new__(IdentityStore)
    lite2._backend = "sqlite"
    lite2._conn = _FakeConn()
    with lite2.bulk_writes():
        lite2._conn.log.append("body")
    assert lite2._conn.log == ["body"]


def test_write_pipeline_dispatch_postgres_uses_conn_pipeline(monkeypatch):
    # #1912: write_pipeline() must open a real conn.pipeline() on postgres so the
    # per-record absorb/merge writes stream without a round-trip each; no-op on
    # sqlite and when the kill-switch is set.
    from goldenmatch.identity.store import IdentityStore

    class _FakePipe:
        def __init__(self, log): self.log = log
        def __enter__(self): self.log.append("pipe-enter")
        def __exit__(self, *a): self.log.append("pipe-exit")

    class _FakeConn:
        def __init__(self): self.log = []
        def pipeline(self): return _FakePipe(self.log)

    monkeypatch.delenv("GOLDENMATCH_IDENTITY_WRITE_PIPELINE", raising=False)
    pg = IdentityStore.__new__(IdentityStore)
    pg._backend = "postgres"
    pg._conn = _FakeConn()
    with pg.write_pipeline():
        pg._conn.log.append("body")
    assert pg._conn.log == ["pipe-enter", "body", "pipe-exit"]

    # Kill-switch: pipeline mode disabled -> plain passthrough, conn untouched.
    monkeypatch.setenv("GOLDENMATCH_IDENTITY_WRITE_PIPELINE", "0")
    pg2 = IdentityStore.__new__(IdentityStore)
    pg2._backend = "postgres"
    pg2._conn = _FakeConn()
    with pg2.write_pipeline():
        pg2._conn.log.append("body")
    assert pg2._conn.log == ["body"]

    # sqlite: no pipeline object touched, still yields.
    monkeypatch.delenv("GOLDENMATCH_IDENTITY_WRITE_PIPELINE", raising=False)
    lite = IdentityStore.__new__(IdentityStore)
    lite._backend = "sqlite"
    ran = []
    with lite.write_pipeline():
        ran.append("body")
    assert ran == ["body"]


def test_per_record_absorb_writes_inside_one_transaction():
    # Every record already maps to an existing entity -> the per-record ABSORB
    # path (not the bulk fast-path). This is the #1886 re-resolve shape: without
    # the fix each of these writes autocommits on its own round-trip.
    n = 5
    preexisting = {f"crm:r{i}": f"ent-{i}" for i in range(n)}
    store = _TxnRecordingStore(preexisting=preexisting)
    resolve_clusters(
        clusters=_singleton_clusters(n), df=_singleton_df(n), scored_pairs=[],
        store=store, run_name="run2", dataset="crm", source_pk_col="raw_id",
    )
    # The per-record path was exercised...
    assert any(e in _PER_RECORD_WRITES for e in store.events), store.events
    # ...and all of it inside the single transaction.
    _assert_writes_transaction_scoped(store.events)
    # #1912: the per-record writes must also fall inside the write_pipeline()
    # scope (nested in bulk_writes) so remote Postgres batches them instead of
    # round-tripping each statement.
    assert "PIPE-ENTER" in store.events and "PIPE-EXIT" in store.events
    lo, hi = store.events.index("PIPE-ENTER"), store.events.index("PIPE-EXIT")
    for i, ev in enumerate(store.events):
        if ev in _PER_RECORD_WRITES:
            assert lo < i < hi, f"per-record write {ev!r} outside pipeline {store.events}"
