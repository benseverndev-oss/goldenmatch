# MongoDB integration

GoldenMatch ships two MongoDB-aware pieces in Phase 1:

| Component | What it does |
|---|---|
| `goldenmatch.connectors.mongo.MongoConnector` | Read documents from a Mongo collection into a Polars DataFrame; write golden records back. |
| `goldenmatch.identity.mongo_backend.MongoIdentityStore` | Persistent Identity Graph backed by Mongo collections instead of SQLite or Postgres. |

Both depend on `pymongo`:

```bash
pip install goldenmatch[mongo]
```

For local development / CI tests, `mongomock` lets you exercise the surface without a real Mongo:

```bash
pip install goldenmatch[mongo,dev]
```

## Connector

```python
from goldenmatch.connectors import load_connector

reader = load_connector("mongo", {"credentials_env": None})

df = reader.read({
    "connection": "mongodb://localhost:27017",
    "database":   "crm",
    "collection": "contacts",
    "filter":     {"status": "active"},
    "projection": {"name": 1, "email": 1, "address.city": 1},
    "limit":      10_000,
    "flatten":    True,   # nested fields become dot-paths
}).collect()
```

`flatten=True` (default) turns `{"address": {"city": "Raleigh"}}` into a column named `address.city`. Set `flatten=False` if a downstream stage needs the nested structure.

Write-back supports three modes:

| Mode | Behavior |
|---|---|
| `append`  (default) | `insert_many` -- raw inserts. |
| `upsert`            | One `update_one(upsert=True)` per row, keyed by `config["key"]`. |
| `replace`           | `delete_many({})` + `insert_many`. |

Internal pipeline columns (`__row_id__`, `__source__`) are stripped before write by default; set `drop_internal=False` to keep them.

Connection sourcing falls back through:

1. `config["connection"]` -- passed at call time
2. `_credentials["key"]` -- if `credentials_env` is wired
3. `MONGO_URI` env var

## Identity Store

For the Identity Graph, `MongoIdentityStore` ships a standalone class with the same surface that the SQL backends expose for reads + writes. It's a sibling of `IdentityStore(backend="sqlite")` / `IdentityStore(backend="postgres")`, not a `backend=` branch on the existing class -- that unification is Phase 2 follow-up.

```python
from goldenmatch.identity.mongo_backend import MongoIdentityStore
from goldenmatch.identity.model import IdentityNode, SourceRecord
from goldenmatch.identity.store import new_entity_id

store = MongoIdentityStore(
    connection="mongodb://localhost:27017",
    database="goldenmatch",
)

eid = new_entity_id()
store.upsert_identity(IdentityNode(
    entity_id=eid, dataset="customers", status="active", confidence=0.99,
))
store.upsert_record(SourceRecord(
    record_id="salesforce:001",
    source="salesforce", source_pk="001", record_hash="h1",
    entity_id=eid, payload={"name": "Alice"}, dataset="customers",
))
store.close()
```

Use it as a context manager to auto-close the client:

```python
with MongoIdentityStore(connection="mongodb://...", database="gm") as store:
    ...
```

### Schema

One collection per logical table from the SQL backends, with the same indexes:

| Collection | Purpose | Key indexes |
|---|---|---|
| `identity_nodes` | One doc per entity | `entity_id` (unique), `dataset`, `status` |
| `source_records` | One doc per source record observed | `record_id` (unique), `entity_id`, `source`, `record_hash` |
| `evidence_edges` | Match-evidence between records | Compound unique on `(entity_id, record_a_id, record_b_id, kind, run_name)` |
| `identity_events` | Append-only event log | `entity_id`, `kind`, `run_name` |
| `identity_aliases` | External-id ↔ entity_id mapping | Compound unique on `(alias, kind, dataset)` |

Indexes are created idempotently on every store open -- no separate migration runner needed for Phase 1.

### Methods (Phase 1)

| Group | Methods |
|---|---|
| Lifecycle | `close`, `__enter__`/`__exit__` |
| Identity nodes | `upsert_identity`, `get_identity` (alias `get_node`), `list_identities`, `count_identities`, `retire_identity` |
| Source records | `upsert_record`, `get_record`, `get_records_for_entity`, `find_entity_by_record`, `lookup_entity_ids` |
| Evidence edges | `add_edge`, `edges_for_entity`, `find_conflicts` |
| Events | `emit_event`, `history`, `has_run_event` |
| Aliases | `add_alias`, `resolve_alias` |

Methods NOT in Phase 1 (and so not available through `MongoIdentityStore` yet):

- `bulk_*` writers (Postgres-only fast-path; the SQL backend uses COPY).
- `IdentityStore(backend="mongo")` dispatch -- you instantiate `MongoIdentityStore` directly.
- The Alembic migration trail (`goldenmatch identity migrate`) targets the Postgres schema; Mongo's index-create-on-open is the equivalent.

These land in Phase 2 alongside a Protocol-based dispatch refactor that lets the existing `IdentityStore` route `backend="mongo"` through `MongoIdentityStore` transparently.

## Why MongoDB?

The Snowflake adapter (PR #553) made sense because Snowflake gave goldenmatch a uniquely deep integration surface: Cortex embeddings + SPCS + dbt + Snowpark UDFs. Mongo doesn't have an analog to any of those.

What Mongo *does* fit naturally:

- **Identity Graph as a document store.** UUIDv7 entity_ids, nested payloads, fast point lookups, append-only event collections -- Mongo's strengths line up cleanly with the Identity Graph schema.
- **Source-data connector.** Many CRM / e-commerce / IoT workloads land in Mongo. Reading them into the Polars-based ER pipeline is a real workflow today.

What ships in Phase 1 (this PR) is precisely those two pieces. Phase 2 candidates (not in this PR):

- Atlas Vector Search as an ANN blocker via `goldenmatch.db.ann_index`.
- `IdentityStore(backend="mongo")` dispatch (Protocol refactor in `goldenmatch.identity.store`).
- Mongo-as-MemoryStore-backend for Learning Memory writes.

None of those have a built-in Mongo audience the way Snowpark / Cortex did for Snowflake, so they're flagged for follow-up rather than rolled into Phase 1.
