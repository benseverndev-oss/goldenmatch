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

from goldenmatch.identity.model import IdentityNode, IdentityStatus, SourceRecord
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
