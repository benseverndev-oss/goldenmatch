"""IdentityStore -- SQLite/Postgres persistence for the Identity Graph.

Mirrors the ``MemoryStore`` pattern in ``goldenmatch/core/memory/store.py``:
SQLite default, Postgres optional, lazy import. WAL mode + busy timeout for
multi-process safety. Schema versioned via ``PRAGMA user_version``.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from goldenmatch.identity.model import (
    EvidenceEdge,
    IdentityAlias,
    IdentityEvent,
    IdentityNode,
    IdentityStatus,
    SourceRecord,
    canon_record_pair,
)

log = logging.getLogger("goldenmatch.identity")

SCHEMA_VERSION = 2

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
    event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id    TEXT NOT NULL,
    kind         TEXT NOT NULL,
    payload      TEXT,
    run_name     TEXT,
    dataset      TEXT,
    recorded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_events_entity ON identity_events(entity_id);
CREATE INDEX IF NOT EXISTS idx_events_kind   ON identity_events(kind);
CREATE INDEX IF NOT EXISTS idx_events_run    ON identity_events(run_name);

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

    def __init__(
        self,
        backend: str = "sqlite",
        path: str = ".goldenmatch/identity.db",
        connection: str | None = None,
    ) -> None:
        self._backend = backend
        if backend == "sqlite":
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
        if version < SCHEMA_VERSION:
            self._conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

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
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE(entity_id, record_a_id, record_b_id, kind, run_name)
        );
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
            recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_events_entity ON identity_events(entity_id);
        CREATE INDEX IF NOT EXISTS idx_events_kind   ON identity_events(kind);
        CREATE INDEX IF NOT EXISTS idx_events_run    ON identity_events(run_name);
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

    def bulk_upsert_identities(self, df: Any) -> None:
        if self._backend != "postgres":
            raise NotImplementedError(
                "bulk_upsert_identities requires Postgres backend; "
                "use upsert_identity in a loop for SQLite",
            )
        if df.height == 0:
            return
        cols = [
            "entity_id", "status", "merged_into", "dataset",
            "created_at", "updated_at",
        ]
        conn: Any = self._conn
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(
                "CREATE TEMP TABLE _stage_identity_nodes "
                "(LIKE identity_nodes INCLUDING DEFAULTS) ON COMMIT DROP"
            )
            with cur.copy(
                "COPY _stage_identity_nodes "
                "(entity_id, status, merged_into, dataset, "
                "created_at, updated_at) FROM STDIN"
            ) as copy:
                for row in df.select(cols).iter_rows():
                    copy.write_row(row)
            cur.execute(
                """
                INSERT INTO identity_nodes
                    (entity_id, status, merged_into, dataset,
                     created_at, updated_at)
                SELECT entity_id, status, merged_into, dataset,
                       created_at, updated_at
                FROM _stage_identity_nodes
                ON CONFLICT (entity_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    merged_into = EXCLUDED.merged_into,
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
        row = self._fetchone(
            "SELECT * FROM identity_nodes WHERE entity_id = ?", (entity_id,)
        )
        return self._row_to_identity(row) if row else None

    def list_identities(
        self,
        dataset: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[IdentityNode]:
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
        row = self._fetchone(
            "SELECT * FROM source_records WHERE record_id = ?", (record_id,)
        )
        return self._row_to_record(row) if row else None

    def get_records_for_entity(self, entity_id: str) -> list[SourceRecord]:
        rows = self._fetchall(
            "SELECT * FROM source_records WHERE entity_id = ? ORDER BY first_seen_at",
            (entity_id,),
        )
        return [self._row_to_record(r) for r in rows]

    def find_entity_by_record(self, record_id: str) -> str | None:
        row = self._fetchone(
            "SELECT entity_id FROM source_records WHERE record_id = ?", (record_id,)
        )
        return row["entity_id"] if row else None

    def lookup_entity_ids(self, record_ids: Iterable[str]) -> dict[str, str]:
        ids = list(record_ids)
        if not ids:
            return {}
        placeholders = ",".join("?" * len(ids))
        rows = self._fetchall(
            f"SELECT record_id, entity_id FROM source_records "
            f"WHERE record_id IN ({placeholders}) AND entity_id IS NOT NULL",
            tuple(ids),
        )
        return {r["record_id"]: r["entity_id"] for r in rows}

    def add_edge(self, edge: EvidenceEdge) -> int | None:
        a, b = canon_record_pair(edge.record_a_id, edge.record_b_id)
        fs = json.dumps(edge.field_scores) if edge.field_scores else None
        ne = json.dumps(edge.negative_evidence) if edge.negative_evidence else None
        cs = json.dumps(edge.controller_snapshot) if edge.controller_snapshot else None
        self._exec(
            """
            INSERT OR IGNORE INTO evidence_edges
                (entity_id, record_a_id, record_b_id, kind, score, matchkey_name,
                 field_scores, negative_evidence, controller_snapshot, run_name,
                 dataset, recorded_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge.entity_id, a, b, edge.kind, edge.score, edge.matchkey_name,
                fs, ne, cs, edge.run_name, edge.dataset, edge.recorded_at.isoformat(),
            ),
        )
        row = self._fetchone(
            "SELECT edge_id FROM evidence_edges WHERE entity_id=? AND record_a_id=? "
            "AND record_b_id=? AND kind=? AND COALESCE(run_name,'')=COALESCE(?,'')",
            (edge.entity_id, a, b, edge.kind, edge.run_name),
        )
        return int(row["edge_id"]) if row else None

    def edges_for_entity(self, entity_id: str) -> list[EvidenceEdge]:
        rows = self._fetchall(
            "SELECT * FROM evidence_edges WHERE entity_id = ? ORDER BY recorded_at",
            (entity_id,),
        )
        return [self._row_to_edge(r) for r in rows]

    def find_conflicts(self, dataset: str | None = None) -> list[EvidenceEdge]:
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

    def emit_event(self, event: IdentityEvent) -> int | None:
        payload = json.dumps(event.payload) if event.payload is not None else None
        self._exec(
            "INSERT INTO identity_events "
            "(entity_id, kind, payload, run_name, dataset, recorded_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                event.entity_id, event.kind, payload, event.run_name,
                event.dataset, event.recorded_at.isoformat(),
            ),
        )
        row = self._fetchone(
            "SELECT MAX(event_id) AS event_id FROM identity_events WHERE entity_id = ?",
            (event.entity_id,),
        )
        return int(row["event_id"]) if row and row["event_id"] is not None else None

    def history(
        self, entity_id: str, limit: int | None = None
    ) -> list[IdentityEvent]:
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

    def has_run_event(self, entity_id: str, run_name: str, kind: str) -> bool:
        row = self._fetchone(
            "SELECT 1 AS one FROM identity_events "
            "WHERE entity_id = ? AND run_name = ? AND kind = ? LIMIT 1",
            (entity_id, run_name, kind),
        )
        return row is not None

    def add_alias(self, alias: IdentityAlias) -> None:
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
        row = self._fetchone(
            "SELECT entity_id FROM identity_aliases WHERE alias = ? AND kind = ?",
            (alias, kind),
        )
        return row["entity_id"] if row else None

    def _exec(self, sql: str, params: tuple) -> None:
        if self._backend == "sqlite":
            self._conn.execute(sql, params)
        else:
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
            recorded_at=_to_dt(row["recorded_at"]),
            event_id=row["event_id"],
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
