"""IdentityStore -- SQLite/Postgres persistence for the Identity Graph.

Mirrors the ``MemoryStore`` pattern in ``goldenmatch/core/memory/store.py``:
SQLite default, Postgres optional, lazy import. WAL mode + busy timeout for
multi-process safety. Schema versioned via ``PRAGMA user_version``.
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import time
import uuid
from collections.abc import Iterable, Iterator
from datetime import datetime
from typing import Any

from goldenmatch.identity.model import (
    AuditSeal,
    EvidenceEdge,
    IdentityAlias,
    IdentityEvent,
    IdentityNode,
    IdentityStatus,
    SourceRecord,
    canon_record_pair,
)

log = logging.getLogger("goldenmatch.identity")


def _write_pipeline_enabled() -> bool:
    """psycopg pipeline mode for the per-record identity write path
    (the absorb / merge branches of ``resolve_clusters``). Default ON for the
    postgres backend. Against a REMOTE Postgres (e.g. Cloud SQL) the per-record
    path issues one statement per ``upsert_identity`` / ``emit_event`` /
    ``upsert_record`` / ``add_edge``; #1894's single-transaction wrap removed the
    per-commit fsync but not the per-statement NETWORK ROUND-TRIP, which is what
    dominates at few-ms RTT (#1912 -- a ~20k-record re-resolve stayed >11 min).
    Pipeline mode lets the client stream many statements before waiting for
    results, collapsing ~N round-trips into a handful of syncs while preserving
    the exact statements + rich event payloads (unlike a COPY rewrite, which
    would drop the event payload and edge ``negative_evidence``). Kill-switch
    ``GOLDENMATCH_IDENTITY_WRITE_PIPELINE=0`` restores per-statement writes."""
    return os.environ.get(
        "GOLDENMATCH_IDENTITY_WRITE_PIPELINE", "1"
    ).strip() != "0"


SCHEMA_VERSION = 5

_SCHEMA = """
CREATE TABLE IF NOT EXISTS identity_nodes (
    entity_id      TEXT PRIMARY KEY,
    status         TEXT NOT NULL DEFAULT 'active',
    merged_into    TEXT,
    golden_record  TEXT,
    confidence     REAL,
    dataset        TEXT,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_identity_nodes_dataset ON identity_nodes(dataset);
CREATE INDEX IF NOT EXISTS idx_identity_nodes_status  ON identity_nodes(status);

CREATE TABLE IF NOT EXISTS source_records (
    record_id      TEXT PRIMARY KEY,
    source         TEXT NOT NULL,
    source_pk      TEXT NOT NULL,
    record_hash    TEXT NOT NULL,
    entity_id      TEXT,
    payload        TEXT,
    dataset        TEXT,
    first_seen_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (entity_id) REFERENCES identity_nodes(entity_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_source_records_entity ON source_records(entity_id);
CREATE INDEX IF NOT EXISTS idx_source_records_source ON source_records(source);
CREATE INDEX IF NOT EXISTS idx_source_records_hash   ON source_records(record_hash);

CREATE TABLE IF NOT EXISTS evidence_edges (
    edge_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id            TEXT NOT NULL,
    record_a_id          TEXT NOT NULL,
    record_b_id          TEXT NOT NULL,
    kind                 TEXT NOT NULL DEFAULT 'same_as',
    score                REAL,
    matchkey_name        TEXT,
    field_scores         TEXT,
    negative_evidence    TEXT,
    controller_snapshot  TEXT,
    run_name             TEXT,
    dataset              TEXT,
    actor                TEXT,
    trust                REAL,
    recorded_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    -- v2 schema: ``kind`` is part of the unique key so a single run can record
    -- both a ``same_as`` edge and a ``conflicts_with`` edge for the same
    -- record pair (e.g. weak bottleneck on an otherwise-linked cluster).
    UNIQUE(entity_id, record_a_id, record_b_id, kind, run_name)
);
CREATE INDEX IF NOT EXISTS idx_edges_entity ON evidence_edges(entity_id);
CREATE INDEX IF NOT EXISTS idx_edges_pair   ON evidence_edges(record_a_id, record_b_id);
CREATE INDEX IF NOT EXISTS idx_edges_run    ON evidence_edges(run_name);

CREATE TABLE IF NOT EXISTS identity_events (
    event_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id         TEXT NOT NULL,
    kind              TEXT NOT NULL,
    payload           TEXT,
    run_name          TEXT,
    dataset           TEXT,
    actor             TEXT,
    trust             REAL,
    claim_type        TEXT,
    evidence_ref      TEXT,
    previous_claim_id INTEGER,
    entry_hash        TEXT,
    recorded_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_events_entity ON identity_events(entity_id);
CREATE INDEX IF NOT EXISTS idx_events_kind   ON identity_events(kind);
CREATE INDEX IF NOT EXISTS idx_events_run    ON identity_events(run_name);

-- Tamper-evidence seal chain (#1078): periodic anchors over identity_events.
-- One row per ``seal_audit_log`` call; chained via prev_seal_id/prev_root.
CREATE TABLE IF NOT EXISTS audit_seals (
    seal_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    dataset       TEXT,
    root_hash     TEXT NOT NULL,
    event_count   INTEGER NOT NULL,
    last_event_id INTEGER,
    prev_seal_id  INTEGER,
    prev_root     TEXT,
    actor         TEXT,
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_audit_seals_dataset ON audit_seals(dataset);

CREATE TABLE IF NOT EXISTS identity_aliases (
    alias        TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'external_id',
    dataset      TEXT,
    recorded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (alias, kind, dataset)
);
CREATE INDEX IF NOT EXISTS idx_aliases_entity ON identity_aliases(entity_id);
"""


def _sqlite_batch_writes_enabled() -> bool:
    """Batch the SQLite resolve write path into explicit transactions.

    The SQLite connection is opened ``isolation_level=None`` (autocommit), so
    before #2105 every INSERT the resolver issued committed on its own -- a WAL
    sync per statement, and resolve issues ~6 statements per cluster. Measured
    on a 200k-row store: ~750 us/statement autocommit vs ~30-90 us inside a
    transaction (8-25x). ``GOLDENMATCH_IDENTITY_SQLITE_BATCH=0`` restores the
    per-statement autocommit behaviour."""
    return os.environ.get(
        "GOLDENMATCH_IDENTITY_SQLITE_BATCH", "1"
    ).strip() != "0"


def _sqlite_batch_size() -> int:
    """Statements per SQLite transaction inside ``bulk_writes``.

    A single transaction spanning a 14M-row resolve would grow the WAL without
    bound before it could checkpoint, so the batch commits and re-opens every N
    writes -- that is the "memory-bounded" half of the fix. Durability is
    unchanged-or-better versus the pre-#2105 autocommit path, which offered no
    per-run atomicity either."""
    raw = os.environ.get("GOLDENMATCH_IDENTITY_SQLITE_BATCH_SIZE", "10000")
    try:
        n = int(raw.strip())
    except ValueError:
        return 10_000
    return n if n > 0 else 10_000


def new_entity_id() -> str:
    """Generate a stable entity id (UUIDv7-shaped, time-ordered)."""
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = uuid.uuid4().int & ((1 << 12) - 1)
    rand_b = uuid.uuid4().int & ((1 << 62) - 1)
    val = (
        (ts_ms << 80)
        | (0x7 << 76)
        | (rand_a << 64)
        | (0b10 << 62)
        | rand_b
    )
    return str(uuid.UUID(int=val))


class IdentityStore:
    """Persistence for the Identity Graph (nodes, records, edges, events, aliases)."""

    _conn: Any
    # Class-level defaults so a store built via ``__new__`` (tests/dispatch
    # probes that skip ``__init__``) still has a sane batching state.
    _sqlite_batch: int = 0
    _sqlite_pending: int = 0

    def __init__(
        self,
        backend: str = "sqlite",
        path: str = ".goldenmatch/identity.db",
        connection: str | None = None,
        pool: Any = None,
        database: str = "goldenmatch",
        client: Any = None,
    ) -> None:
        self._backend = backend
        # SQLite write batching (#2105). ``_sqlite_batch`` is 0 outside a
        # ``bulk_writes`` block (statements autocommit as before) and the batch
        # size inside one; ``_sqlite_pending`` counts statements since the last
        # commit. Set for every backend so ``_exec`` needs no hasattr guard.
        self._sqlite_batch = 0
        self._sqlite_pending = 0
        # Optional psycopg_pool.ConnectionPool for postgres. When set, methods
        # check out a pooled conn for each call. Default None preserves the
        # legacy per-store single-conn behavior the existing tests rely on.
        self._pool = pool
        # MongoIdentityStore wraps a pymongo client. For backend="mongo",
        # delegated by the per-method `if self._backend == "mongo"` early
        # returns below. The SQL paths see ``self._mongo is None`` and skip
        # the dispatch.
        self._mongo: Any = None
        if backend == "mongo":
            # Defer the import so the SQL backends don't pay for pymongo.
            from goldenmatch.identity.mongo_backend import (
                MongoIdentityStore,
            )
            self._mongo = MongoIdentityStore(
                connection=connection, database=database, client=client,
            )
            # No SQL connection for mongo -- _conn stays unset and any SQL
            # method that gets called without a dispatch branch hits the
            # AttributeError fast, signaling a missing branch.
            return
        if backend == "sqlite":
            import sqlite3  # noqa: PLC0415 -- lazy, see #364
            # Canonicalize path early so logs / errors see the resolved form
            # and the parent-dir create cannot escape via "..". Path is a
            # trusted-config value supplied by the embedding application,
            # but normpath defends against accidental traversal.
            safe_path = os.path.normpath(path)
            parent = os.path.dirname(safe_path) or "."
            os.makedirs(parent, exist_ok=True)
            self._conn = sqlite3.connect(safe_path, timeout=30, isolation_level=None)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA busy_timeout=5000")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(_SCHEMA)
            self._migrate()
            # Log the basename only — keeps user-controlled directory names
            # out of structured logs while still being useful for debugging.
            log.debug("IdentityStore opened: %s", os.path.basename(safe_path))
        elif backend == "postgres":
            if not connection:
                raise ValueError("postgres backend requires connection= DSN")
            try:
                import psycopg  # noqa: F401
            except ImportError as e:
                raise ImportError(
                    "postgres backend requires psycopg3: "
                    "pip install 'psycopg[binary]'",
                ) from e
            import psycopg
            self._conn = psycopg.connect(connection, autocommit=True)
            self._pg_init_schema()
        else:
            raise NotImplementedError(f"Backend '{backend}' not supported")

    def close(self) -> None:
        if self._backend == "mongo":
            self._mongo.close()
            return
        self._conn.close()

    def __enter__(self) -> IdentityStore:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _migrate(self) -> None:
        cur = self._conn.execute("PRAGMA user_version")
        version = cur.fetchone()[0]
        if version < 2:
            # v1 -> v2: widen the evidence_edges UNIQUE constraint to include
            # ``kind`` so a single run can record both same_as and
            # conflicts_with edges on the same record pair. SQLite has no
            # ALTER CONSTRAINT, so we rebuild the table.
            self._conn.executescript(
                """
                BEGIN;
                CREATE TABLE evidence_edges_v2 (
                    edge_id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id            TEXT NOT NULL,
                    record_a_id          TEXT NOT NULL,
                    record_b_id          TEXT NOT NULL,
                    kind                 TEXT NOT NULL DEFAULT 'same_as',
                    score                REAL,
                    matchkey_name        TEXT,
                    field_scores         TEXT,
                    negative_evidence    TEXT,
                    controller_snapshot  TEXT,
                    run_name             TEXT,
                    dataset              TEXT,
                    recorded_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(entity_id, record_a_id, record_b_id, kind, run_name)
                );
                INSERT INTO evidence_edges_v2
                    (edge_id, entity_id, record_a_id, record_b_id, kind, score,
                     matchkey_name, field_scores, negative_evidence,
                     controller_snapshot, run_name, dataset, recorded_at)
                SELECT edge_id, entity_id, record_a_id, record_b_id, kind, score,
                       matchkey_name, field_scores, negative_evidence,
                       controller_snapshot, run_name, dataset, recorded_at
                FROM evidence_edges;
                DROP TABLE evidence_edges;
                ALTER TABLE evidence_edges_v2 RENAME TO evidence_edges;
                CREATE INDEX IF NOT EXISTS idx_edges_entity ON evidence_edges(entity_id);
                CREATE INDEX IF NOT EXISTS idx_edges_pair   ON evidence_edges(record_a_id, record_b_id);
                CREATE INDEX IF NOT EXISTS idx_edges_run    ON evidence_edges(run_name);
                COMMIT;
                """
            )
        if version < 3:
            # v2 -> v3: provenance spine (#1075/#1078). Add actor/trust to the
            # event + edge logs. Idempotent (PRAGMA-guarded) so it's safe on a
            # fresh DB whose tables already carry the columns from ``_SCHEMA`` and
            # on the rebuilt-evidence_edges path above (which drops them).
            self._ensure_provenance_columns()
        if version < 4:
            # v3 -> v4: tamper-evidence (#1078). Add the per-event ``entry_hash``
            # column and the ``audit_seals`` chain table. PRAGMA-guarded ADD
            # COLUMN + CREATE TABLE IF NOT EXISTS, so it's idempotent on a fresh
            # DB (already carries them from ``_SCHEMA``) and on a migrated v2/v3
            # DB. Old rows keep entry_hash=NULL and are hashed on the fly by the
            # seal/verify path.
            self._ensure_audit_columns()
        if version < 5:
            # v4 -> v5: claim-authority tier (#1256). Add the nullable
            # claim_type / evidence_ref / previous_claim_id columns to the event
            # log. PRAGMA-guarded ADD COLUMN, idempotent on fresh (already carry
            # them from ``_SCHEMA``) and migrated DBs. Old rows read back None.
            self._ensure_claim_columns()
        if version < SCHEMA_VERSION:
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    def _ensure_provenance_columns(self) -> None:
        """Add the nullable ``actor``/``trust`` columns to the event + edge tables
        if absent. SQLite has no ``ADD COLUMN IF NOT EXISTS``, so we probe
        ``PRAGMA table_info`` first -- making the op idempotent across fresh,
        v1-rebuilt, and v2 databases."""
        for table in ("identity_events", "evidence_edges"):
            cols = {
                r[1] for r in self._conn.execute(f"PRAGMA table_info({table})")
            }
            if "actor" not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN actor TEXT")
            if "trust" not in cols:
                self._conn.execute(f"ALTER TABLE {table} ADD COLUMN trust REAL")

    def _ensure_audit_columns(self) -> None:
        """Add the ``entry_hash`` column to identity_events and create the
        ``audit_seals`` table if absent (#1078). PRAGMA-guarded ADD COLUMN +
        CREATE TABLE IF NOT EXISTS make this idempotent across fresh and
        migrated databases."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(identity_events)")}
        if "entry_hash" not in cols:
            self._conn.execute(
                "ALTER TABLE identity_events ADD COLUMN entry_hash TEXT"
            )
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS audit_seals (
                seal_id       INTEGER PRIMARY KEY AUTOINCREMENT,
                dataset       TEXT,
                root_hash     TEXT NOT NULL,
                event_count   INTEGER NOT NULL,
                last_event_id INTEGER,
                prev_seal_id  INTEGER,
                prev_root     TEXT,
                actor         TEXT,
                created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_audit_seals_dataset ON audit_seals(dataset);
            """
        )

    def _ensure_claim_columns(self) -> None:
        """Add the nullable claim-authority columns to identity_events if absent
        (#1256): ``claim_type`` + ``evidence_ref`` (categorical authority tier +
        typed evidence, orthogonal to ``trust``) and ``previous_claim_id`` (the
        event this claim supersedes). PRAGMA-guarded ADD COLUMN, idempotent on
        fresh and migrated databases; old rows read back None."""
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(identity_events)")}
        if "claim_type" not in cols:
            self._conn.execute(
                "ALTER TABLE identity_events ADD COLUMN claim_type TEXT"
            )
        if "evidence_ref" not in cols:
            self._conn.execute(
                "ALTER TABLE identity_events ADD COLUMN evidence_ref TEXT"
            )
        if "previous_claim_id" not in cols:
            self._conn.execute(
                "ALTER TABLE identity_events ADD COLUMN previous_claim_id INTEGER"
            )

    def _pg_init_schema(self) -> None:
        ddl = """
        CREATE TABLE IF NOT EXISTS identity_nodes (
            entity_id TEXT PRIMARY KEY,
            status TEXT NOT NULL DEFAULT 'active',
            merged_into TEXT,
            golden_record JSONB,
            confidence DOUBLE PRECISION,
            dataset TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_identity_nodes_dataset ON identity_nodes(dataset);
        CREATE INDEX IF NOT EXISTS idx_identity_nodes_status  ON identity_nodes(status);
        CREATE TABLE IF NOT EXISTS source_records (
            record_id TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            source_pk TEXT NOT NULL,
            record_hash TEXT NOT NULL,
            entity_id TEXT REFERENCES identity_nodes(entity_id) ON DELETE SET NULL,
            payload JSONB,
            dataset TEXT,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_source_records_entity ON source_records(entity_id);
        CREATE INDEX IF NOT EXISTS idx_source_records_source ON source_records(source);
        CREATE INDEX IF NOT EXISTS idx_source_records_hash   ON source_records(record_hash);
        CREATE TABLE IF NOT EXISTS evidence_edges (
            edge_id BIGSERIAL PRIMARY KEY,
            entity_id TEXT NOT NULL,
            record_a_id TEXT NOT NULL,
            record_b_id TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'same_as',
            score DOUBLE PRECISION,
            matchkey_name TEXT,
            field_scores JSONB,
            negative_evidence JSONB,
            controller_snapshot JSONB,
            run_name TEXT,
            dataset TEXT,
            actor TEXT,
            trust DOUBLE PRECISION,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(entity_id, record_a_id, record_b_id, kind, run_name)
        );
        -- Provenance spine (#1075/#1078): add to pre-existing tables too (the
        -- CREATE above only covers fresh DBs). ADD COLUMN IF NOT EXISTS is
        -- idempotent on Postgres, so this runs safely on every store open.
        ALTER TABLE evidence_edges ADD COLUMN IF NOT EXISTS actor TEXT;
        ALTER TABLE evidence_edges ADD COLUMN IF NOT EXISTS trust DOUBLE PRECISION;
        CREATE INDEX IF NOT EXISTS idx_edges_entity ON evidence_edges(entity_id);
        CREATE INDEX IF NOT EXISTS idx_edges_pair   ON evidence_edges(record_a_id, record_b_id);
        CREATE INDEX IF NOT EXISTS idx_edges_run    ON evidence_edges(run_name);
        CREATE TABLE IF NOT EXISTS identity_events (
            event_id BIGSERIAL PRIMARY KEY,
            entity_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            payload JSONB,
            run_name TEXT,
            dataset TEXT,
            actor TEXT,
            trust DOUBLE PRECISION,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        ALTER TABLE identity_events ADD COLUMN IF NOT EXISTS actor TEXT;
        ALTER TABLE identity_events ADD COLUMN IF NOT EXISTS trust DOUBLE PRECISION;
        -- Claim-authority tier (#1256): categorical authority + typed evidence +
        -- lifecycle chain, orthogonal to numeric ``trust``. Nullable/additive.
        ALTER TABLE identity_events ADD COLUMN IF NOT EXISTS claim_type TEXT;
        ALTER TABLE identity_events ADD COLUMN IF NOT EXISTS evidence_ref TEXT;
        ALTER TABLE identity_events ADD COLUMN IF NOT EXISTS previous_claim_id BIGINT;
        -- Tamper-evidence (#1078): per-event content hash + seal chain table.
        ALTER TABLE identity_events ADD COLUMN IF NOT EXISTS entry_hash TEXT;
        CREATE INDEX IF NOT EXISTS idx_events_entity ON identity_events(entity_id);
        CREATE INDEX IF NOT EXISTS idx_events_kind   ON identity_events(kind);
        CREATE INDEX IF NOT EXISTS idx_events_run    ON identity_events(run_name);
        CREATE TABLE IF NOT EXISTS audit_seals (
            seal_id BIGSERIAL PRIMARY KEY,
            dataset TEXT,
            root_hash TEXT NOT NULL,
            event_count BIGINT NOT NULL,
            last_event_id BIGINT,
            prev_seal_id BIGINT,
            prev_root TEXT,
            actor TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_audit_seals_dataset ON audit_seals(dataset);
        CREATE TABLE IF NOT EXISTS identity_aliases (
            alias TEXT NOT NULL,
            entity_id TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'external_id',
            dataset TEXT,
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (alias, kind, dataset)
        );
        CREATE INDEX IF NOT EXISTS idx_aliases_entity ON identity_aliases(entity_id);
        """
        with self._conn.cursor() as cur:
            cur.execute(ddl)

    # ----- Bulk write methods (Postgres only) -----
    #
    # Each takes a Polars frame and pushes it through Postgres COPY into a
    # temp staging table, then INSERT ... ON CONFLICT into the real table.
    # Single transaction per call. SQLite raises NotImplementedError -- the
    # SQLite path is single-process and the row-by-row upsert_* methods are
    # plenty fast for that scale.

    @contextlib.contextmanager
    def bulk_writes(self) -> Iterator[None]:
        """Run a batch of writes inside ONE transaction (Postgres).

        The Postgres connection is opened ``autocommit=True``, so on the
        per-record resolve path every ``upsert_identity`` / ``emit_event`` /
        ``upsert_record`` / ``add_edge`` commits on its own -- one COMMIT + a
        network round-trip PER write. Against a remote DB (e.g. Cloud SQL) that
        turns a ~20k-record resolve into minutes of latency even though the
        compute is milliseconds (#1886). Wrapping the whole write body in a
        single ``conn.transaction()`` collapses those N commits into one and lets
        psycopg pipeline the statements.

        SQLite gets the same treatment for the same reason (#2105). Its
        connection is opened ``isolation_level=None``, so each statement was its
        own transaction and paid a WAL sync -- local, but still ~750 us a piece
        against ~30-90 us batched. Statements commit in
        ``_sqlite_batch_size()``-sized chunks rather than one run-long
        transaction so the WAL cannot grow without bound on a multi-million-row
        resolve. Reads issued inside the batch see the pending writes (same
        connection), so the absorb / merge branches that read back rows written
        earlier in the run are unaffected.

        No-op for Mongo, and for SQLite when already inside a transaction
        (nesting) or when ``GOLDENMATCH_IDENTITY_SQLITE_BATCH=0``, so callers
        can wrap unconditionally. Nesting is safe on Postgres too: the bulk COPY
        helpers open their own ``conn.transaction()`` which becomes a savepoint
        under this outer one. Errors roll the batch back instead of leaving a
        partially-committed graph -- an improvement over the autocommit path,
        which the caller already treats as all-or-nothing (it has no per-cluster
        recovery).
        """
        if self._backend == "postgres":
            with self._conn.transaction():
                yield
            return
        if (
            self._backend != "sqlite"
            or not _sqlite_batch_writes_enabled()
            or self._conn.in_transaction
        ):
            yield
            return
        self._sqlite_batch = _sqlite_batch_size()
        self._sqlite_pending = 0
        self._conn.execute("BEGIN")
        try:
            yield
        except BaseException:
            self._sqlite_batch = 0
            if self._conn.in_transaction:
                self._conn.execute("ROLLBACK")
            raise
        self._sqlite_batch = 0
        if self._conn.in_transaction:
            self._conn.execute("COMMIT")

    @contextlib.contextmanager
    def write_pipeline(self) -> Iterator[None]:
        """Batch the per-record write path into psycopg pipeline mode (Postgres).

        Wraps the ``resolve_clusters`` absorb / merge loop so its
        ``upsert_identity`` / ``upsert_record`` / ``add_edge(return_id=False)`` /
        ``emit_event(return_id=False)`` statements stream to the server without a
        per-statement round-trip (see ``_write_pipeline_enabled``). No-op for
        SQLite / Mongo and when the kill-switch is set.

        COPY is not permitted in pipeline mode, so the caller must flush any bulk
        COPY accumulators OUTSIDE this block (still inside ``bulk_writes``). Reads
        issued inside a pipeline still work -- psycopg auto-syncs to fetch a
        result -- but each such sync forfeits batching, so callers should
        pre-fetch reads (e.g. ``get_identities``) before the write loop and pass
        ``return_id=False`` to the write helpers that would otherwise read back a
        generated id.
        """
        if self._backend == "postgres" and _write_pipeline_enabled():
            with self._conn.pipeline():
                yield
        else:
            yield

    def bulk_upsert_identities(self, df: Any) -> None:
        if self._backend != "postgres":
            raise NotImplementedError(
                "bulk_upsert_identities requires Postgres backend; "
                "use upsert_identity in a loop for SQLite",
            )
        if df.height == 0:
            return
        # All eight identity_nodes columns. ``golden_record`` and
        # ``confidence`` are required for the bench fast-path -- without
        # them, brand-new identities created via resolve_clusters lose
        # their rolled-up record + confidence score on upsert (#368
        # follow-up). Callers that don't have one of these can pass
        # ``None``; we'll fill missing cols with ``None`` to be ergonomic.
        cols = [
            "entity_id", "status", "merged_into", "golden_record",
            "confidence", "dataset", "created_at", "updated_at",
        ]
        import polars as pl  # noqa: PLC0415
        missing = [c for c in cols if c not in df.columns]
        if missing:
            df = df.with_columns([pl.lit(None).alias(c) for c in missing])
        conn: Any = self._conn
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE _stage_identity_nodes "
                "(LIKE identity_nodes INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            with cur.copy(
                "COPY _stage_identity_nodes "
                "(entity_id, status, merged_into, golden_record, "
                "confidence, dataset, created_at, updated_at) FROM STDIN"
            ) as copy:
                for row in df.select(cols).iter_rows():
                    copy.write_row(row)
            cur.execute(
                """
                INSERT INTO identity_nodes
                    (entity_id, status, merged_into, golden_record,
                     confidence, dataset, created_at, updated_at)
                SELECT entity_id, status, merged_into,
                       golden_record::jsonb, confidence, dataset,
                       created_at, updated_at
                FROM _stage_identity_nodes
                ON CONFLICT (entity_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    merged_into = EXCLUDED.merged_into,
                    golden_record = EXCLUDED.golden_record,
                    confidence = EXCLUDED.confidence,
                    updated_at = EXCLUDED.updated_at
                """
            )
            cur.execute("DROP TABLE IF EXISTS _stage_identity_nodes")

    def bulk_upsert_records(self, df: Any) -> None:
        if self._backend != "postgres":
            raise NotImplementedError(
                "bulk_upsert_records requires Postgres backend; "
                "use upsert_record in a loop for SQLite",
            )
        if df.height == 0:
            return
        cols = [
            "record_id", "source", "source_pk", "record_hash",
            "entity_id", "dataset", "first_seen_at", "last_seen_at",
        ]
        conn: Any = self._conn
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE _stage_source_records "
                "(LIKE source_records INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            with cur.copy(
                "COPY _stage_source_records "
                "(record_id, source, source_pk, record_hash, entity_id, "
                "dataset, first_seen_at, last_seen_at) FROM STDIN"
            ) as copy:
                for row in df.select(cols).iter_rows():
                    copy.write_row(row)
            cur.execute(
                """
                INSERT INTO source_records
                    (record_id, source, source_pk, record_hash, entity_id,
                     dataset, first_seen_at, last_seen_at)
                SELECT record_id, source, source_pk, record_hash, entity_id,
                       dataset, first_seen_at, last_seen_at
                FROM _stage_source_records
                ON CONFLICT (record_id) DO UPDATE SET
                    record_hash = EXCLUDED.record_hash,
                    entity_id = EXCLUDED.entity_id,
                    last_seen_at = EXCLUDED.last_seen_at
                """
            )
            cur.execute("DROP TABLE IF EXISTS _stage_source_records")

    def bulk_add_edges(self, df: Any) -> None:
        if self._backend != "postgres":
            raise NotImplementedError(
                "bulk_add_edges requires Postgres backend; "
                "use add_edge in a loop for SQLite",
            )
        if df.height == 0:
            return
        cols = [
            "entity_id", "record_a_id", "record_b_id", "kind", "score",
            "matchkey_name", "run_name", "dataset", "recorded_at",
        ]
        conn: Any = self._conn
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """
                CREATE TEMP TABLE _stage_evidence_edges (
                    entity_id TEXT,
                    record_a_id TEXT,
                    record_b_id TEXT,
                    kind TEXT,
                    score DOUBLE PRECISION,
                    matchkey_name TEXT,
                    run_name TEXT,
                    dataset TEXT,
                    recorded_at TIMESTAMPTZ
                ) ON COMMIT DROP
                """
            )
            with cur.copy(
                "COPY _stage_evidence_edges "
                "(entity_id, record_a_id, record_b_id, kind, score, "
                "matchkey_name, run_name, dataset, recorded_at) FROM STDIN"
            ) as copy:
                for row in df.select(cols).iter_rows():
                    copy.write_row(row)
            cur.execute(
                """
                INSERT INTO evidence_edges
                    (entity_id, record_a_id, record_b_id, kind, score,
                     matchkey_name, run_name, dataset, recorded_at)
                SELECT entity_id, record_a_id, record_b_id, kind, score,
                       matchkey_name, run_name, dataset, recorded_at
                FROM _stage_evidence_edges
                ON CONFLICT (entity_id, record_a_id, record_b_id, kind,
                             run_name) DO NOTHING
                """
            )
            cur.execute("DROP TABLE IF EXISTS _stage_evidence_edges")

    def bulk_emit_events(self, df: Any) -> None:
        if self._backend != "postgres":
            raise NotImplementedError(
                "bulk_emit_events requires Postgres backend; "
                "use emit_event in a loop for SQLite",
            )
        if df.height == 0:
            return
        cols = [
            "entity_id", "kind", "run_name", "dataset", "recorded_at",
        ]
        conn: Any = self._conn
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """
                CREATE TEMP TABLE _stage_identity_events (
                    entity_id TEXT,
                    kind TEXT,
                    run_name TEXT,
                    dataset TEXT,
                    recorded_at TIMESTAMPTZ
                ) ON COMMIT DROP
                """
            )
            with cur.copy(
                "COPY _stage_identity_events "
                "(entity_id, kind, run_name, dataset, recorded_at) FROM STDIN"
            ) as copy:
                for row in df.select(cols).iter_rows():
                    copy.write_row(row)
            cur.execute(
                """
                INSERT INTO identity_events
                    (entity_id, kind, run_name, dataset, recorded_at)
                SELECT entity_id, kind, run_name, dataset, recorded_at
                FROM _stage_identity_events
                """
            )
            cur.execute("DROP TABLE IF EXISTS _stage_identity_events")

    def count_nodes(self) -> int:
        """Alias of count_identities (plan compat)."""
        return self.count_identities()

    def get_node(self, entity_id: str):
        """Alias of get_identity (plan compat)."""
        return self.get_identity(entity_id)

    def upsert_identity(self, node: IdentityNode) -> None:
        if self._backend == "mongo":
            self._mongo.upsert_identity(node)
            return
        gr = json.dumps(node.golden_record) if node.golden_record is not None else None
        self._exec(
            """
            INSERT INTO identity_nodes
                (entity_id, status, merged_into, golden_record, confidence, dataset,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
                status=excluded.status,
                merged_into=excluded.merged_into,
                golden_record=excluded.golden_record,
                confidence=excluded.confidence,
                dataset=excluded.dataset,
                updated_at=excluded.updated_at
            """,
            (
                node.entity_id, node.status, node.merged_into, gr,
                node.confidence, node.dataset,
                node.created_at.isoformat(), node.updated_at.isoformat(),
            ),
        )

    def get_identity(self, entity_id: str) -> IdentityNode | None:
        if self._backend == "mongo":
            return self._mongo.get_identity(entity_id)
        row = self._fetchone(
            "SELECT * FROM identity_nodes WHERE entity_id = ?", (entity_id,)
        )
        return self._row_to_identity(row) if row else None

    def get_identities(
        self, entity_ids: Iterable[str]
    ) -> dict[str, IdentityNode]:
        """Batched ``get_identity`` -- resolve many entity ids in one (chunked)
        round-trip. Pre-flight helper for ``resolve_clusters`` (#1912): reading
        each cluster's existing identity from this dict instead of a per-cluster
        ``get_identity`` SELECT keeps the absorb / merge write loop read-free, so
        ``write_pipeline`` batches its writes without a per-cluster sync. Missing
        ids are simply absent from the returned dict."""
        ids = list({e for e in entity_ids if e})
        if not ids:
            return {}
        if self._backend == "mongo":
            out: dict[str, IdentityNode] = {}
            for eid in ids:
                node = self._mongo.get_identity(eid)
                if node is not None:
                    out[eid] = node
            return out
        # Chunk the IN-list (SQLite host-parameter cap; harmless on postgres).
        out = {}
        _CHUNK = 900
        for i in range(0, len(ids), _CHUNK):
            chunk = ids[i:i + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = self._fetchall(
                f"SELECT * FROM identity_nodes WHERE entity_id IN ({placeholders})",
                tuple(chunk),
            )
            for r in rows:
                out[r["entity_id"]] = self._row_to_identity(r)
        return out

    def list_identities(
        self,
        dataset: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[IdentityNode]:
        if self._backend == "mongo":
            return self._mongo.list_identities(
                dataset=dataset, status=status, limit=limit, offset=offset,
            )
        clauses: list[str] = []
        params: list[Any] = []
        if dataset is not None:
            clauses.append("dataset = ?")
            params.append(dataset)
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([limit, offset])
        rows = self._fetchall(
            f"SELECT * FROM identity_nodes{where} "
            f"ORDER BY updated_at DESC LIMIT ? OFFSET ?",
            tuple(params),
        )
        return [self._row_to_identity(r) for r in rows]

    def count_identities(self, dataset: str | None = None) -> int:
        if self._backend == "mongo":
            return self._mongo.count_identities(dataset=dataset)
        if dataset is None:
            row = self._fetchone("SELECT COUNT(*) AS n FROM identity_nodes", ())
        else:
            row = self._fetchone(
                "SELECT COUNT(*) AS n FROM identity_nodes WHERE dataset = ?",
                (dataset,),
            )
        return int(row["n"]) if row else 0

    def retire_identity(
        self,
        entity_id: str,
        merged_into: str | None = None,
        run_name: str | None = None,
    ) -> None:
        if self._backend == "mongo":
            self._mongo.retire_identity(entity_id, merged_into=merged_into)
            return
        new_status = (
            IdentityStatus.MERGED_INTO.value
            if merged_into is not None
            else IdentityStatus.RETIRED.value
        )
        self._exec(
            "UPDATE identity_nodes SET status = ?, merged_into = ?, updated_at = ? "
            "WHERE entity_id = ?",
            (new_status, merged_into, datetime.now().isoformat(), entity_id),
        )

    def upsert_record(self, rec: SourceRecord) -> None:
        if self._backend == "mongo":
            self._mongo.upsert_record(rec)
            return
        payload = json.dumps(rec.payload) if rec.payload is not None else None
        self._exec(
            """
            INSERT INTO source_records
                (record_id, source, source_pk, record_hash, entity_id, payload,
                 dataset, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(record_id) DO UPDATE SET
                record_hash=excluded.record_hash,
                entity_id=excluded.entity_id,
                payload=excluded.payload,
                last_seen_at=excluded.last_seen_at
            """,
            (
                rec.record_id, rec.source, rec.source_pk, rec.record_hash,
                rec.entity_id, payload, rec.dataset,
                rec.first_seen_at.isoformat(), rec.last_seen_at.isoformat(),
            ),
        )

    def get_record(self, record_id: str) -> SourceRecord | None:
        if self._backend == "mongo":
            return self._mongo.get_record(record_id)
        row = self._fetchone(
            "SELECT * FROM source_records WHERE record_id = ?", (record_id,)
        )
        return self._row_to_record(row) if row else None

    def get_records_for_entity(self, entity_id: str) -> list[SourceRecord]:
        if self._backend == "mongo":
            return self._mongo.get_records_for_entity(entity_id)
        rows = self._fetchall(
            "SELECT * FROM source_records WHERE entity_id = ? ORDER BY first_seen_at",
            (entity_id,),
        )
        return [self._row_to_record(r) for r in rows]

    def find_entity_by_record(self, record_id: str) -> str | None:
        if self._backend == "mongo":
            return self._mongo.find_entity_by_record(record_id)
        row = self._fetchone(
            "SELECT entity_id FROM source_records WHERE record_id = ?", (record_id,)
        )
        return row["entity_id"] if row else None

    def lookup_entity_ids(self, record_ids: Iterable[str]) -> dict[str, str]:
        if self._backend == "mongo":
            return self._mongo.lookup_entity_ids(record_ids)
        ids = list(record_ids)
        if not ids:
            return {}
        # SQLite caps host parameters per statement (SQLITE_MAX_VARIABLE_NUMBER;
        # 999 on older builds). A single IN-list over the full candidate set
        # raised "too many SQL variables" at 1M+ records (#670). Chunk the
        # IN-list and union the results -- each record_id is unique so chunks
        # never overlap; behavior is identical to the single-query form.
        out: dict[str, str] = {}
        _CHUNK = 900
        for i in range(0, len(ids), _CHUNK):
            chunk = ids[i:i + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            rows = self._fetchall(
                f"SELECT record_id, entity_id FROM source_records "
                f"WHERE record_id IN ({placeholders}) AND entity_id IS NOT NULL",
                tuple(chunk),
            )
            for r in rows:
                out[r["record_id"]] = r["entity_id"]
        return out

    def add_edge(self, edge: EvidenceEdge, *, return_id: bool = True) -> int | None:
        if self._backend == "mongo":
            return self._mongo.add_edge(edge)
        a, b = canon_record_pair(edge.record_a_id, edge.record_b_id)
        fs = json.dumps(edge.field_scores) if edge.field_scores else None
        ne = json.dumps(edge.negative_evidence) if edge.negative_evidence else None
        cs = json.dumps(edge.controller_snapshot) if edge.controller_snapshot else None
        if self._backend == "sqlite":
            self._exec(
                "INSERT OR IGNORE INTO evidence_edges "
                "(entity_id, record_a_id, record_b_id, kind, score, "
                "matchkey_name, field_scores, negative_evidence, "
                "controller_snapshot, run_name, dataset, actor, trust, recorded_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    edge.entity_id, a, b, edge.kind, edge.score,
                    edge.matchkey_name, fs, ne, cs, edge.run_name,
                    edge.dataset, edge.actor, edge.trust,
                    edge.recorded_at.isoformat(),
                ),
            )
        else:
            conn: Any = self._conn
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO evidence_edges "
                    "(entity_id, record_a_id, record_b_id, kind, score, "
                    "matchkey_name, field_scores, negative_evidence, "
                    "controller_snapshot, run_name, dataset, actor, trust, recorded_at) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
                    "ON CONFLICT (entity_id, record_a_id, record_b_id, "
                    "kind, run_name) DO NOTHING",
                    (
                        edge.entity_id, a, b, edge.kind, edge.score,
                        edge.matchkey_name, fs, ne, cs, edge.run_name,
                        edge.dataset, edge.actor, edge.trust,
                        edge.recorded_at.isoformat(),
                    ),
                )
        # Fire-and-forget: the resolve_clusters write path ignores the edge_id,
        # so skip the read-back -- under write_pipeline() this SELECT would force
        # a per-edge sync and defeat the batching (#1912).
        if not return_id:
            return None
        row = self._fetchone(
            "SELECT edge_id FROM evidence_edges WHERE entity_id=? AND record_a_id=? "
            "AND record_b_id=? AND kind=? AND COALESCE(run_name,'')=COALESCE(?,'')",
            (edge.entity_id, a, b, edge.kind, edge.run_name),
        )
        return int(row["edge_id"]) if row else None

    def edges_for_entity(self, entity_id: str) -> list[EvidenceEdge]:
        if self._backend == "mongo":
            return self._mongo.edges_for_entity(entity_id)
        rows = self._fetchall(
            "SELECT * FROM evidence_edges WHERE entity_id = ? ORDER BY recorded_at",
            (entity_id,),
        )
        return [self._row_to_edge(r) for r in rows]

    def edges_by_kind(
        self, kind: str, dataset: str | None = None
    ) -> list[EvidenceEdge]:
        """All evidence edges of a given ``kind`` (most-recent first). Generic
        counterpart to ``find_conflicts`` (which is ``edges_by_kind('conflicts_with')``)
        -- used by the mediation workflow to list steward verdict edges."""
        if self._backend == "mongo":
            return self._mongo.edges_by_kind(kind, dataset=dataset)
        if dataset is None:
            rows = self._fetchall(
                "SELECT * FROM evidence_edges WHERE kind = ? "
                "ORDER BY recorded_at DESC",
                (kind,),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM evidence_edges WHERE kind = ? AND dataset = ? "
                "ORDER BY recorded_at DESC",
                (kind, dataset),
            )
        return [self._row_to_edge(r) for r in rows]

    def find_conflicts(self, dataset: str | None = None) -> list[EvidenceEdge]:
        if self._backend == "mongo":
            return self._mongo.find_conflicts(dataset=dataset)
        if dataset is None:
            rows = self._fetchall(
                "SELECT * FROM evidence_edges WHERE kind = 'conflicts_with' "
                "ORDER BY recorded_at DESC",
                (),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM evidence_edges WHERE kind = 'conflicts_with' "
                "AND dataset = ? ORDER BY recorded_at DESC",
                (dataset,),
            )
        return [self._row_to_edge(r) for r in rows]

    def emit_event(
        self, event: IdentityEvent, *, return_id: bool = True
    ) -> int | None:
        if self._backend == "mongo":
            return self._mongo.emit_event(event)
        payload = json.dumps(event.payload) if event.payload is not None else None
        # Tamper-evidence (#1078): stamp a per-event content hash at insert. Pure
        # function of the event's own fields -- no DB read, no contention -- so it
        # imposes no serialization point on the write path. Set it on the object
        # too so an in-memory caller sees the same value the row carries.
        from goldenmatch.identity.audit import event_content_hash  # noqa: PLC0415
        if event.entry_hash is None:
            event.entry_hash = event_content_hash(event)
        self._exec(
            "INSERT INTO identity_events "
            "(entity_id, kind, payload, run_name, dataset, actor, trust, "
            "claim_type, evidence_ref, previous_claim_id, "
            "entry_hash, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.entity_id, event.kind, payload, event.run_name,
                event.dataset, event.actor, event.trust,
                event.claim_type, event.evidence_ref, event.previous_claim_id,
                event.entry_hash, event.recorded_at.isoformat(),
            ),
        )
        # Fire-and-forget: resolve_clusters ignores the event_id; skipping the
        # read-back keeps write_pipeline() batching (#1912).
        if not return_id:
            return None
        row = self._fetchone(
            "SELECT MAX(event_id) AS event_id FROM identity_events WHERE entity_id = ?",
            (event.entity_id,),
        )
        return int(row["event_id"]) if row and row["event_id"] is not None else None

    def history(
        self, entity_id: str, limit: int | None = None
    ) -> list[IdentityEvent]:
        if self._backend == "mongo":
            return self._mongo.history(entity_id, limit=limit)
        if limit:
            rows = self._fetchall(
                "SELECT * FROM identity_events WHERE entity_id = ? "
                "ORDER BY event_id LIMIT ?",
                (entity_id, limit),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM identity_events WHERE entity_id = ? ORDER BY event_id",
                (entity_id,),
            )
        return [self._row_to_event(r) for r in rows]

    def export_audit_log(
        self, *, dataset: str | None = None, actor: str | None = None,
        since: datetime | None = None,
    ) -> list[IdentityEvent]:
        """The full append-only event log in commit order (event_id ASC), for
        compliance review/export (#1078). Optional ``dataset`` / ``actor`` /
        ``since`` filters. Each event carries who (``actor``), trust, when
        (``recorded_at``), why (``payload['reason']``) -- so a reviewer can
        reconstruct exactly which actor changed what, when, and on what basis.
        Callers serialize to JSONL/CSV as needed."""
        if self._backend == "mongo":
            return self._mongo.export_audit_log(
                dataset=dataset, actor=actor, since=since
            )
        clauses: list[str] = []
        params: list[Any] = []
        if dataset is not None:
            clauses.append("dataset = ?")
            params.append(dataset)
        if actor is not None:
            clauses.append("actor = ?")
            params.append(actor)
        if since is not None:
            clauses.append("recorded_at >= ?")
            params.append(since.isoformat())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self._fetchall(
            f"SELECT * FROM identity_events{where} ORDER BY event_id",
            tuple(params),
        )
        return [self._row_to_event(r) for r in rows]

    # ----- Audit seal chain (#1078) -----

    def add_seal(self, seal: AuditSeal) -> int | None:
        """Persist a tamper-evidence seal and return its id. Used by
        ``audit.seal_audit_log``; the chain logic lives there, not here."""
        if self._backend == "mongo":
            raise NotImplementedError(
                "audit seals are not supported on the mongo backend"
            )
        self._exec(
            "INSERT INTO audit_seals "
            "(dataset, root_hash, event_count, last_event_id, prev_seal_id, "
            "prev_root, actor, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                seal.dataset, seal.root_hash, seal.event_count,
                seal.last_event_id, seal.prev_seal_id, seal.prev_root,
                seal.actor, seal.created_at.isoformat(),
            ),
        )
        row = self._fetchone("SELECT MAX(seal_id) AS seal_id FROM audit_seals", ())
        return int(row["seal_id"]) if row and row["seal_id"] is not None else None

    def latest_seal(self, *, dataset: str | None = None) -> AuditSeal | None:
        """The most recent seal for the given ``dataset`` scope (``None`` =
        global chain), or ``None`` if the chain is empty."""
        if self._backend == "mongo":
            raise NotImplementedError(
                "audit seals are not supported on the mongo backend"
            )
        if dataset is None:
            row = self._fetchone(
                "SELECT * FROM audit_seals WHERE dataset IS NULL "
                "ORDER BY seal_id DESC LIMIT 1",
                (),
            )
        else:
            row = self._fetchone(
                "SELECT * FROM audit_seals WHERE dataset = ? "
                "ORDER BY seal_id DESC LIMIT 1",
                (dataset,),
            )
        return self._row_to_seal(row) if row else None

    def list_seals(self, *, dataset: str | None = None) -> list[AuditSeal]:
        """Every seal for the given ``dataset`` scope in creation order."""
        if self._backend == "mongo":
            raise NotImplementedError(
                "audit seals are not supported on the mongo backend"
            )
        if dataset is None:
            rows = self._fetchall(
                "SELECT * FROM audit_seals WHERE dataset IS NULL ORDER BY seal_id",
                (),
            )
        else:
            rows = self._fetchall(
                "SELECT * FROM audit_seals WHERE dataset = ? ORDER BY seal_id",
                (dataset,),
            )
        return [self._row_to_seal(r) for r in rows]

    def has_run_event(self, entity_id: str, run_name: str, kind: str) -> bool:
        if self._backend == "mongo":
            return self._mongo.has_run_event(entity_id, run_name, kind)
        row = self._fetchone(
            "SELECT 1 AS one FROM identity_events "
            "WHERE entity_id = ? AND run_name = ? AND kind = ? LIMIT 1",
            (entity_id, run_name, kind),
        )
        return row is not None

    def add_alias(self, alias: IdentityAlias) -> None:
        if self._backend == "mongo":
            self._mongo.add_alias(alias)
            return
        self._exec(
            "INSERT OR REPLACE INTO identity_aliases "
            "(alias, entity_id, kind, dataset, recorded_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                alias.alias, alias.entity_id, alias.kind, alias.dataset,
                alias.recorded_at.isoformat(),
            ),
        )

    def resolve_alias(self, alias: str, kind: str = "external_id") -> str | None:
        if self._backend == "mongo":
            return self._mongo.resolve_alias(alias, kind=kind)
        row = self._fetchone(
            "SELECT entity_id FROM identity_aliases WHERE alias = ? AND kind = ?",
            (alias, kind),
        )
        return row["entity_id"] if row else None

    def _exec(self, sql: str, params: tuple) -> None:
        if self._backend == "sqlite":
            self._conn.execute(sql, params)
            # Inside ``bulk_writes`` (``_sqlite_batch`` > 0) commit in chunks so
            # a multi-million-row resolve cannot grow the WAL without bound
            # before it gets a chance to checkpoint (#2105).
            if self._sqlite_batch:
                self._sqlite_pending += 1
                if self._sqlite_pending >= self._sqlite_batch:
                    self._conn.execute("COMMIT")
                    self._conn.execute("BEGIN")
                    self._sqlite_pending = 0
            return
        with self._conn.cursor() as cur:
            cur.execute(self._pg_sql(sql), params)

    def _fetchone(self, sql: str, params: tuple) -> Any:
        if self._backend == "sqlite":
            return self._conn.execute(sql, params).fetchone()
        from psycopg.rows import dict_row
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(self._pg_sql(sql), params)
            return cur.fetchone()

    def _fetchall(self, sql: str, params: tuple) -> list[Any]:
        if self._backend == "sqlite":
            return self._conn.execute(sql, params).fetchall()
        from psycopg.rows import dict_row
        with self._conn.cursor(row_factory=dict_row) as cur:
            cur.execute(self._pg_sql(sql), params)
            return list(cur.fetchall())

    @staticmethod
    def _pg_sql(sql: str) -> str:
        out = sql.replace("?", "%s")
        out = out.replace("INSERT OR IGNORE", "INSERT")
        out = out.replace("INSERT OR REPLACE", "INSERT")
        return out

    @staticmethod
    def _row_to_identity(row: Any) -> IdentityNode:
        gr = row["golden_record"]
        if isinstance(gr, str):
            gr = json.loads(gr) if gr else None
        return IdentityNode(
            entity_id=row["entity_id"],
            status=row["status"],
            merged_into=row["merged_into"],
            golden_record=gr,
            confidence=row["confidence"],
            dataset=row["dataset"],
            created_at=_to_dt(row["created_at"]),
            updated_at=_to_dt(row["updated_at"]),
        )

    @staticmethod
    def _row_to_record(row: Any) -> SourceRecord:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload) if payload else None
        return SourceRecord(
            record_id=row["record_id"],
            source=row["source"],
            source_pk=row["source_pk"],
            record_hash=row["record_hash"],
            entity_id=row["entity_id"],
            payload=payload,
            dataset=row["dataset"],
            first_seen_at=_to_dt(row["first_seen_at"]),
            last_seen_at=_to_dt(row["last_seen_at"]),
        )

    @staticmethod
    def _row_to_edge(row: Any) -> EvidenceEdge:
        def _maybe_json(v: Any) -> Any:
            if isinstance(v, str):
                return json.loads(v) if v else None
            return v
        return EvidenceEdge(
            entity_id=row["entity_id"],
            record_a_id=row["record_a_id"],
            record_b_id=row["record_b_id"],
            kind=row["kind"],
            score=row["score"],
            matchkey_name=row["matchkey_name"],
            field_scores=_maybe_json(row["field_scores"]),
            negative_evidence=_maybe_json(row["negative_evidence"]),
            controller_snapshot=_maybe_json(row["controller_snapshot"]),
            run_name=row["run_name"],
            dataset=row["dataset"],
            actor=_row_get(row, "actor"),
            trust=_row_get(row, "trust"),
            recorded_at=_to_dt(row["recorded_at"]),
            edge_id=row["edge_id"],
        )

    @staticmethod
    def _row_to_event(row: Any) -> IdentityEvent:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload) if payload else None
        return IdentityEvent(
            entity_id=row["entity_id"],
            kind=row["kind"],
            payload=payload,
            run_name=row["run_name"],
            dataset=row["dataset"],
            actor=_row_get(row, "actor"),
            trust=_row_get(row, "trust"),
            claim_type=_row_get(row, "claim_type"),
            evidence_ref=_row_get(row, "evidence_ref"),
            previous_claim_id=_row_get(row, "previous_claim_id"),
            entry_hash=_row_get(row, "entry_hash"),
            recorded_at=_to_dt(row["recorded_at"]),
            event_id=row["event_id"],
        )

    @staticmethod
    def _row_to_seal(row: Any) -> AuditSeal:
        return AuditSeal(
            root_hash=row["root_hash"],
            event_count=int(row["event_count"]),
            last_event_id=(
                int(row["last_event_id"])
                if row["last_event_id"] is not None
                else None
            ),
            dataset=row["dataset"],
            prev_seal_id=(
                int(row["prev_seal_id"])
                if row["prev_seal_id"] is not None
                else None
            ),
            prev_root=row["prev_root"],
            actor=_row_get(row, "actor"),
            created_at=_to_dt(row["created_at"]),
            seal_id=int(row["seal_id"]),
        )


def _to_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v)
        except ValueError:
            return datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
    return datetime.now()


def _row_get(row: Any, key: str) -> Any:
    """Column value or None if the column is absent -- tolerates rows from a
    pre-provenance schema (sqlite3.Row raises IndexError, dict raises KeyError on
    a missing key) so reads never break before the migration runs."""
    try:
        if hasattr(row, "keys") and key not in row.keys():
            return None
        return row[key]
    except (KeyError, IndexError):
        return None
