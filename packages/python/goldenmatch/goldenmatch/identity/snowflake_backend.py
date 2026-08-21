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

# Module-level, not lazy: ``store.py`` imports THIS module inside
# ``IdentityStore.__init__`` (store.py:360-362), so the dependency runs one way
# and there is no cycle. Importing SCHEMA_VERSION rather than restating 7 keeps
# the Snowflake DDL's stamped version from silently desyncing on a schema bump.
from goldenmatch.identity.store import _SAFE_FIELD, SCHEMA_VERSION
from goldenmatch.snowflake._store_sql import (
    IDENTITY_DDL,
    ensure_schema,
    execute,
    fetchall_rows,
    fetchone_row,
    merge_one,
    resolve_connection,
    stage_and_merge,
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
            database=database, schema=schema, version=SCHEMA_VERSION,
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

    # ----- relationship value transforms ---------------------------------

    def _rel_expr(self, field: str, transform: str | None) -> str:
        """Snowflake port of ``store._rel_value_expr`` (store.py:59-96).

        Same FIXED transform vocabulary, same NULL-on-no-match semantics; only
        the string functions change. ``field`` is ``_SAFE_FIELD``-validated by
        the caller before it reaches the ``payload:<field>`` path expression.
        """
        raw = f"payload:{field}::string"
        t = (transform or "raw").lower()
        if t == "raw":
            return raw
        if t == "lower_trim":
            return f"lower(trim({raw}))"
        if t == "zip3":
            return f"substr({raw}, 1, 3)"
        if t == "email_domain":
            # Snowflake has SPLIT_PART, so this follows the postgres branch: a
            # value with no '@' yields '' -> NULLIF -> NULL, rather than the
            # whole string (which would wrongly relate everyone missing an '@').
            return f"lower(nullif(split_part({raw}, '@', 2), ''))"
        if t == "normalize_company":
            # Snowflake HAS regexp_replace (all occurrences by default), so
            # unlike sqlite this does not degrade to lower_trim. Backslashes are
            # doubled because Snowflake unescapes them inside a string literal.
            return (
                f"nullif(trim(regexp_replace(lower(trim({raw})), "
                r"'[\\s,\\.]+(inc|llc|ltd|corp|co|company|pllc|pc|pa|group"
                r"|assoc|associates)\\.{0,1}$'"
                ", '')), '')"
            )
        raise ValueError(f"unknown relationship transform: {transform!r}")

    # ----- deterministic merge on a shared field --------------------------

    def merge_by_shared_field(
        self, dataset: str | None, field: str | list[str], max_group: int = 100,
    ) -> tuple[int, int]:
        """Port of ``IdentityStore.merge_by_shared_field`` (store.py:1539-1646).

        The union-find and the survivor rule are the source's, lifted into
        ``_union_find_remap``; only the value extraction (``json_extract`` ->
        ``payload:<f>::string``), the composite separator (``char(31)`` ->
        ``CHR(31)``) and the two UPDATE statements are dialect-translated.
        """
        fields = [field] if isinstance(field, str) else list(field)
        if not fields:
            raise ValueError("merge_by_shared_field: no fields given")
        for f in fields:
            if not _SAFE_FIELD.fullmatch(f):
                raise ValueError(f"unsafe merge field name: {f!r}")
        parts = [f"payload:{f}::string" for f in fields]
        if len(parts) == 1:
            vexpr = parts[0]
            not_null = f"{vexpr} IS NOT NULL AND {vexpr} <> ''"
        else:
            # Composite value: concatenate with a unit-separator so distinct
            # tuples cannot collide. Every column must be present (guarded).
            vexpr = " || CHR(31) || ".join(parts)
            not_null = " AND ".join(
                f"{p} IS NOT NULL AND {p} <> ''" for p in parts
            )
        ds = "" if dataset is None else " AND dataset = %s"
        params: tuple = () if dataset is None else (dataset,)
        rows = fetchall_rows(
            self._conn,
            f"WITH ev AS (SELECT DISTINCT {vexpr} AS v, entity_id "
            f" FROM source_records"
            f" WHERE entity_id IS NOT NULL{ds} AND {not_null}), "
            "grp AS (SELECT v FROM ev GROUP BY v "
            "         HAVING COUNT(*) >= 2 AND COUNT(*) <= %s) "
            "SELECT ev.v AS v, ev.entity_id AS e FROM ev JOIN grp ON ev.v = grp.v",
            params + (max_group,),
        )
        if not rows:
            return (0, 0)
        by_val: dict[str, list[str]] = {}
        for r in rows:
            by_val.setdefault(r["v"], []).append(r["e"])
        remap, groups = _union_find_remap(by_val)
        if not remap:
            return (0, 0)
        ts = datetime.now().isoformat()
        merged_status = IdentityStatus.MERGED_INTO.value
        rec_upd = [(new, old) for old, new in remap]
        node_upd = [(merged_status, new, ts, old) for old, new in remap]

        def _body(cur: Any) -> None:
            cur.executemany(
                "UPDATE source_records SET entity_id = %s WHERE entity_id = %s",
                rec_upd,
            )
            cur.executemany(
                "UPDATE identity_nodes SET status = %s, merged_into = %s, "
                "updated_at = %s WHERE entity_id = %s",
                node_upd,
            )

        self._in_transaction(_body)
        return (len(remap), groups)

    def _in_transaction(self, body: Any) -> None:
        """Run ``body(cursor)`` inside one explicit transaction.

        Snowflake sessions autocommit, so the multi-statement write paths the
        SQLite/Postgres backends wrap in a transaction need an explicit BEGIN
        here or a mid-way failure leaves a half-applied remap.
        """
        execute(self._conn, "BEGIN")
        try:
            with self._conn.cursor() as cur:
                body(cur)
            execute(self._conn, "COMMIT")
        except BaseException:
            try:
                execute(self._conn, "ROLLBACK")
            except Exception:  # noqa: BLE001 -- surface the original failure
                pass
            raise

    # ----- persisted blocking index ---------------------------------------

    def index_record_block_keys(
        self,
        record_id: str,
        entity_id: str | None,
        keys: Iterable[tuple[str, str]],
    ) -> None:
        """Persist the ``(pass_sig, block_key)`` pairs a record falls in.

        Idempotent per ``(record_id, pass_sig, block_key)`` and re-points
        ``entity_id`` on replay -- the SQLite ``ON CONFLICT ... DO UPDATE SET
        entity_id`` (store.py:1739-1789), i.e. ``update_cols=["entity_id"]``.
        """
        rows = [
            {
                "record_id": record_id,
                "entity_id": entity_id,
                "block_key": str(bk),
                "pass_sig": str(ps),
            }
            for ps, bk in keys
            if bk is not None
        ]
        for row in rows:
            merge_one(
                self._conn, "identity_record_block_keys",
                ["record_id", "pass_sig", "block_key"], row,
                update_cols=["entity_id"],
            )

    def candidates_by_block_keys(
        self, keys: Iterable[tuple[str, str]]
    ) -> set[str]:
        pairs = [(str(ps), str(bk)) for ps, bk in keys if bk is not None]
        if not pairs:
            return set()
        out: set[str] = set()
        # Two host params per pair; chunked to match store.py:1809-1811.
        chunk_size = 450
        for i in range(0, len(pairs), chunk_size):
            chunk = pairs[i:i + chunk_size]
            clause = " OR ".join(
                "(pass_sig = %s AND block_key = %s)" for _ in chunk
            )
            flat = [v for pair in chunk for v in pair]
            rows = fetchall_rows(
                self._conn,
                "SELECT DISTINCT record_id FROM identity_record_block_keys "
                f"WHERE {clause}",
                tuple(flat),
            )
            for r in rows:
                out.add(r["record_id"])
        return out

    # ----- summary stats ---------------------------------------------------

    def status_counts(self, dataset: str | None = None) -> dict[str, int]:
        if dataset is None:
            rows = fetchall_rows(
                self._conn,
                "SELECT status, COUNT(*) AS n FROM identity_nodes GROUP BY status",
            )
        else:
            rows = fetchall_rows(
                self._conn,
                "SELECT status, COUNT(*) AS n FROM identity_nodes "
                "WHERE dataset = %s GROUP BY status",
                (dataset,),
            )
        return {r["status"]: int(r["n"]) for r in rows}

    def active_record_stats(
        self, dataset: str | None = None,
    ) -> tuple[dict[str, int], dict[str, int]]:
        ds = "" if dataset is None else " AND n.dataset = %s"
        params: tuple = () if dataset is None else (dataset,)
        per_entity_rows = fetchall_rows(
            self._conn,
            "SELECT n.entity_id AS eid, COUNT(sr.record_id) AS n "
            "FROM identity_nodes n "
            "LEFT JOIN source_records sr ON sr.entity_id = n.entity_id "
            f"WHERE n.status = 'active'{ds} "
            "GROUP BY n.entity_id",
            params,
        )
        source_rows = fetchall_rows(
            self._conn,
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

    # ----- semantic graph: relationship discovery -------------------------

    def relationship_groups(
        self, field: str, dataset: str | None,
        min_entities: int, max_entities: int,
        transform: str | None = None,
    ) -> list[tuple[str, list[str]]]:
        if not _SAFE_FIELD.fullmatch(field):
            raise ValueError(f"unsafe relationship field name: {field!r}")
        vexpr = self._rel_expr(field, transform)
        ds = "" if dataset is None else " AND dataset = %s"
        params: tuple = () if dataset is None else (dataset,)
        # LISTAGG is Snowflake's group_concat/string_agg; the pairs CTE has
        # already deduped (v, entity_id), so no DISTINCT inside it. There is no
        # _ensure_relationship_index equivalent -- Snowflake has no secondary
        # indexes, micro-partition pruning is the engine's own business.
        sql = (
            "WITH ex AS ("
            f" SELECT {vexpr} AS v, entity_id FROM source_records"
            f" WHERE entity_id IS NOT NULL{ds}"
            "), pairs AS ("
            " SELECT DISTINCT v, entity_id FROM ex WHERE v IS NOT NULL AND v <> ''"
            "), qual AS ("
            " SELECT v FROM pairs GROUP BY v HAVING COUNT(*) >= %s AND COUNT(*) <= %s"
            ") "
            "SELECT p.v AS v, LISTAGG(p.entity_id, ',') AS eids "
            "FROM pairs p JOIN qual q ON p.v = q.v GROUP BY p.v"
        )
        rows = fetchall_rows(
            self._conn, sql, params + (min_entities, max_entities)
        )
        return [(r["v"], str(r["eids"]).split(",")) for r in rows]

    def sample_records(
        self, dataset: str | None, limit: int,
    ) -> list[tuple[str, dict]]:
        ds = "" if dataset is None else " AND dataset = %s"
        params: tuple = () if dataset is None else (dataset,)
        rows = fetchall_rows(
            self._conn,
            "SELECT entity_id, payload FROM source_records "
            f"WHERE entity_id IS NOT NULL{ds} LIMIT %s",
            params + (int(limit),),
        )
        out: list[tuple[str, dict]] = []
        for r in rows:
            p = r["payload"]
            # A VARIANT column arrives as its JSON text over the wire.
            if isinstance(p, str):
                try:
                    p = json.loads(p)
                except (ValueError, TypeError):
                    continue
            if isinstance(p, dict):
                out.append((r["entity_id"], p))
        return out

    def relationship_field_stats(
        self, field: str, dataset: str | None,
        min_entities: int, max_entities: int, transform: str | None = None,
    ) -> dict[str, int]:
        if not _SAFE_FIELD.fullmatch(field):
            raise ValueError(f"unsafe relationship field name: {field!r}")
        vexpr = self._rel_expr(field, transform)
        ds = "" if dataset is None else " AND dataset = %s"
        params: tuple = () if dataset is None else (dataset,)
        sql = (
            "WITH ex AS ("
            f" SELECT {vexpr} AS v, entity_id FROM source_records"
            f" WHERE entity_id IS NOT NULL{ds}"
            "), pairs AS ("
            " SELECT DISTINCT v, entity_id FROM ex WHERE v IS NOT NULL AND v <> ''"
            "), card AS ("
            " SELECT v, COUNT(*) AS n FROM pairs GROUP BY v"
            ") "
            "SELECT "
            "(SELECT COUNT(*) FROM card WHERE n >= %s AND n <= %s) AS sweet_values, "
            "(SELECT COUNT(*) FROM card WHERE n > %s) AS hub_values, "
            "(SELECT COUNT(DISTINCT p.entity_id) FROM pairs p JOIN card c "
            " ON p.v = c.v WHERE c.n >= %s AND c.n <= %s) AS coverage_entities, "
            "(SELECT COALESCE(SUM(n*(n-1)/2), 0) FROM card "
            " WHERE n >= %s AND n <= %s) AS sweet_pairs, "
            "(SELECT COALESCE(SUM(n*(n-1)/2*n), 0) FROM card "
            " WHERE n >= %s AND n <= %s) AS sweet_pair_n"
        )
        rows = fetchall_rows(
            self._conn, sql,
            params + (min_entities, max_entities, max_entities,
                      min_entities, max_entities, min_entities,
                      max_entities, min_entities, max_entities),
        )
        r = rows[0] if rows else None
        if r is None:
            return {"sweet_values": 0, "hub_values": 0, "coverage_entities": 0,
                    "sweet_pairs": 0, "sweet_pair_n": 0}
        return {
            "sweet_values": int(r["sweet_values"] or 0),
            "hub_values": int(r["hub_values"] or 0),
            "coverage_entities": int(r["coverage_entities"] or 0),
            "sweet_pairs": int(r["sweet_pairs"] or 0),
            "sweet_pair_n": int(r["sweet_pair_n"] or 0),
        }

    # ----- semantic graph: relationship edges -----------------------------

    def add_relationships(self, rows: list[tuple]) -> int:
        norm = _canon_relationships(rows)
        if not norm:
            return 0
        # SQLite's INSERT OR IGNORE also collapses duplicates WITHIN the batch.
        # A MERGE would insert both copies of a repeated key -- with no WHEN
        # MATCHED branch there is nothing to catch the second -- so dedupe
        # before staging. The return value stays the PRE-dedupe count, matching
        # store.py's "rows attempted" contract (store.py:2192).
        self._merge_relationships(_dedupe_relationships(norm))
        return len(norm)

    def _merge_relationships(self, norm: list[tuple]) -> None:
        if not norm:
            return
        stage_and_merge(
            self._conn, "identity_relationships",
            [
                {
                    "entity_a_id": a, "entity_b_id": b, "kind": kind,
                    "field": fld, "shared_value": val, "dataset": ds,
                }
                for a, b, kind, fld, val, ds in norm
            ],
            _REL_KEY,
            update_cols=None,  # insert-if-absent: the INSERT OR IGNORE
            database=self._database, schema=self._schema,
        )

    def reconcile_relationships(
        self, dataset: str | None, kind: str, desired: Iterable[tuple],
    ) -> tuple[int, int, int]:
        want: dict[tuple, tuple] = {}
        for a, b, k, fld, val, _ds in desired:
            if a is None or b is None or a == b:
                continue
            lo, hi = (a, b) if a < b else (b, a)
            want[(lo, hi, k, val)] = (lo, hi, k, fld, val, dataset)
        # ``dataset = %s``, NOT a NULL-safe comparison, on purpose: the SQLite
        # and Postgres paths bind the same ``dataset = ?`` (store.py:2233-2240),
        # so a dataset=None call matches nothing there. NULL-safe equality here
        # would make this backend delete rows the other two leave alone.
        existing = {
            (r["entity_a_id"], r["entity_b_id"], r["kind"], r["shared_value"])
            for r in fetchall_rows(
                self._conn,
                "SELECT entity_a_id, entity_b_id, kind, shared_value "
                "FROM identity_relationships WHERE dataset = %s AND kind = %s",
                (dataset, kind),
            )
        }
        want_keys = set(want)
        ins_keys = want_keys - existing
        del_keys = existing - want_keys
        unchanged = len(want_keys & existing)
        if not ins_keys and not del_keys:
            return (0, 0, unchanged)
        ins_rows = [want[k] for k in ins_keys]
        # del key = (a, b, kind, value); DELETE binds (dataset, a, b, kind, value).
        del_rows = [(dataset, a, b, k, v) for (a, b, k, v) in del_keys]
        del_sql = (
            "DELETE FROM identity_relationships WHERE dataset = %s "
            "AND entity_a_id = %s AND entity_b_id = %s AND kind = %s "
            "AND shared_value IS NOT DISTINCT FROM %s"
        )
        # NOT wrapped in an explicit transaction, unlike the SQLite and Postgres
        # paths (store.py:2250-2278). The insert half goes through
        # ``stage_and_merge``, whose ``CREATE TRANSIENT TABLE`` is DDL -- and
        # Snowflake commits the open transaction implicitly at every DDL
        # statement. A BEGIN around this would therefore be a lie: it would read
        # as all-or-nothing while the stage creation silently ended the
        # transaction mid-way. (fakesnow refuses it outright -- a MERGE inside
        # BEGIN aborts the duckdb transaction -- which is how this surfaced.)
        #
        # So the guarantee here is convergence, not atomicity, and the ORDER is
        # what buys it: insert first, delete second. A failure between the two
        # leaves a SUPERSET (stale edges still present, no edge missing), and
        # the next reconcile -- which recomputes ``desired`` from the current
        # entity ids and is idempotent by construction -- removes them. The
        # reverse order would leave a gap instead, which reads as "these
        # entities are unrelated" until the next run.
        self._merge_relationships(ins_rows)
        if del_rows:
            with self._conn.cursor() as cur:
                cur.executemany(del_sql, del_rows)
        return (len(ins_keys), len(del_keys), unchanged)

    def get_relationships(self, entity_id: str) -> list[dict[str, Any]]:
        rows = fetchall_rows(
            self._conn,
            "SELECT entity_a_id, entity_b_id, kind, field, shared_value "
            "FROM identity_relationships "
            "WHERE entity_a_id = %s OR entity_b_id = %s",
            (entity_id, entity_id),
        )
        out = []
        for r in rows:
            other = (
                r["entity_b_id"] if r["entity_a_id"] == entity_id
                else r["entity_a_id"]
            )
            out.append({"other_entity_id": other, "kind": r["kind"],
                        "field": r["field"], "shared_value": r["shared_value"]})
        return out

    def count_relationships(self) -> int:
        row = fetchone_row(
            self._conn, "SELECT COUNT(*) AS n FROM identity_relationships"
        )
        return int(row["n"]) if row else 0

    def list_relationships(
        self, dataset: str | None = None,
    ) -> list[dict[str, Any]]:
        where = "" if dataset is None else " WHERE dataset = %s"
        params: tuple = () if dataset is None else (dataset,)
        rows = fetchall_rows(
            self._conn,
            "SELECT entity_a_id, entity_b_id, kind, field, shared_value "
            f"FROM identity_relationships{where}",
            params,
        )
        return [
            {"entity_a_id": r["entity_a_id"], "entity_b_id": r["entity_b_id"],
             "kind": r["kind"], "field": r["field"],
             "shared_value": r["shared_value"]}
            for r in rows
        ]

    # ----- run lineage ----------------------------------------------------

    def record_run(
        self,
        run_name: str,
        *,
        config_id: str | None = None,
        schema_version: int | None = None,
        config_json: str | None = None,
        dataset: str | None = None,
    ) -> None:
        """First writer wins on a repeated run_name -- the SQLite ``ON CONFLICT
        (run_name) DO NOTHING`` (store.py:2386), i.e. ``update_cols=None``."""
        if not run_name:
            return
        merge_one(
            self._conn, "identity_runs", ["run_name"],
            {
                "run_name": run_name,
                "config_id": config_id,
                "schema_version": schema_version,
                "config_json": config_json,
                "dataset": dataset,
            },
            update_cols=None,
        )

    def run_config(self, run_name: str) -> dict[str, Any] | None:
        row = fetchone_row(
            self._conn,
            "SELECT run_name, config_id, schema_version, config_json, dataset, "
            "created_at FROM identity_runs WHERE run_name = %s",
            (run_name,),
        )
        # CaseInsensitiveRow is a Mapping, so dict() yields the lowercase keys
        # the SQLite/Postgres callers already index.
        return dict(row) if row else None

    # ----- audit export ---------------------------------------------------

    def export_audit_log(
        self, *, dataset: str | None = None, actor: str | None = None,
        since: datetime | None = None,
    ) -> list[IdentityEvent]:
        from goldenmatch.identity.store import IdentityStore  # noqa: PLC0415

        clauses: list[str] = []
        params: list[Any] = []
        if dataset is not None:
            clauses.append("dataset = %s")
            params.append(dataset)
        if actor is not None:
            clauses.append("actor = %s")
            params.append(actor)
        if since is not None:
            clauses.append("recorded_at >= %s")
            params.append(since.isoformat())
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = fetchall_rows(
            self._conn,
            f"SELECT * FROM identity_events{where} ORDER BY event_id",
            tuple(params),
        )
        return [IdentityStore._row_to_event(r) for r in rows]

    # ----- audit seal chain ------------------------------------------------

    def add_seal(self, seal: AuditSeal) -> int | None:
        """Append-only log write: a plain INSERT, not a MERGE.

        ``audit_seals`` is a chain, not a keyed table -- two identical seals are
        two legitimate rows, and the only key is the autoincrement ``seal_id``.
        MERGE-ing here would silently swallow a re-seal.
        """
        execute(
            self._conn,
            "INSERT INTO audit_seals "
            "(dataset, root_hash, event_count, last_event_id, prev_seal_id, "
            "prev_root, actor, created_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                seal.dataset, seal.root_hash, seal.event_count,
                seal.last_event_id, seal.prev_seal_id, seal.prev_root,
                seal.actor, seal.created_at.isoformat(),
            ),
        )
        row = fetchone_row(
            self._conn, "SELECT MAX(seal_id) AS seal_id FROM audit_seals"
        )
        return int(row["seal_id"]) if row and row["seal_id"] is not None else None

    def latest_seal(self, *, dataset: str | None = None) -> AuditSeal | None:
        from goldenmatch.identity.store import IdentityStore  # noqa: PLC0415

        if dataset is None:
            row = fetchone_row(
                self._conn,
                "SELECT * FROM audit_seals WHERE dataset IS NULL "
                "ORDER BY seal_id DESC LIMIT 1",
            )
        else:
            row = fetchone_row(
                self._conn,
                "SELECT * FROM audit_seals WHERE dataset = %s "
                "ORDER BY seal_id DESC LIMIT 1",
                (dataset,),
            )
        return IdentityStore._row_to_seal(row) if row else None

    def list_seals(self, *, dataset: str | None = None) -> list[AuditSeal]:
        from goldenmatch.identity.store import IdentityStore  # noqa: PLC0415

        if dataset is None:
            rows = fetchall_rows(
                self._conn,
                "SELECT * FROM audit_seals WHERE dataset IS NULL ORDER BY seal_id",
            )
        else:
            rows = fetchall_rows(
                self._conn,
                "SELECT * FROM audit_seals WHERE dataset = %s ORDER BY seal_id",
                (dataset,),
            )
        return [IdentityStore._row_to_seal(r) for r in rows]


_REL_KEY = ["entity_a_id", "entity_b_id", "kind", "shared_value"]


def _canon_relationships(rows: list[tuple]) -> list[tuple]:
    """Drop self-edges and canonicalize each endpoint pair to ``a < b``.

    Lifted from ``IdentityStore.add_relationships`` (store.py:2171-2180).
    """
    norm: list[tuple] = []
    for a, b, kind, fld, val, dataset in rows or []:
        if a == b:
            continue
        lo, hi = (a, b) if a < b else (b, a)
        norm.append((lo, hi, kind, fld, val, dataset))
    return norm


def _dedupe_relationships(norm: list[tuple]) -> list[tuple]:
    """Collapse rows repeating an edge key, keeping the first occurrence."""
    seen: set[tuple] = set()
    out: list[tuple] = []
    for row in norm:
        key = (row[0], row[1], row[2], row[4])
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def _union_find_remap(
    by_val: dict[str, list[str]],
) -> tuple[list[tuple[str, str]], int]:
    """Component-collapse the entity ids sharing a value.

    Lifted from ``IdentityStore.merge_by_shared_field`` (store.py:1601-1620):
    an entity may share two ids and chain, and the survivor is the
    lexicographically smallest entity id in the component. Returns
    ``([(absorbed, survivor), ...], group_count)``.
    """
    parent: dict[str, str] = {}

    def _find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def _union(a: str, b: str) -> None:
        ra, rb = _find(a), _find(b)
        if ra != rb:
            lo, hi = (ra, rb) if ra < rb else (rb, ra)
            parent[hi] = lo

    for ents in by_val.values():
        for e in ents[1:]:
            _union(ents[0], e)
    allents = {e for ents in by_val.values() for e in ents}
    remap = [(e, _find(e)) for e in allents if _find(e) != e]
    if not remap:
        return ([], 0)
    return (remap, len({_find(e) for e in allents}))
