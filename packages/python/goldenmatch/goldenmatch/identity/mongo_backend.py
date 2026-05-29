"""MongoDB-backed Identity Store.

Phase 1: a standalone ``MongoIdentityStore`` class with the same public
surface as the read/write side of ``IdentityStore`` for the methods the
core pipeline + REST + MCP surfaces actually use.

Why standalone instead of an ``IdentityStore(backend="mongo")``
branch: the existing SQLite + Postgres backends are deeply inlined in
``store.py`` (~600 lines of SQL strings and per-method branches).
Squeezing Mongo into that pattern would either duplicate every method
or require a big refactor. Standalone here keeps the change scoped;
unifying through ``IdentityStore`` is a Phase 2 follow-up that can
land via a Protocol-based dispatch without disturbing existing
callers.

Surface implemented in Phase 1:

  - Identity nodes: ``upsert_identity``, ``get_identity``,
    ``list_identities``, ``count_identities``, ``retire_identity``
  - Source records: ``upsert_record``, ``get_record``,
    ``get_records_for_entity``, ``find_entity_by_record``,
    ``lookup_entity_ids``
  - Evidence edges: ``add_edge``, ``edges_for_entity``,
    ``find_conflicts``
  - Events: ``emit_event``, ``history``, ``has_run_event``
  - Aliases: ``add_alias``, ``resolve_alias``
  - Lifecycle: ``close``, ``__enter__`` / ``__exit__``

Schema -- one collection per logical table from the SQL backends:

  - ``identity_nodes``  (entity_id PRIMARY)
  - ``source_records``  (record_id PRIMARY)
  - ``evidence_edges``  (entity_id + record_a_id + record_b_id +
                          kind + run_name UNIQUE compound)
  - ``identity_events`` (event_id auto, entity_id indexed)
  - ``identity_aliases`` (alias + kind + dataset PRIMARY compound)

Indexes mirror the Postgres definitions byte-for-byte. The unique
indexes are CREATEd with ``unique=True`` so duplicate ``upsert_*``
calls collapse to the single-document update path.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from goldenmatch.identity.model import (
    EvidenceEdge,
    IdentityAlias,
    IdentityEvent,
    IdentityNode,
    SourceRecord,
)

log = logging.getLogger(__name__)


# Collection names mirror the SQL table names.
_NODES = "identity_nodes"
_RECORDS = "source_records"
_EDGES = "evidence_edges"
_EVENTS = "identity_events"
_ALIASES = "identity_aliases"


class MongoIdentityStore:
    """Identity Store backed by MongoDB.

    Typically constructed with a MongoDB URI:

        store = MongoIdentityStore(
            connection="mongodb://localhost:27017",
            database="goldenmatch",
        )

    Or with an existing ``pymongo.MongoClient``:

        client = pymongo.MongoClient(...)
        store = MongoIdentityStore(client=client, database="gm")
    """

    def __init__(
        self,
        connection: str | None = None,
        database: str = "goldenmatch",
        *,
        client: Any = None,
    ) -> None:
        if client is None:
            uri = connection or os.environ.get("MONGO_URI")
            if not uri:
                raise ValueError(
                    "MongoIdentityStore needs a connection URI; pass "
                    "connection=, set MONGO_URI, or pass client="
                )
            try:
                import pymongo  # type: ignore[import-not-found]
            except ImportError as exc:
                raise ImportError(
                    "MongoIdentityStore requires pymongo: "
                    "pip install goldenmatch[mongo]"
                ) from exc
            client = pymongo.MongoClient(uri)
            self._owns_client = True
        else:
            self._owns_client = False
        self._client = client
        self._db = client[database]
        self._init_indexes()

    # ----- lifecycle ---------------------------------------------------

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> MongoIdentityStore:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # ----- one-time index setup ---------------------------------------

    def _init_indexes(self) -> None:
        """Create the indexes that mirror the SQL DDL on first connect.

        ``create_index`` is idempotent; running this on every store
        open keeps the surface in lockstep with the schema without a
        separate migration runner.
        """
        # identity_nodes
        self._db[_NODES].create_index("entity_id", unique=True)
        self._db[_NODES].create_index("dataset")
        self._db[_NODES].create_index("status")
        # source_records
        self._db[_RECORDS].create_index("record_id", unique=True)
        self._db[_RECORDS].create_index("entity_id")
        self._db[_RECORDS].create_index("source")
        self._db[_RECORDS].create_index("record_hash")
        # evidence_edges
        self._db[_EDGES].create_index(
            [
                ("entity_id", 1),
                ("record_a_id", 1),
                ("record_b_id", 1),
                ("kind", 1),
                ("run_name", 1),
            ],
            unique=True,
            name="edges_unique",
        )
        self._db[_EDGES].create_index("entity_id")
        self._db[_EDGES].create_index([("record_a_id", 1), ("record_b_id", 1)])
        self._db[_EDGES].create_index("run_name")
        # identity_events
        self._db[_EVENTS].create_index("entity_id")
        self._db[_EVENTS].create_index("kind")
        self._db[_EVENTS].create_index("run_name")
        # identity_aliases
        self._db[_ALIASES].create_index(
            [("alias", 1), ("kind", 1), ("dataset", 1)],
            unique=True,
            name="aliases_unique",
        )
        self._db[_ALIASES].create_index("entity_id")

    # ----- identity nodes ---------------------------------------------

    def upsert_identity(self, node: IdentityNode) -> None:
        doc = {
            "entity_id": node.entity_id,
            "status": node.status,
            "merged_into": node.merged_into,
            "golden_record": node.golden_record,
            "confidence": node.confidence,
            "dataset": node.dataset,
            "updated_at": datetime.now(),
        }
        self._db[_NODES].update_one(
            {"entity_id": node.entity_id},
            {
                "$set": doc,
                "$setOnInsert": {"created_at": node.created_at},
            },
            upsert=True,
        )

    def get_identity(self, entity_id: str) -> IdentityNode | None:
        d = self._db[_NODES].find_one({"entity_id": entity_id})
        return _to_node(d) if d else None

    # Alias used by the query layer.
    def get_node(self, entity_id: str) -> IdentityNode | None:
        return self.get_identity(entity_id)

    def list_identities(
        self,
        dataset: str | None = None,
        status: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[IdentityNode]:
        q: dict[str, Any] = {}
        if dataset is not None:
            q["dataset"] = dataset
        if status is not None:
            q["status"] = status
        cur = self._db[_NODES].find(q).sort("updated_at", -1).skip(offset).limit(limit)
        return [_to_node(d) for d in cur]

    def count_identities(self, dataset: str | None = None) -> int:
        q: dict[str, Any] = {}
        if dataset is not None:
            q["dataset"] = dataset
        return self._db[_NODES].count_documents(q)

    def retire_identity(
        self,
        entity_id: str,
        merged_into: str | None = None,
        status: str = "retired",
    ) -> None:
        self._db[_NODES].update_one(
            {"entity_id": entity_id},
            {
                "$set": {
                    "status": status,
                    "merged_into": merged_into,
                    "updated_at": datetime.now(),
                },
            },
        )

    # ----- source records ---------------------------------------------

    def upsert_record(self, rec: SourceRecord) -> None:
        self._db[_RECORDS].update_one(
            {"record_id": rec.record_id},
            {
                "$set": {
                    "source": rec.source,
                    "source_pk": rec.source_pk,
                    "record_hash": rec.record_hash,
                    "entity_id": rec.entity_id,
                    "payload": rec.payload,
                    "dataset": rec.dataset,
                    "last_seen_at": datetime.now(),
                },
                "$setOnInsert": {"first_seen_at": rec.first_seen_at},
            },
            upsert=True,
        )

    def get_record(self, record_id: str) -> SourceRecord | None:
        d = self._db[_RECORDS].find_one({"record_id": record_id})
        return _to_record(d) if d else None

    def get_records_for_entity(self, entity_id: str) -> list[SourceRecord]:
        cur = self._db[_RECORDS].find({"entity_id": entity_id})
        return [_to_record(d) for d in cur]

    def find_entity_by_record(self, record_id: str) -> str | None:
        d = self._db[_RECORDS].find_one(
            {"record_id": record_id}, {"entity_id": 1},
        )
        return d.get("entity_id") if d else None

    def lookup_entity_ids(self, record_ids: Iterable[str]) -> dict[str, str]:
        ids = list(record_ids)
        if not ids:
            return {}
        cur = self._db[_RECORDS].find(
            {"record_id": {"$in": ids}},
            {"record_id": 1, "entity_id": 1},
        )
        return {d["record_id"]: d["entity_id"] for d in cur if d.get("entity_id")}

    # ----- evidence edges ---------------------------------------------

    def add_edge(self, edge: EvidenceEdge) -> int | None:
        """Insert an evidence edge. Mongo's upsert returns the
        post-update doc's ``_id`` -- we surface it as the edge_id so
        the SQL signature stays compatible. Replay-safe via the
        compound unique index."""
        doc = {
            "entity_id": edge.entity_id,
            "record_a_id": edge.record_a_id,
            "record_b_id": edge.record_b_id,
            "kind": edge.kind,
            "score": edge.score,
            "matchkey_name": edge.matchkey_name,
            "field_scores": edge.field_scores,
            "negative_evidence": edge.negative_evidence,
            "controller_snapshot": edge.controller_snapshot,
            "run_name": edge.run_name,
            "dataset": edge.dataset,
            "recorded_at": edge.recorded_at,
        }
        result = self._db[_EDGES].update_one(
            {
                "entity_id": edge.entity_id,
                "record_a_id": edge.record_a_id,
                "record_b_id": edge.record_b_id,
                "kind": edge.kind,
                "run_name": edge.run_name,
            },
            {"$setOnInsert": doc},
            upsert=True,
        )
        # upserted_id is the BSON ObjectId on insert, None on hit.
        return _objectid_to_int(result.upserted_id) if result.upserted_id else None

    def edges_for_entity(self, entity_id: str) -> list[EvidenceEdge]:
        cur = self._db[_EDGES].find({"entity_id": entity_id}).sort("recorded_at", -1)
        return [_to_edge(d) for d in cur]

    def find_conflicts(self, dataset: str | None = None) -> list[EvidenceEdge]:
        q: dict[str, Any] = {"kind": "conflicts_with"}
        if dataset is not None:
            q["dataset"] = dataset
        cur = self._db[_EDGES].find(q).sort("recorded_at", -1)
        return [_to_edge(d) for d in cur]

    # ----- events ------------------------------------------------------

    def emit_event(self, event: IdentityEvent) -> int | None:
        doc = {
            "entity_id": event.entity_id,
            "kind": event.kind,
            "payload": event.payload,
            "run_name": event.run_name,
            "dataset": event.dataset,
            "recorded_at": event.recorded_at,
        }
        result = self._db[_EVENTS].insert_one(doc)
        return _objectid_to_int(result.inserted_id)

    def history(
        self, entity_id: str, limit: int | None = None,
    ) -> list[IdentityEvent]:
        cur = self._db[_EVENTS].find({"entity_id": entity_id}).sort(
            "recorded_at", 1,
        )
        if limit is not None:
            cur = cur.limit(int(limit))
        return [_to_event(d) for d in cur]

    def has_run_event(self, entity_id: str, run_name: str, kind: str) -> bool:
        return self._db[_EVENTS].count_documents(
            {"entity_id": entity_id, "run_name": run_name, "kind": kind},
            limit=1,
        ) > 0

    # ----- aliases -----------------------------------------------------

    def add_alias(self, alias: IdentityAlias) -> None:
        self._db[_ALIASES].update_one(
            {
                "alias": alias.alias,
                "kind": alias.kind,
                "dataset": alias.dataset,
            },
            {
                "$set": {
                    "entity_id": alias.entity_id,
                    "recorded_at": alias.recorded_at,
                },
            },
            upsert=True,
        )

    def resolve_alias(
        self, alias: str, kind: str = "external_id",
    ) -> str | None:
        d = self._db[_ALIASES].find_one(
            {"alias": alias, "kind": kind}, {"entity_id": 1},
        )
        return d.get("entity_id") if d else None


# ---------------------------------------------------------------------------
# Doc <-> model conversion helpers.
# ---------------------------------------------------------------------------


def _to_node(d: dict[str, Any]) -> IdentityNode:
    return IdentityNode(
        entity_id=d["entity_id"],
        status=d.get("status", "active"),
        merged_into=d.get("merged_into"),
        golden_record=d.get("golden_record"),
        confidence=d.get("confidence"),
        dataset=d.get("dataset"),
        created_at=d.get("created_at") or datetime.now(),
        updated_at=d.get("updated_at") or datetime.now(),
    )


def _to_record(d: dict[str, Any]) -> SourceRecord:
    return SourceRecord(
        record_id=d["record_id"],
        source=d["source"],
        source_pk=d["source_pk"],
        record_hash=d["record_hash"],
        entity_id=d.get("entity_id"),
        payload=d.get("payload"),
        dataset=d.get("dataset"),
        first_seen_at=d.get("first_seen_at") or datetime.now(),
        last_seen_at=d.get("last_seen_at") or datetime.now(),
    )


def _to_edge(d: dict[str, Any]) -> EvidenceEdge:
    return EvidenceEdge(
        entity_id=d["entity_id"],
        record_a_id=d["record_a_id"],
        record_b_id=d["record_b_id"],
        kind=d.get("kind", "same_as"),
        score=d.get("score"),
        matchkey_name=d.get("matchkey_name"),
        field_scores=d.get("field_scores"),
        negative_evidence=d.get("negative_evidence"),
        controller_snapshot=d.get("controller_snapshot"),
        run_name=d.get("run_name"),
        dataset=d.get("dataset"),
        recorded_at=d.get("recorded_at") or datetime.now(),
        edge_id=_objectid_to_int(d.get("_id")) if d.get("_id") else None,
    )


def _to_event(d: dict[str, Any]) -> IdentityEvent:
    return IdentityEvent(
        entity_id=d["entity_id"],
        kind=d["kind"],
        payload=d.get("payload"),
        run_name=d.get("run_name"),
        dataset=d.get("dataset"),
        recorded_at=d.get("recorded_at") or datetime.now(),
        event_id=_objectid_to_int(d.get("_id")) if d.get("_id") else None,
    )


def _objectid_to_int(oid: Any) -> int:
    """The SQL backends return ``int`` ids; Mongo's ObjectId is
    12 bytes. Pack into an ``int`` so existing callers see the same
    shape. Comparisons stay stable as long as the ObjectId is the
    source of truth."""
    if oid is None:
        return 0
    if isinstance(oid, int):
        return oid
    s = str(oid)
    try:
        return int(s, 16)
    except ValueError:
        return hash(s) & 0x7FFFFFFFFFFFFFFF


__all__ = ["MongoIdentityStore"]
