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
import re
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


SCHEMA_VERSION = 7

# Relationship field names are interpolated into a JSON path / column expression,
# so they are validated against this before use (never user free-text at the SQL).
_SAFE_FIELD = re.compile(r"[A-Za-z0-9_]+")

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

-- Persisted blocking index (control-plane manifesto C2 / decision 0047 §9.1):
-- one row per (record, blocking pass) -> the block key that record fell in, so a
-- NEW record can find candidate persisted records that share a block key WITHOUT
-- re-blocking the whole corpus in RAM. entity_id is the identity the record
-- currently belongs to (nullable until resolved). pass_sig identifies which
-- blocking pass produced the key (a record can sit in several passes' blocks).
CREATE TABLE IF NOT EXISTS identity_record_block_keys (
    record_id  TEXT NOT NULL,
    entity_id  TEXT,
    block_key  TEXT NOT NULL,
    pass_sig   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (record_id, pass_sig, block_key)
);
CREATE INDEX IF NOT EXISTS idx_rbk_block  ON identity_record_block_keys(pass_sig, block_key);
CREATE INDEX IF NOT EXISTS idx_rbk_entity ON identity_record_block_keys(entity_id);
CREATE INDEX IF NOT EXISTS idx_rbk_record ON identity_record_block_keys(record_id);

-- semantic-graph: entity<->entity relationship edges derived from a shared
-- NON-identity attribute (two entities on one clinic phone, one address, ...).
-- Distinct from evidence_edges (which is record-level, WITHIN an entity); this is
-- entity-level, BETWEEN entities. The UNIQUE key omits run_name on purpose so a
-- re-resolve is idempotent (INSERT OR IGNORE de-dupes across runs). Endpoints are
-- canonicalized entity_a_id < entity_b_id so each pair is stored once.
CREATE TABLE IF NOT EXISTS identity_relationships (
    entity_a_id   TEXT NOT NULL,
    entity_b_id   TEXT NOT NULL,
    kind          TEXT NOT NULL,
    field         TEXT NOT NULL,
    shared_value  TEXT,
    dataset       TEXT,
    recorded_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (entity_a_id, entity_b_id, kind, shared_value)
);
CREATE INDEX IF NOT EXISTS idx_rel_a    ON identity_relationships(entity_a_id);
CREATE INDEX IF NOT EXISTS idx_rel_b    ON identity_relationships(entity_b_id);
CREATE INDEX IF NOT EXISTS idx_rel_kind ON identity_relationships(kind);
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
        if version < 6:
            # v5 -> v6: persisted blocking index (C2). CREATE TABLE IF NOT EXISTS
            # + indexes, idempotent on fresh (already carries it from ``_SCHEMA``)
            # and migrated DBs.
            self._ensure_block_index_table()
        if version < 7:
            # v6 -> v7: entity<->entity relationship edges (semantic-graph). CREATE
            # TABLE IF NOT EXISTS + indexes, idempotent on fresh (already in
            # ``_SCHEMA``) and migrated DBs.
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS identity_relationships (
                    entity_a_id   TEXT NOT NULL,
                    entity_b_id   TEXT NOT NULL,
                    kind          TEXT NOT NULL,
                    field         TEXT NOT NULL,
                    shared_value  TEXT,
                    dataset       TEXT,
                    recorded_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (entity_a_id, entity_b_id, kind, shared_value)
                );
                CREATE INDEX IF NOT EXISTS idx_rel_a    ON identity_relationships(entity_a_id);
                CREATE INDEX IF NOT EXISTS idx_rel_b    ON identity_relationships(entity_b_id);
                CREATE INDEX IF NOT EXISTS idx_rel_kind ON identity_relationships(kind);
                """
            )
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

    def _ensure_block_index_table(self) -> None:
        """Create the persisted blocking-index table + indexes if absent (C2).
        Idempotent CREATE ... IF NOT EXISTS, safe on fresh and migrated DBs."""
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS identity_record_block_keys (
                record_id  TEXT NOT NULL,
                entity_id  TEXT,
                block_key  TEXT NOT NULL,
                pass_sig   TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (record_id, pass_sig, block_key)
            );
            CREATE INDEX IF NOT EXISTS idx_rbk_block  ON identity_record_block_keys(pass_sig, block_key);
            CREATE INDEX IF NOT EXISTS idx_rbk_entity ON identity_record_block_keys(entity_id);
            CREATE INDEX IF NOT EXISTS idx_rbk_record ON identity_record_block_keys(record_id);
            """
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
        -- Persisted blocking index (C2): candidate generation for incremental
        -- resolution against persisted identities without re-blocking the corpus.
        CREATE TABLE IF NOT EXISTS identity_record_block_keys (
            record_id  TEXT NOT NULL,
            entity_id  TEXT,
            block_key  TEXT NOT NULL,
            pass_sig   TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (record_id, pass_sig, block_key)
        );
        CREATE INDEX IF NOT EXISTS idx_rbk_block  ON identity_record_block_keys(pass_sig, block_key);
        CREATE INDEX IF NOT EXISTS idx_rbk_entity ON identity_record_block_keys(entity_id);
        CREATE INDEX IF NOT EXISTS idx_rbk_record ON identity_record_block_keys(record_id);

        -- semantic-graph: entity<->entity relationship edges (see _SCHEMA).
        CREATE TABLE IF NOT EXISTS identity_relationships (
            entity_a_id   TEXT NOT NULL,
            entity_b_id   TEXT NOT NULL,
            kind          TEXT NOT NULL,
            field         TEXT NOT NULL,
            shared_value  TEXT,
            dataset       TEXT,
            recorded_at   TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (entity_a_id, entity_b_id, kind, shared_value)
        );
        CREATE INDEX IF NOT EXISTS idx_rel_a    ON identity_relationships(entity_a_id);
        CREATE INDEX IF NOT EXISTS idx_rel_b    ON identity_relationships(entity_b_id);
        CREATE INDEX IF NOT EXISTS idx_rel_kind ON identity_relationships(kind);
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

    def _sqlite_stage(self, stage: str, cols: list[str], df: Any) -> None:
        """Load ``df[cols]`` into a per-connection TEMP staging table, replacing
        any prior contents. The staging table is reused across flushes
        (``CREATE ... IF NOT EXISTS`` + ``DELETE FROM``) so a batched resolve
        does not pay repeated DDL. Datetime cells are isoformatted so the
        staged rows are byte-identical to what the per-row ``upsert_*`` path
        writes (which stores ``.isoformat()`` strings). Runs inside whatever
        transaction ``bulk_writes`` opened."""
        import polars as pl  # noqa: PLC0415
        missing = [c for c in cols if c not in df.columns]
        if missing:
            df = df.with_columns([pl.lit(None).alias(c) for c in missing])
        collist = ", ".join(cols)
        conn = self._conn
        conn.execute(f"CREATE TEMP TABLE IF NOT EXISTS {stage} ({collist})")
        conn.execute(f"DELETE FROM {stage}")
        placeholders = ", ".join("?" * len(cols))
        conn.executemany(
            f"INSERT INTO {stage} ({collist}) VALUES ({placeholders})",
            (
                tuple(
                    v.isoformat() if isinstance(v, datetime) else v for v in row
                )
                for row in df.select(cols).iter_rows()
            ),
        )

    def bulk_flush_checkpoint(self) -> None:
        """Commit-and-reopen the current SQLite batch transaction so the WAL
        cannot grow without bound across the many bulk flushes of one
        multi-million-row resolve. No-op unless SQLite inside an open
        ``bulk_writes`` transaction -- mirrors the per-statement chunk commit in
        ``_exec`` for the bulk (COPY-equivalent) path. TEMP staging tables
        survive the commit (they live for the connection, not the transaction),
        so the next flush reuses them."""
        if self._backend != "sqlite":
            return
        if self._sqlite_batch and self._conn.in_transaction:
            self._conn.execute("COMMIT")
            self._conn.execute("BEGIN")
            self._sqlite_pending = 0

    def bulk_upsert_identities(self, df: Any) -> None:
        if self._backend == "sqlite":
            if df.height == 0:
                return
            cols = [
                "entity_id", "status", "merged_into", "golden_record",
                "confidence", "dataset", "created_at", "updated_at",
            ]
            self._sqlite_stage("_stage_identity_nodes", cols, df)
            # ``WHERE true`` disambiguates INSERT..SELECT..ON CONFLICT for the
            # SQLite parser (without it the ON CONFLICT binds to the SELECT).
            # DO UPDATE set mirrors the per-row ``upsert_identity`` exactly, so
            # a re-resolve of the same cluster is idempotent (brand-new clusters
            # never conflict, but keeping the sets identical is the parity
            # contract with the row path).
            self._conn.execute(
                """
                INSERT INTO identity_nodes
                    (entity_id, status, merged_into, golden_record,
                     confidence, dataset, created_at, updated_at)
                SELECT entity_id, status, merged_into, golden_record,
                       confidence, dataset, created_at, updated_at
                FROM _stage_identity_nodes WHERE true
                ON CONFLICT(entity_id) DO UPDATE SET
                    status=excluded.status,
                    merged_into=excluded.merged_into,
                    golden_record=excluded.golden_record,
                    confidence=excluded.confidence,
                    dataset=excluded.dataset,
                    updated_at=excluded.updated_at
                """
            )
            return
        if self._backend != "postgres":
            raise NotImplementedError(
                "bulk_upsert_identities requires Postgres or SQLite backend; "
                "use upsert_identity in a loop for other backends",
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
        if self._backend == "sqlite":
            if df.height == 0:
                return
            # Carries ``payload`` -- unlike the leaner Postgres COPY path, which
            # drops it. The per-row SQLite ``upsert_record`` stores payload, so
            # routing brand-new clusters through this bulk path must too or a
            # SQLite user silently loses their source-record payloads (the
            # documented payload-drop trap). ON CONFLICT set mirrors
            # ``upsert_record``.
            cols = [
                "record_id", "source", "source_pk", "record_hash",
                "entity_id", "payload", "dataset",
                "first_seen_at", "last_seen_at",
            ]
            self._sqlite_stage("_stage_source_records", cols, df)
            self._conn.execute(
                """
                INSERT INTO source_records
                    (record_id, source, source_pk, record_hash, entity_id,
                     payload, dataset, first_seen_at, last_seen_at)
                SELECT record_id, source, source_pk, record_hash, entity_id,
                       payload, dataset, first_seen_at, last_seen_at
                FROM _stage_source_records WHERE true
                ON CONFLICT(record_id) DO UPDATE SET
                    record_hash=excluded.record_hash,
                    entity_id=excluded.entity_id,
                    payload=excluded.payload,
                    last_seen_at=excluded.last_seen_at
                """
            )
            return
        if self._backend != "postgres":
            raise NotImplementedError(
                "bulk_upsert_records requires Postgres or SQLite backend; "
                "use upsert_record in a loop for other backends",
            )
        if df.height == 0:
            return
        # Carry ``payload`` (JSONB) like the SQLite bulk path and the per-row
        # ``upsert_record`` -- source_records has the column, so the Postgres bulk
        # path must populate it or routing brand-new clusters here silently drops
        # record payloads (the payload-drop trap, closed for edges/events too).
        cols = [
            "record_id", "source", "source_pk", "record_hash",
            "entity_id", "payload", "dataset", "first_seen_at", "last_seen_at",
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
                "payload, dataset, first_seen_at, last_seen_at) FROM STDIN"
            ) as copy:
                for row in df.select(cols).iter_rows():
                    copy.write_row(row)
            cur.execute(
                """
                INSERT INTO source_records
                    (record_id, source, source_pk, record_hash, entity_id,
                     payload, dataset, first_seen_at, last_seen_at)
                SELECT record_id, source, source_pk, record_hash, entity_id,
                       payload, dataset, first_seen_at, last_seen_at
                FROM _stage_source_records
                ON CONFLICT (record_id) DO UPDATE SET
                    record_hash = EXCLUDED.record_hash,
                    entity_id = EXCLUDED.entity_id,
                    payload = EXCLUDED.payload,
                    last_seen_at = EXCLUDED.last_seen_at
                """
            )
            cur.execute("DROP TABLE IF EXISTS _stage_source_records")

    def bulk_add_edges(self, df: Any) -> None:
        if self._backend == "sqlite":
            if df.height == 0:
                return
            # ``INSERT OR IGNORE`` honours the UNIQUE(entity_id, record_a_id,
            # record_b_id, kind, run_name) constraint, matching the per-row
            # ``add_edge`` (which also uses INSERT OR IGNORE). Carries
            # controller_snapshot / actor / trust (SQLite has contract tests
            # asserting edge provenance); field_scores / negative_evidence stay
            # NULL because the per-row brand-new same_as edge does not set them
            # either.
            cols = [
                "entity_id", "record_a_id", "record_b_id", "kind", "score",
                "matchkey_name", "controller_snapshot", "run_name", "dataset",
                "actor", "trust", "recorded_at",
            ]
            self._sqlite_stage("_stage_evidence_edges", cols, df)
            self._conn.execute(
                """
                INSERT OR IGNORE INTO evidence_edges
                    (entity_id, record_a_id, record_b_id, kind, score,
                     matchkey_name, controller_snapshot, run_name, dataset,
                     actor, trust, recorded_at)
                SELECT entity_id, record_a_id, record_b_id, kind, score,
                       matchkey_name, controller_snapshot, run_name, dataset,
                       actor, trust, recorded_at
                FROM _stage_evidence_edges
                """
            )
            return
        if self._backend != "postgres":
            raise NotImplementedError(
                "bulk_add_edges requires Postgres or SQLite backend; "
                "use add_edge in a loop for other backends",
            )
        if df.height == 0:
            return
        # Carry controller_snapshot (JSONB) / actor / trust like the SQLite bulk
        # path and the per-row ``add_edge`` -- evidence_edges has these columns
        # and the per-row Postgres path writes them, so the bulk path must too or
        # edge provenance is silently lost on the brand-new-cluster route.
        cols = [
            "entity_id", "record_a_id", "record_b_id", "kind", "score",
            "matchkey_name", "controller_snapshot", "run_name", "dataset",
            "actor", "trust", "recorded_at",
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
                    controller_snapshot JSONB,
                    run_name TEXT,
                    dataset TEXT,
                    actor TEXT,
                    trust DOUBLE PRECISION,
                    recorded_at TIMESTAMPTZ
                ) ON COMMIT DROP
                """
            )
            with cur.copy(
                "COPY _stage_evidence_edges "
                "(entity_id, record_a_id, record_b_id, kind, score, "
                "matchkey_name, controller_snapshot, run_name, dataset, "
                "actor, trust, recorded_at) FROM STDIN"
            ) as copy:
                for row in df.select(cols).iter_rows():
                    copy.write_row(row)
            cur.execute(
                """
                INSERT INTO evidence_edges
                    (entity_id, record_a_id, record_b_id, kind, score,
                     matchkey_name, controller_snapshot, run_name, dataset,
                     actor, trust, recorded_at)
                SELECT entity_id, record_a_id, record_b_id, kind, score,
                       matchkey_name, controller_snapshot, run_name, dataset,
                       actor, trust, recorded_at
                FROM _stage_evidence_edges
                ON CONFLICT (entity_id, record_a_id, record_b_id, kind,
                             run_name) DO NOTHING
                """
            )
            cur.execute("DROP TABLE IF EXISTS _stage_evidence_edges")

    def bulk_emit_events(self, df: Any) -> None:
        if self._backend == "sqlite":
            if df.height == 0:
                return
            # Carries payload / actor / trust -- the audit spine the per-row
            # ``emit_event`` records (the Postgres bulk path below now carries
            # them too). ``entry_hash`` is left NULL: the seal / verify path already
            # hashes NULL-entry_hash rows on the fly (pre-#1078 rows do the
            # same), so the tamper-evidence guarantee holds without
            # reconstructing an IdentityEvent per row on the flush hot path.
            cols = [
                "entity_id", "kind", "payload", "run_name", "dataset",
                "actor", "trust", "recorded_at",
            ]
            self._sqlite_stage("_stage_identity_events", cols, df)
            self._conn.execute(
                """
                INSERT INTO identity_events
                    (entity_id, kind, payload, run_name, dataset,
                     actor, trust, recorded_at)
                SELECT entity_id, kind, payload, run_name, dataset,
                       actor, trust, recorded_at
                FROM _stage_identity_events
                """
            )
            return
        if self._backend != "postgres":
            raise NotImplementedError(
                "bulk_emit_events requires Postgres or SQLite backend; "
                "use emit_event in a loop for other backends",
            )
        if df.height == 0:
            return
        # Carry payload (JSONB) / actor / trust like the SQLite bulk path and the
        # per-row ``emit_event`` -- identity_events has these columns and the
        # per-row Postgres path writes them, so the bulk path must too or the
        # audit spine (who/why/trust) is silently lost on the bulk route.
        # ``entry_hash`` is left NULL: the seal/verify path hashes NULL-entry_hash
        # rows on the fly (matching the SQLite bulk branch above).
        cols = [
            "entity_id", "kind", "payload", "run_name", "dataset",
            "actor", "trust", "recorded_at",
        ]
        conn: Any = self._conn
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                """
                CREATE TEMP TABLE _stage_identity_events (
                    entity_id TEXT,
                    kind TEXT,
                    payload JSONB,
                    run_name TEXT,
                    dataset TEXT,
                    actor TEXT,
                    trust DOUBLE PRECISION,
                    recorded_at TIMESTAMPTZ
                ) ON COMMIT DROP
                """
            )
            with cur.copy(
                "COPY _stage_identity_events "
                "(entity_id, kind, payload, run_name, dataset, "
                "actor, trust, recorded_at) FROM STDIN"
            ) as copy:
                for row in df.select(cols).iter_rows():
                    copy.write_row(row)
            cur.execute(
                """
                INSERT INTO identity_events
                    (entity_id, kind, payload, run_name, dataset,
                     actor, trust, recorded_at)
                SELECT entity_id, kind, payload, run_name, dataset,
                       actor, trust, recorded_at
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

    # ----- Persisted blocking index (C2, control-plane manifesto §4(ii)) -----
    #
    # The bidirectional-seam foundation: the control plane persists the block
    # keys each record fell in, so incremental resolution (compute) can ask the
    # store (control) for candidate records sharing a block key WITHOUT
    # re-blocking the whole corpus in RAM. Slice 1 = the store index + its
    # write/query API; wiring population-on-write + the incremental candidate
    # query is the next slice.

    def index_record_block_keys(
        self,
        record_id: str,
        entity_id: str | None,
        keys: Iterable[tuple[str, str]],
    ) -> None:
        """Persist the ``(pass_sig, block_key)`` pairs a record falls in.

        Idempotent per (record_id, pass_sig, block_key): re-indexing refreshes
        ``entity_id`` (so a record reassigned to a new entity on a later resolve
        has its index rows re-pointed) without duplicating. ``keys`` is an
        iterable of ``(pass_sig, block_key)``; null block keys are skipped.
        """
        if self._backend == "mongo":
            raise NotImplementedError(
                "block-key index is not supported on the mongo backend"
            )
        rows = [
            (record_id, entity_id, str(bk), str(ps))
            for ps, bk in keys
            if bk is not None
        ]
        if not rows:
            return
        if self._backend == "sqlite":
            self._conn.executemany(
                "INSERT INTO identity_record_block_keys "
                "(record_id, entity_id, block_key, pass_sig) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(record_id, pass_sig, block_key) "
                "DO UPDATE SET entity_id=excluded.entity_id",
                rows,
            )
            # Bounded-WAL chunk commit inside ``bulk_writes`` (mirrors ``_exec``).
            if self._sqlite_batch:
                self._sqlite_pending += len(rows)
                if self._sqlite_pending >= self._sqlite_batch:
                    self._conn.execute("COMMIT")
                    self._conn.execute("BEGIN")
                    self._sqlite_pending = 0
            return
        with self._conn.cursor() as cur:
            cur.executemany(
                "INSERT INTO identity_record_block_keys "
                "(record_id, entity_id, block_key, pass_sig) "
                "VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (record_id, pass_sig, block_key) "
                "DO UPDATE SET entity_id = EXCLUDED.entity_id",
                rows,
            )

    def candidates_by_block_keys(
        self, keys: Iterable[tuple[str, str]]
    ) -> set[str]:
        """Record ids sharing ANY ``(pass_sig, block_key)`` with ``keys``.

        The persisted-index candidate set for an incoming record: the records a
        blocking pass would co-locate it with, read from the durable index
        instead of a full-corpus re-block. Excludes the query record only if the
        caller filters it out (this returns raw block-mates). ``keys`` is an
        iterable of ``(pass_sig, block_key)``."""
        if self._backend == "mongo":
            raise NotImplementedError(
                "block-key index is not supported on the mongo backend"
            )
        pairs = [(str(ps), str(bk)) for ps, bk in keys if bk is not None]
        if not pairs:
            return set()
        out: set[str] = set()
        # Two host params per pair; chunk to stay under SQLite's ~999 cap.
        _CHUNK = 450
        for i in range(0, len(pairs), _CHUNK):
            chunk = pairs[i:i + _CHUNK]
            clause = " OR ".join(
                "(pass_sig = ? AND block_key = ?)" for _ in chunk
            )
            flat = [v for pair in chunk for v in pair]
            rows = self._fetchall(
                "SELECT DISTINCT record_id FROM identity_record_block_keys "
                f"WHERE {clause}",
                tuple(flat),
            )
            for r in rows:
                out.add(r["record_id"])
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

    def status_counts(self, dataset: str | None = None) -> dict[str, int]:
        """Entity count per status in one grouped query.

        Lets ``identity_summary_stats`` tally statuses without paging every node
        (#2198). Raises on mongo so the caller keeps its paged fallback."""
        if self._backend == "mongo":
            raise NotImplementedError("status_counts: mongo uses the paged fallback")
        if dataset is None:
            rows = self._fetchall(
                "SELECT status, COUNT(*) AS n FROM identity_nodes GROUP BY status", (),
            )
        else:
            rows = self._fetchall(
                "SELECT status, COUNT(*) AS n FROM identity_nodes "
                "WHERE dataset = ? GROUP BY status", (dataset,),
            )
        return {r["status"]: int(r["n"]) for r in rows}

    def active_record_stats(
        self, dataset: str | None = None,
    ) -> tuple[dict[str, int], dict[str, int]]:
        """``(records_per_active_entity, records_per_source)`` in two grouped
        queries instead of one ``get_records_for_entity`` per entity (#2198).

        Records live on active entities (a merge reassigns them), matching
        ``identity_summary_stats``' per-active-entity accounting. The entity map
        LEFT JOINs so an active entity with zero records still appears (count 0),
        preserving the prior ``len()``-based semantics. Raises on mongo so the
        caller keeps its paged fallback."""
        if self._backend == "mongo":
            raise NotImplementedError(
                "active_record_stats: mongo uses the paged fallback"
            )
        ds = "" if dataset is None else " AND n.dataset = ?"
        params: tuple = () if dataset is None else (dataset,)
        per_entity_rows = self._fetchall(
            "SELECT n.entity_id AS eid, COUNT(sr.record_id) AS n "
            "FROM identity_nodes n "
            "LEFT JOIN source_records sr ON sr.entity_id = n.entity_id "
            f"WHERE n.status = 'active'{ds} "
            "GROUP BY n.entity_id",
            params,
        )
        source_rows = self._fetchall(
            "SELECT sr.source AS source, COUNT(*) AS n "
            "FROM source_records sr "
            "JOIN identity_nodes n ON sr.entity_id = n.entity_id "
            f"WHERE n.status = 'active'{ds} "
            "GROUP BY sr.source",
            params,
        )
        per_entity = {r["eid"]: int(r["n"]) for r in per_entity_rows}
        source_breakdown = {r["source"]: int(r["n"]) for r in source_rows}
        return per_entity, source_breakdown

    # ----- Semantic-graph: entity<->entity relationships -----

    def relationship_groups(
        self, field: str, dataset: str | None,
        min_entities: int, max_entities: int,
    ) -> list[tuple[str, list[str]]]:
        """Distinct entities that share a value of ``field`` (a payload key),
        grouped in ONE query. Returns ``[(shared_value, [entity_id, ...]), ...]``
        for values held by between ``min_entities`` and ``max_entities`` distinct
        entities. The field is read out of the JSON ``payload`` column; the
        cardinality gate runs in SQL so only qualifying groups come back."""
        if self._backend == "mongo":
            raise NotImplementedError("relationship_groups: not supported on mongo")
        if not _SAFE_FIELD.fullmatch(field):
            raise ValueError(f"unsafe relationship field name: {field!r}")
        if self._backend == "postgres":
            vexpr = f"((sr.payload)::jsonb ->> '{field}')"
            agg = "string_agg(DISTINCT sr.entity_id, ',')"
        else:
            vexpr = f"json_extract(sr.payload, '$.{field}')"
            agg = "group_concat(DISTINCT sr.entity_id)"
        ds = "" if dataset is None else " AND sr.dataset = ?"
        params: tuple = () if dataset is None else (dataset,)
        sql = (
            f"SELECT {vexpr} AS v, {agg} AS eids "
            "FROM source_records sr "
            f"WHERE sr.entity_id IS NOT NULL AND {vexpr} IS NOT NULL "
            f"AND {vexpr} != ''{ds} "
            f"GROUP BY {vexpr} "
            "HAVING COUNT(DISTINCT sr.entity_id) >= ? "
            "AND COUNT(DISTINCT sr.entity_id) <= ?"
        )
        rows = self._fetchall(sql, params + (min_entities, max_entities))
        return [(r["v"], str(r["eids"]).split(",")) for r in rows]

    def add_relationships(self, rows: list[tuple]) -> int:
        """Insert ``(entity_a, entity_b, kind, field, shared_value, dataset)``
        relationship edges, idempotently. Endpoints are canonicalized
        (a < b) and the PRIMARY KEY de-dupes across runs. Returns rows attempted."""
        if not rows:
            return 0
        norm = []
        for a, b, kind, field, val, dataset in rows:
            if a == b:
                continue
            lo, hi = (a, b) if a < b else (b, a)
            norm.append((lo, hi, kind, field, val, dataset))
        if not norm:
            return 0
        if self._backend == "mongo":
            raise NotImplementedError("add_relationships: not supported on mongo")
        if self._backend == "postgres":
            sql = (
                "INSERT INTO identity_relationships "
                "(entity_a_id, entity_b_id, kind, field, shared_value, dataset) "
                "VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (entity_a_id, entity_b_id, kind, shared_value) DO NOTHING"
            )
            with self._conn.cursor() as cur:
                cur.executemany(sql, norm)
        else:
            self._conn.executemany(
                "INSERT OR IGNORE INTO identity_relationships "
                "(entity_a_id, entity_b_id, kind, field, shared_value, dataset) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                norm,
            )
        return len(norm)

    def get_relationships(self, entity_id: str) -> list[dict[str, Any]]:
        """Every relationship edge touching ``entity_id`` (either endpoint),
        as ``{other_entity_id, kind, field, shared_value}``."""
        if self._backend == "mongo":
            raise NotImplementedError("get_relationships: not supported on mongo")
        rows = self._fetchall(
            "SELECT entity_a_id, entity_b_id, kind, field, shared_value "
            "FROM identity_relationships "
            "WHERE entity_a_id = ? OR entity_b_id = ?",
            (entity_id, entity_id),
        )
        out = []
        for r in rows:
            other = r["entity_b_id"] if r["entity_a_id"] == entity_id else r["entity_a_id"]
            out.append({"other_entity_id": other, "kind": r["kind"],
                        "field": r["field"], "shared_value": r["shared_value"]})
        return out

    def count_relationships(self) -> int:
        if self._backend == "mongo":
            raise NotImplementedError("count_relationships: not supported on mongo")
        row = self._fetchone("SELECT COUNT(*) AS n FROM identity_relationships", ())
        return int(row["n"]) if row else 0

    def list_relationships(
        self, dataset: str | None = None,
    ) -> list[dict[str, Any]]:
        """All relationship edges as ``{entity_a_id, entity_b_id, kind, field,
        shared_value}`` (optionally dataset-scoped). Used by the GoldenGraph
        export."""
        if self._backend == "mongo":
            raise NotImplementedError("list_relationships: not supported on mongo")
        where = "" if dataset is None else " WHERE dataset = ?"
        params: tuple = () if dataset is None else (dataset,)
        rows = self._fetchall(
            "SELECT entity_a_id, entity_b_id, kind, field, shared_value "
            f"FROM identity_relationships{where}",
            params,
        )
        return [
            {"entity_a_id": r["entity_a_id"], "entity_b_id": r["entity_b_id"],
             "kind": r["kind"], "field": r["field"], "shared_value": r["shared_value"]}
            for r in rows
        ]

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
