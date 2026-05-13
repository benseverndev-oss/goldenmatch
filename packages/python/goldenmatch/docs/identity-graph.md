---
layout: default
title: Identity Graph
nav_order: 20
---

# Identity Graph

GoldenMatch v1.15 turns the run-local cluster output of `dedupe_df()` into a **durable, queryable identity graph**. Stable `entity_id`s survive re-runs, every match has provenance, and the same answer comes back from Python, SQL, REST, MCP, A2A, and the web UI.

> **Status.** Shipped in v1.15.0 (2026-05-12). Off by default -- the zero-config posture is preserved. Enable via `config.identity.enabled = True` or an `identity:` block in YAML.

---

## What problem this solves

A plain `run_dedupe()` returns clusters whose IDs are meaningful only inside *that* result. Re-run the pipeline tomorrow with one new record and:

- Cluster IDs are different.
- There is no record of "Alice Smith merged into entity 7 because of evidence X."
- A second source pointing at the same person has no way to find the existing identity.
- The web/SQL/agent surfaces all reason from scratch each time.

The Identity Graph layer turns the cluster output into **first-class entities** that persist across runs, retain the evidence that linked them, and expose the same JSON view from every surface.

---

## Quickstart

Add an `identity:` block to your config and run dedupe normally:

```yaml
matchkeys:
  - name: people
    type: weighted
    threshold: 0.85
    fields:
      - { field: name,  scorer: jaro_winkler, weight: 0.7 }
      - { field: email, scorer: exact,        weight: 0.3 }

blocking:
  strategy: static
  keys:
    - fields: [zip]

identity:
  enabled: true
  source_pk_column: id
  dataset: customers
```

```python
import goldenmatch as gm
result = gm.dedupe("customers.csv", config="goldenmatch.yml")
print(result["identity_summary"])
# {'created': 12, 'absorbed_records': 0, 'merged': 0,
#  'split': 0, 'edges_added': 27, 'events_emitted': 12,
#  'records_upserted': 100}
```

Then resolve a record at any time:

```python
with gm.IdentityStore(path=".goldenmatch/identity.db") as store:
    view = gm.find_by_record(store, "customers:1")
    print(view.node.entity_id)            # stable UUIDv7
    print([r.record_id for r in view.records])
    print([(e.kind, e.run_name) for e in view.events])
```

---

## Storage model

Five tables. SQLite default at `.goldenmatch/identity.db`; Postgres optional.

| Table | What it stores |
|---|---|
| `identity_nodes` | One row per identity: `entity_id` (UUIDv7), status, rolled-up golden record, confidence, dataset. |
| `source_records` | One row per `{source}:{source_pk}` -- the raw observation, current owning identity, payload, first/last seen. |
| `evidence_edges` | One row per scored pair that supports an identity. Score, matchkey, per-field breakdown, NE penalties, controller telemetry, run name. |
| `identity_events` | Append-only log: `created` / `absorbed_record` / `merged_with` / `split_from` / `retired` / `manual_*`. |
| `identity_aliases` | Optional cross-source convenience lookups (e.g. `salesforce:003abc` -> entity). |

Postgres ships three analytical views: `v_identities`, `v_identity_pairs`, `v_identity_timeline`. Apply via `packages/python/goldenmatch/goldenmatch/db/migrations/identity_v1.sql` or let `IdentityStore(backend="postgres", connection=...)` create them on first connect.

---

## How resolution works

After clustering, the pipeline takes each cluster and:

1. **Look up existing identities** that already own any record in the cluster.
2. **Decide what happened:**
   - No overlap -> mint a new identity (UUIDv7), emit `created`.
   - One existing identity covers all overlapping records -> absorb the new records, emit `absorbed_record` per addition.
   - Multiple existing identities overlap -> merge them. Winner = most members (tie-break: oldest `created_at`). Emit `merged_with` on winner, retire losers with `status='merged_into', merged_into=<winner>`.
3. **Upsert** every cluster record under the chosen identity.
4. **Record evidence** -- one row in `evidence_edges` for every scored within-cluster pair, including matchkey name, per-field scores, negative-evidence penalties, and a controller-telemetry snapshot.

The result: `entity_id` is **stable across runs**. The same Alice Smith on a Tuesday run and a Wednesday run with one extra record both resolve to the same UUID -- evidence in `evidence_edges` shows which run added which edge.

Resolution is **idempotent**: replaying the same `run_name` is a no-op. Edges deduplicate on `(entity_id, record_a_id, record_b_id, run_name)`. Events deduplicate on `(run_name, kind, entity_id)`.

---

## Surfaces -- one shape, many faces

Every surface returns the same JSON (the `IdentityView.to_dict()` shape). The cross-surface contract test at `tests/identity/test_cross_surface_contract.py` enforces this byte-for-byte across all six.

### Python

```python
from goldenmatch import IdentityStore, find_by_record, get_entity, history, manual_merge

with IdentityStore(path=".goldenmatch/identity.db") as s:
    view = find_by_record(s, "crm:42")
    events = history(s, view.node.entity_id)
    manual_merge(s, keep_entity_id="...", absorb_entity_id="...", reason="dup")
```

### CLI

```bash
goldenmatch identity list --dataset customers --status active
goldenmatch identity show <entity_id>
goldenmatch identity resolve crm:42
goldenmatch identity history <entity_id>
goldenmatch identity conflicts --dataset customers
goldenmatch identity merge <keep_id> <absorb_id> --reason "dup confirmed"
goldenmatch identity split <entity_id> crm:42 crm:43 --reason "wrong merge"
```

### REST

