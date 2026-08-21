"""Identity Store backed by Snowflake tables.

Reached through ``IdentityStore(backend="snowflake")``; see
``docs/superpowers/specs/2026-08-20-snowflake-native-stores-design.md``.

Every write is a MERGE. Snowflake does not enforce PRIMARY KEY or UNIQUE, so a
bare INSERT would silently duplicate on a replayed run instead of being the
no-op the resolver relies on.
"""
from __future__ import annotations

import json
from collections.abc import Iterable
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
from goldenmatch.snowflake._store_sql import (
    IDENTITY_DDL,
    ensure_schema,
    execute,
    fetchall_rows,
    fetchone_row,
    merge_one,
    resolve_connection,
    stage_and_merge,  # noqa: F401 -- re-exported for future bulk_* methods
)

_NODE_UPDATE = [
    "status", "merged_into", "golden_record", "confidence", "dataset",
    "updated_at",
]
_RECORD_UPDATE = ["record_hash", "entity_id", "payload", "last_seen_at"]
_EDGE_KEY = ["entity_id", "record_a_id", "record_b_id", "kind", "run_name"]


def _dumps(value: Any) -> str | None:
    return json.dumps(value) if value else None

# SQLite/Postgres cap host parameters per statement (SQLITE_MAX_VARIABLE_NUMBER
# / similar); Snowflake's own limit differs, but chunking the IN-list here is
# harmless and keeps behaviour identical to ``IdentityStore.lookup_entity_ids``
# / ``get_identities`` (store.py:1660-1685, 1421-1433).
_CHUNK = 900