```
GET    /api/v1/identities                            # list (paginated)
GET    /api/v1/identities/stats                      # totals
GET    /api/v1/identities/{entity_id}                # full view
GET    /api/v1/identities/{entity_id}/history        # event log
GET    /api/v1/identities/{entity_id}/evidence       # edges
GET    /api/v1/identities/by-record/{record_id}      # resolve
GET    /api/v1/identities/conflicts                  # conflict edges
POST   /api/v1/identities/{entity_id}/merge          # manual merge
POST   /api/v1/identities/{entity_id}/split          # manual split
```

### Web UI

The "Identities" tab in `goldenmatch serve-ui` lists identities with dataset/status filters, drills into one to show members + evidence + event log, and supports steward merge/split.

### MCP

Six tools on the standard MCP server (`goldenmatch mcp-serve`):

- `identity_resolve` -- look up by `record_id`
- `identity_list` -- list with filters
- `identity_view` -- full payload by `entity_id`
- `identity_history` -- event log
- `identity_conflicts` -- list `conflicts_with` edges
- `identity_merge` / `identity_split` -- steward operations

### A2A

Same six skills on the A2A agent server (`goldenmatch agent-serve`). Agent card declares 18 total skills (the 12 pre-v1.15 surface plus the six identity skills).

### SQL (Postgres + DuckDB)

The `goldenmatch-duckdb` PyPI package (>= 0.3.0) and `goldenmatch_pg` Postgres extension (>= 0.4.0) expose five read-only functions per backend:

```sql
SELECT goldenmatch_identity_resolve('crm:42', '/path/to/identity.db');
SELECT goldenmatch_identity_view('019e1f...', '/path/to/identity.db');
SELECT goldenmatch_identity_history('019e1f...', '/path/to/identity.db');
SELECT goldenmatch_identity_conflicts('customers', '/path/to/identity.db');
SELECT goldenmatch_identity_list('customers', 'active', '/path/to/identity.db');
```

All five return JSON in the same shape the Python `IdentityView.to_dict()` returns. SQL is read-only -- writes go through the Python CLI, REST endpoints, or MCP tools.

---

## "Why did these link?" -- reading the evidence

Every link decision is auditable. Pull an entity's edges:

```python
from goldenmatch import IdentityStore, get_entity

with IdentityStore(path=".goldenmatch/identity.db") as s:
    view = get_entity(s, entity_id)

for edge in view.edges:
    print(f"{edge.record_a_id} <-> {edge.record_b_id}")
    print(f"  score: {edge.score:.3f}  matchkey: {edge.matchkey_name}")
    print(f"  fields: {edge.field_scores}")
    if edge.negative_evidence:
        print(f"  negative_evidence: {edge.negative_evidence}")
    if edge.controller_snapshot:
        print(f"  autoconfig: {edge.controller_snapshot.get('stop_reason')}")
```

The same shape comes back from `GET /api/v1/identities/{eid}/evidence`, `identity_history` over MCP/A2A, and the DuckDB / Postgres `_view` functions.

---

## Configuration reference

```yaml
identity:
  enabled: false           # default off; set true to opt in
  backend: sqlite          # or "postgres"
  path: .goldenmatch/identity.db   # sqlite only
  connection: null         # postgres DSN; required when backend=postgres
  dataset: null            # namespace label that flows into every row
  source_pk_column: null   # column to derive {source}:{source_pk} record_id
                           # when null, falls back to {source}:hash:{12 hex}
                           # derived from the row payload SHA-256
  emit_singletons: true    # whether 1-record clusters become identities
```

When `source_pk_column` is unset and you have near-duplicate raw rows from the same source, two physically-different observations may collide on the same `record_id`. The recommended pattern is to always pass an explicit PK column when you can.

---

## Postgres setup

Apply the schema directly (skip if you only use SQLite):

```bash
psql -d $DB -f packages/python/goldenmatch/goldenmatch/db/migrations/identity_v1.sql
```

Or let the Python store create it on first connect:

```python
gm.IdentityStore(backend="postgres", connection="postgres://user:pass@host/db")
```

Both paths produce identical schemas. The migration script also creates three analytical views (`v_identities`, `v_identity_pairs`, `v_identity_timeline`) that the bare `IdentityStore` does not -- prefer the migration file for shared/team setups.

---

## Performance notes

- Resolve runs after clustering, before output. On a 100k-row dedupe the resolve step is dominated by SQLite write throughput (~5-15s). Postgres scales further but adds network latency.
- Resolution is **gated and additive** -- if the store fails to open, the pipeline logs a warning and continues. Identity never blocks a dedupe.
- For multi-process writers, the SQLite store uses WAL + a 5s `busy_timeout`. Postgres relies on row-level locks. Single-tenant web UI / CLI invocations are the assumed model; for high-write multi-tenant graphs use Postgres.

---

## When NOT to use it

- Single-shot ad-hoc dedupe where you only want golden records out and don't care about the next run.
- Pipelines whose source has no stable PK and whose rows are duplicated character-for-character -- the hash fallback will fold them together.

---

## Migration / backfill

Existing projects without an identity graph don't get retroactive `entity_id` stability. New runs will assign fresh UUIDs from the moment you enable identity. A best-effort backfill command that walks lineage JSONL + cluster snapshots is on the v2.1 roadmap.

---

## See also

- `examples/python/08_identity_graph.py` -- end-to-end demo (two-run stability + absorb + merge + split + conflict)
- [Pipeline architecture](architecture.md) -- where identity sits in the dedupe flow
- [Learning Memory](learning-memory.md) -- the other persistent-state layer; complementary, not competing
- [Configuration](configuration.md) -- full schema reference