class SnowflakeIdentityStore:
    supports_bulk = True

    def __init__(
        self,
        connection: Any = None,
        *,
        database: str = "GOLDENMATCH",
        schema: str = "PUBLIC",
    ) -> None:
        self._database = database
        self._schema = schema
        self._conn = resolve_connection(
            connection, database=database, schema=schema
        )
        ensure_schema(
            self._conn, IDENTITY_DDL,
            database=database, schema=schema, version=7,
        )

    def close(self) -> None:
        self._conn.close()

    # ----- identity nodes -------------------------------------------------

    def count_nodes(self) -> int:
        """Alias of count_identities (plan compat)."""
        return self.count_identities()

    def get_node(self, entity_id: str):
        """Alias of get_identity (plan compat)."""
        return self.get_identity(entity_id)

    def upsert_identity(self, node: IdentityNode) -> None:
        merge_one(
            self._conn, "identity_nodes", ["entity_id"],
            {
                "entity_id": node.entity_id,
                "status": node.status,
                "merged_into": node.merged_into,
                "golden_record": (
                    json.dumps(node.golden_record)
                    if node.golden_record is not None else None
                ),
                "confidence": node.confidence,
                "dataset": node.dataset,
                "created_at": node.created_at.isoformat(),
                "updated_at": node.updated_at.isoformat(),
            },
            update_cols=_NODE_UPDATE,
            json_cols=["golden_record"],
        )

    def get_identity(self, entity_id: str) -> IdentityNode | None:
        from goldenmatch.identity.store import IdentityStore  # noqa: PLC0415

        row = fetchone_row(
            self._conn,
            "SELECT * FROM identity_nodes WHERE entity_id = %s",
            (entity_id,),
        )
        return IdentityStore._row_to_identity(row) if row else None

    def get_identities(
        self, entity_ids: Iterable[str]
    ) -> dict[str, IdentityNode]:
        from goldenmatch.identity.store import IdentityStore  # noqa: PLC0415

        ids = list({e for e in entity_ids if e})
        if not ids:
            return {}
        out: dict[str, IdentityNode] = {}
        for i in range(0, len(ids), _CHUNK):
            chunk = ids[i:i + _CHUNK]
            placeholders = ",".join(["%s"] * len(chunk))
            rows = fetchall_rows(
                self._conn,
                f"SELECT * FROM identity_nodes WHERE entity_id IN ({placeholders})",
                tuple(chunk),
            )
            for r in rows:
                out[r["entity_id"]] = IdentityStore._row_to_identity(r)
        return out

    def list_identities(
        self,
        dataset: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[IdentityNode]:
        from goldenmatch.identity.store import IdentityStore  # noqa: PLC0415

        clauses: list[str] = []
        params: list[Any] = []
        if dataset is not None:
            clauses.append("dataset = %s")
            params.append(dataset)
        if status is not None:
            clauses.append("status = %s")
            params.append(status)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.extend([limit, offset])
        # ORDER BY updated_at DESC matches ``IdentityStore.list_identities``
        # (store.py:1483-1487) -- the task-4 brief's SQL summary table says
        # "ORDER BY created_at", but store.py itself (the source of truth per
        # the brief's own Step 4 instructions) sorts by updated_at DESC.
        rows = fetchall_rows(
            self._conn,
            f"SELECT * FROM identity_nodes{where} "
            f"ORDER BY updated_at DESC LIMIT %s OFFSET %s",
            tuple(params),
        )
        return [IdentityStore._row_to_identity(r) for r in rows]

    def count_identities(self, dataset: str | None = None) -> int:
        if dataset is None:
            row = fetchone_row(
                self._conn, "SELECT COUNT(*) AS n FROM identity_nodes"
            )
        else:
            row = fetchone_row(
                self._conn,
                "SELECT COUNT(*) AS n FROM identity_nodes WHERE dataset = %s",
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
        execute(
            self._conn,
            "UPDATE identity_nodes SET status = %s, merged_into = %s, "
            "updated_at = CURRENT_TIMESTAMP() WHERE entity_id = %s",
            (new_status, merged_into, entity_id),
        )

    # ----- source records ---------------------------------------------

    def upsert_record(self, rec: SourceRecord) -> None:
        merge_one(
            self._conn, "source_records", ["record_id"],
            {
                "record_id": rec.record_id,
                "source": rec.source,
                "source_pk": rec.source_pk,
                "record_hash": rec.record_hash,
                "entity_id": rec.entity_id,
                "payload": (
                    json.dumps(rec.payload) if rec.payload is not None else None
                ),
                "dataset": rec.dataset,
                "first_seen_at": rec.first_seen_at.isoformat(),
                "last_seen_at": rec.last_seen_at.isoformat(),
            },
            update_cols=_RECORD_UPDATE,
            json_cols=["payload"],
        )

    def get_record(self, record_id: str) -> SourceRecord | None:
        from goldenmatch.identity.store import IdentityStore  # noqa: PLC0415

        row = fetchone_row(
            self._conn,
            "SELECT * FROM source_records WHERE record_id = %s",
            (record_id,),
        )
        return IdentityStore._row_to_record(row) if row else None

    def get_records_for_entity(self, entity_id: str) -> list[SourceRecord]:
        from goldenmatch.identity.store import IdentityStore  # noqa: PLC0415

        rows = fetchall_rows(
            self._conn,
            "SELECT * FROM source_records WHERE entity_id = %s "
            "ORDER BY first_seen_at",
            (entity_id,),
        )
        return [IdentityStore._row_to_record(r) for r in rows]

    def find_entity_by_record(self, record_id: str) -> str | None:
        row = fetchone_row(
            self._conn,
            "SELECT entity_id FROM source_records WHERE record_id = %s",
            (record_id,),
        )
        return row["entity_id"] if row else None

    def lookup_entity_ids(self, record_ids: Iterable[str]) -> dict[str, str]:
        ids = list(record_ids)
        if not ids:
            return {}
        out: dict[str, str] = {}
        for i in range(0, len(ids), _CHUNK):
            chunk = ids[i:i + _CHUNK]
            placeholders = ",".join(["%s"] * len(chunk))
            rows = fetchall_rows(
                self._conn,
                f"SELECT record_id, entity_id FROM source_records "
                f"WHERE record_id IN ({placeholders}) AND entity_id IS NOT NULL",
                tuple(chunk),
            )
            for r in rows:
                out[r["record_id"]] = r["entity_id"]
        return out

    # ----- evidence edges --------------------------------------------------

    def add_edge(self, edge: EvidenceEdge, *, return_id: bool = True) -> int | None:
        """Insert-if-absent on the five-column edge identity.

        The SQLite path relies on ``INSERT OR IGNORE`` against
        ``UNIQUE(entity_id, record_a_id, record_b_id, kind, run_name)``. That
        constraint is metadata-only in Snowflake, so the dedupe has to be the
        MERGE's own ``WHEN NOT MATCHED`` -- with no ``WHEN MATCHED`` branch, which
        is what makes it an ignore rather than an upsert.
        """
        a, b = canon_record_pair(edge.record_a_id, edge.record_b_id)
        merge_one(
            self._conn, "evidence_edges", _EDGE_KEY,
            {
                "entity_id": edge.entity_id,
                "record_a_id": a,
                "record_b_id": b,
                "kind": edge.kind,
                "score": edge.score,
                "matchkey_name": edge.matchkey_name,
                "field_scores": _dumps(edge.field_scores),
                "negative_evidence": _dumps(edge.negative_evidence),
                "controller_snapshot": _dumps(edge.controller_snapshot),
                "run_name": edge.run_name,
                "dataset": edge.dataset,
                "actor": edge.actor,
                "trust": edge.trust,
                "recorded_at": edge.recorded_at.isoformat(),
            },
            update_cols=None,  # insert-if-absent; see docstring
            json_cols=["field_scores", "negative_evidence", "controller_snapshot"],
        )
        # Fire-and-forget: the resolve_clusters write path ignores the edge_id,
        # so skip the read-back when the caller says so (matches store.py's
        # write_pipeline() batching rationale, #1912).
        if not return_id:
            return None
        row = fetchone_row(
            self._conn,
            "SELECT edge_id FROM evidence_edges WHERE entity_id = %s "
            "AND record_a_id = %s AND record_b_id = %s AND kind = %s "
            "AND COALESCE(run_name, '') = COALESCE(%s, '')",
            (edge.entity_id, a, b, edge.kind, edge.run_name),
        )
        return int(row["edge_id"]) if row else None

    def edges_for_entity(self, entity_id: str) -> list[EvidenceEdge]:
        from goldenmatch.identity.store import IdentityStore  # noqa: PLC0415

        rows = fetchall_rows(
            self._conn,
            "SELECT * FROM evidence_edges WHERE entity_id = %s ORDER BY recorded_at",
            (entity_id,),
        )
        return [IdentityStore._row_to_edge(r) for r in rows]

    def edges_by_kind(
        self, kind: str, dataset: str | None = None
    ) -> list[EvidenceEdge]:
        from goldenmatch.identity.store import IdentityStore  # noqa: PLC0415

        if dataset is None:
            rows = fetchall_rows(
                self._conn,
                "SELECT * FROM evidence_edges WHERE kind = %s "
                "ORDER BY recorded_at DESC",
                (kind,),
            )
        else:
            rows = fetchall_rows(
                self._conn,
                "SELECT * FROM evidence_edges WHERE kind = %s AND dataset = %s "
                "ORDER BY recorded_at DESC",
                (kind, dataset),
            )
        return [IdentityStore._row_to_edge(r) for r in rows]

    def find_conflicts(self, dataset: str | None = None) -> list[EvidenceEdge]:
        return self.edges_by_kind("conflicts_with", dataset)

    # ----- identity events ---------------------------------------------

    def emit_event(
        self, event: IdentityEvent, *, return_id: bool = True
    ) -> int | None:
        """Append-only log write: a plain INSERT, not a MERGE.

        ``identity_events`` has no dedupe column -- just the autoincrement
        ``event_id`` -- so there is nothing for a MERGE to key on. Replay-safety
        lives one level up, in ``resolve_clusters``'s ``has_run_event`` guard.
        """
        from goldenmatch.identity.audit import event_content_hash  # noqa: PLC0415

        payload = json.dumps(event.payload) if event.payload is not None else None
        # Tamper-evidence (#1078): stamp a per-event content hash at insert,
        # same as the SQLite/Postgres paths. Pure function of the event's own
        # fields, so it adds no read or contention on this write.
        if event.entry_hash is None:
            event.entry_hash = event_content_hash(event)
        execute(
            self._conn,
            "INSERT INTO identity_events "
            "(entity_id, kind, payload, run_name, dataset, actor, trust, "
            "claim_type, evidence_ref, previous_claim_id, entry_hash, recorded_at) "
            "VALUES (%s, %s, PARSE_JSON(%s), %s, %s, %s, %s, %s, %s, %s, %s, %s)",
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
        row = fetchone_row(
            self._conn,
            "SELECT MAX(event_id) AS event_id FROM identity_events WHERE entity_id = %s",
            (event.entity_id,),
        )
        return int(row["event_id"]) if row and row["event_id"] is not None else None

    def history(
        self, entity_id: str, limit: int | None = None
    ) -> list[IdentityEvent]:
        from goldenmatch.identity.store import IdentityStore  # noqa: PLC0415

        if limit:
            rows = fetchall_rows(
                self._conn,
                "SELECT * FROM identity_events WHERE entity_id = %s "
                "ORDER BY event_id LIMIT %s",
                (entity_id, limit),
            )
        else:
            rows = fetchall_rows(
                self._conn,
                "SELECT * FROM identity_events WHERE entity_id = %s ORDER BY event_id",
                (entity_id,),
            )
        return [IdentityStore._row_to_event(r) for r in rows]

    def has_run_event(self, entity_id: str, run_name: str, kind: str) -> bool:
        row = fetchone_row(
            self._conn,
            "SELECT 1 AS one FROM identity_events "
            "WHERE entity_id = %s AND run_name = %s AND kind = %s LIMIT 1",
            (entity_id, run_name, kind),
        )
        return row is not None

    def run_event_entities(self, run_name: str, kind: str) -> set[str]:
        rows = fetchall_rows(
            self._conn,
            "SELECT DISTINCT entity_id FROM identity_events "
            "WHERE run_name = %s AND kind = %s",
            (run_name, kind),
        )
        return {r["entity_id"] for r in rows}

    # ----- aliases -------------------------------------------------------

    def add_alias(self, alias: IdentityAlias) -> None:
        """Upsert on (alias, kind, dataset) -- the SQLite INSERT OR REPLACE."""
        merge_one(
            self._conn, "identity_aliases", ["alias", "kind", "dataset"],
            {
                "alias": alias.alias,
                "entity_id": alias.entity_id,
                "kind": alias.kind,
                "dataset": alias.dataset,
                "recorded_at": alias.recorded_at.isoformat(),
            },
            update_cols=["entity_id", "recorded_at"],
        )

    def resolve_alias(self, alias: str, kind: str = "external_id") -> str | None:
        row = fetchone_row(
            self._conn,
            "SELECT entity_id FROM identity_aliases WHERE alias = %s AND kind = %s",
            (alias, kind),
        )
        return row["entity_id"] if row else None
