# goldenmatch-pg: in-database stateful identity resolution (write path)

- **Issue:** #1913
- **Status:** design (pre-implementation)
- **Related:** #1912 (per-record identity writes are round-trip-bound — an in-DB
  write path should be set-based / pipelined from the start), #1883 (table read
  is a JSON handoff, not Arrow), the Identity Graph v2 contract
  (`docs/superpowers/specs/2026-05-12-identity-graph-duckdb-contract.md`).

## 1. Problem

A multi-tenant app whose golden layer already lives in Cloud SQL Postgres wants
to run entity resolution **in-database** — maintain a durable, event-sourced
identity spine per `(tenant, entity_type)` with stable ids that survive across
runs, incremental absorb of new records, and steward merge/split. Today
`goldenmatch-pg` cannot serve as that engine, and the gap is structural:

- **The identity SQL functions are read-only and take an external `db_path`.**
  `goldenmatch_identity_resolve` / `_view` / `_history` / `_conflicts` / `_list`
  read a **SQLite file path** handed in per call. There is no SQL function that
  **writes** the identity graph, and the store they read is not the Postgres the
  extension runs in.
- **`gm_run` / `dedupe_*` are stateless.** They cluster a table fresh on each
  call and return pairs/clusters/golden into `goldenmatch._pairs` / `_clusters`
  / `_golden`. No stable entity ids, no incremental absorb, no identity events —
  re-running produces a brand-new clustering with no continuity.

So the extension does batch dedupe but cannot maintain durable golden identities
in Postgres.

## 2. Goal

Add an **in-database, stateful identity write path** to `goldenmatch-pg`:

1. A SQL path that resolves a table **into a named, Postgres-native identity
   dataset** — writing the event-sourced graph (nodes / source_records /
   evidence_edges / events) into Postgres tables via the extension's own DB, not
   an external SQLite file.
2. **Incremental** resolve: reconcile new records against the dataset's existing
   identities, preserving stable ids across runs.
3. In-SQL **manual merge / split** writes (the steward corrections path).

Non-goals (this design): distributed/Ray identity resolution (already exists on
the Python side via `distributed/identity.py`); changing the Python identity
schema or resolution semantics; the Arrow table read (#1883, folded in as an
optional later phase).

## 3. Key decision — where does the resolution logic run?

**Reuse the Python engine through the bridge. Do NOT reimplement resolution in
Rust/SQL.**

`goldenmatch.identity.resolve_clusters` + `IdentityStore(backend="postgres")`
*already implement the entire target*: stable UUIDv7 ids, create/absorb/merge
decisions from `preflight_existing` overlap, the append-only event log, evidence
edges, conflict detection, **and** (as of #1912) a set-based bulk-COPY fast path
plus psycopg-pipeline per-record writes. The only missing piece is pointing that
store at the **same Postgres the extension lives in** and exposing a write
entrypoint in SQL.

Rejected alternatives:

- **Reimplement resolution in Rust/SQL.** Enormous surface (overlap detection,
  UUIDv7, event sourcing, merge tie-breaks, conflict edges) and a permanent
  drift risk against the Python reference — the exact anti-pattern the
  cross-surface parity gates exist to prevent. No.
- **A pure-SQL/plpgsql resolver.** Same drift problem, worse ergonomics, and it
  cannot reuse the tuned blocking/scoring that produced the clusters.

The bridge already embeds CPython and calls `goldenmatch` for `gm_run`; this
design adds one more bridge entrypoint that runs `resolve_clusters` against a
Postgres-backed store.

### 3.1 The connection question (the one real subtlety)

The embedded CPython **cannot reuse the backend's SPI connection** — pyo3 and SPI
do not share a libpq connection, and `IdentityStore(backend="postgres")` opens
its own psycopg connection anyway. So the identity writes happen on a **second
libpq connection** that the bridge opens to the same database, using a DSN the
extension supplies.

Consequences, stated up front:

- **Writes are NOT in the caller's SQL transaction.** The Python store commits on
  its own connection. `gm_resolve(...)` is a batch operation, not part of a
  user's OLTP transaction, so this is acceptable — but it must be **documented**
  (a failed `gm_resolve` can leave a partially-written run; the run is
  idempotent on replay, per the existing `has_run_event` / edge-UNIQUE guards,
  so re-running converges). A future `dblink`/background-worker variant could
  tighten this; v1 does not.
- **DSN source.** A GUC `goldenmatch.identity_dsn` (superuser-set, e.g.
  `ALTER SYSTEM SET goldenmatch.identity_dsn = '...'`), falling back to the
  standard `GOLDENMATCH_DATABASE_URL` / `GOLDENMATCH_IDENTITY_DSN` env the Python
  store already understands. The extension reads the GUC and passes it to the
  bridge. Default (unset) → the write functions raise a clear "configure
  goldenmatch.identity_dsn to enable in-DB identity" error rather than silently
  falling back to SQLite.
- **Connection count.** One extra connection per `gm_resolve` call. Documented;
  callers pool/serialize resolves as needed.

## 4. Surface

### 4.1 New bridge entrypoint

`goldenmatch_bridge::api::resolve_identities(rows_json, config_json, dsn, dataset, run_name) -> summary_json`

Runs, in embedded Python:

1. `df = frame_from_rows(rows_json)` (reuse the existing JSON→frame path; Arrow
   later via #1883).
2. Resolve clusters via the same `dedupe` the config implies (or accept
   pre-scored clusters — see 4.3).
3. `store = IdentityStore(backend="postgres", connection=dsn)`.
4. `summary = resolve_clusters(clusters, df, scored_pairs, store, run_name,
   dataset, source_pk_col=<from config.identity>)`.
5. Return `summary.as_dict()` as JSON (`created` / `absorbed_records` /
   `merged` / `edges_added` / `events_emitted` / `conflicts_flagged`).

Because `resolve_clusters` reads `preflight_existing` from the store, **step 4 is
incremental for free** — re-running against the same dataset absorbs new records
into existing ids and merges overlaps, exactly the durable-spine requirement.

### 4.2 New SQL functions (pgrx `#[pg_extern]`, `goldenmatch` schema)

| Function | Purpose |
|---|---|
| `gm_resolve(job, table, dataset)` | Resolve `table` under `job`'s config into the Postgres-native identity `dataset` (create/absorb/merge, incremental). Returns the summary JSON. |
| `gm_identity_merge(dataset, entity_a, entity_b)` | Steward manual merge → `manual_merge` write + event. |
| `gm_identity_split(dataset, entity_id, record_id)` | Steward manual split → `manual_split` write + event. |

The existing **read-only** `goldenmatch_identity_*` functions gain an
**optional/absent `db_path`**: when `db_path` is NULL/empty they read the
in-DB Postgres dataset (via the same DSN) instead of a SQLite file — so the read
surface and the new write surface share one store. Existing callers that pass a
SQLite path are unchanged (back-compat).

`gm_configure`'s stored config blob carries the identity settings the resolve
needs — `identity.dataset`, `identity.source_pk_column` — reusing the
full-`GoldenMatchConfig` path landed for #1914 (`golden_rules` etc.). No new
job-table columns.

### 4.3 Reuse vs. re-score

`gm_resolve` should resolve **from the clusters `gm_run`/`dedupe` already
produce**, not re-run scoring. Two options:

- **Simple (v1):** `gm_resolve` calls the bridge which runs `dedupe` then
  `resolve_clusters` in one shot (like `gm_run`, but writing identities instead
  of `_clusters`). One SQL call, one table read.
- **Composed (later):** `gm_run` first, then `gm_resolve_from_job(job)` promotes
  the stored `_clusters` into the identity dataset without re-reading the table.
  Deferred — needs the cluster→record mapping persisted in a resolve-friendly
  shape.

v1 ships the simple path.

## 5. Schema

The identity tables (`identity_nodes`, `source_records`, `evidence_edges`,
`identity_events`, aliases) are created by `IdentityStore._pg_init_schema()` on
first connect — **the Python store already owns and migrates this schema** (the
Phase-6 Alembic baseline `0001_identity_v1`). So the extension does **not**
hand-maintain identity DDL; it lets the Python store create it in the target DB
on the first `gm_resolve`. This keeps a single schema authority (Python) and
avoids a second, drift-prone DDL copy in the pgrx SQL files.

The extension's own `goldenmatch` schema (jobs/pairs/clusters/golden) is
unchanged.

## 6. Set-based / pipelined from the start (the #1912 lesson)

The whole point of an in-DB path is to avoid the remote round-trip tax. Because
`gm_resolve` runs the Python store **inside the database host**, the store's
connection is loopback (sub-ms), so the round-trip cost #1912 fought is already
small. But we still inherit the right primitives for free:

- Brand-new datasets hit `resolve_clusters`' **bulk-COPY fast path** (4 COPYs).
- Re-resolve/absorb hits the **psycopg-pipeline** per-record path (#1912).

So the in-DB write path is set-based/pipelined **by construction** — no separate
implementation. (If a future variant runs the store against a *remote* identity
DB distinct from the data DB, #1912's pipeline still applies.)

## 7. Versioning / packaging

- New pgrx version **0.14.0**: new `#[pg_extern]`s (`gm_resolve`,
  `gm_identity_merge`, `gm_identity_split`) → new `sql/goldenmatch_pg--0.14.0.sql`
  base + `sql/goldenmatch_pg--0.13.0--0.14.0.sql` migration, bump `default_version`
  in `.control` + `version` in `Cargo.toml`, add the `cp sql/...` lines in root
  `ci.yml` + `publish-goldenmatch-pg.yml`. The `pgrx_sql_sync` gate enforces the
  Rust→SQL presence.
- `goldenmatch >= <version shipping resolve_identities-friendly frame_from_rows>`
  (already present) — no new Python dep beyond `psycopg[binary]` (already the
  identity postgres-backend requirement).
- DuckDB parity: **deferred**. DuckDB has no durable multi-connection server
  identity store shape; the identity write path is Postgres-only for now
  (documented in the extensions "deferred by design" table, alongside the
  existing "Identity writes go through the Python CLI/REST/MCP" row, which this
  narrows to "…or `gm_resolve` in Postgres").

## 8. Phasing (multi-PR)

- **P1 — write path.** Bridge `resolve_identities` + `gm_resolve` + GUC/DSN
  plumbing + the pgrx 0.14.0 SQL bump. Ships create/absorb/merge into the in-DB
  dataset. CI: `rust_pgrx` lane resolves a small table twice and asserts stable
  ids + absorb on the second run (the incremental proof).
- **P2 — read the in-DB dataset.** Make the read-only `goldenmatch_identity_*`
  functions read the Postgres dataset when `db_path` is NULL. Closes the loop:
  resolve then query in the same DB.
- **P3 — steward writes.** `gm_identity_merge` / `gm_identity_split`.
- **P4 (optional) — Arrow table read (#1883).** Replace the `row_to_json` handoff
  with an Arrow read so wide tables don't pay the serialization pass; independent
  of P1–P3.

## 9. Risks & open questions

- **Transaction isolation** (§3.1): `gm_resolve` writes commit outside the
  caller's SQL transaction. Documented; idempotent replay is the safety net. Is a
  `dblink`/bgworker variant worth it later to make it atomic with the caller?
- **DSN / auth**: the bridge connecting back to the same cluster needs
  credentials (the GUC DSN). Superuser-set GUC; documented. Same-host loopback
  keeps latency negligible.
- **Connection pressure**: one extra connection per resolve. Acceptable for a
  batch op; note it for high-frequency callers.
- **Schema ownership**: letting the Python store create identity tables in the
  target DB (rather than the pgrx SQL) is deliberate (single authority) but means
  the tables appear on first `gm_resolve`, not at `CREATE EXTENSION`. Document
  so operators aren't surprised.
- **Multi-tenant scoping**: `dataset` = `{tenant}:{entity_type}` is the scoping
  key (already how the Python store scopes). Row-level security on the identity
  tables is the app's responsibility; out of scope here.

## 10. Why this shape

It advances the North Star (be the default ER tool on every surface) by making
Postgres a **first-class resolution engine**, not just a dedupe UDF host — while
respecting the cross-surface discipline: one resolution implementation (Python),
reused through the bridge, with the SQL layer as a thin, versioned entrypoint.
No second copy of the resolver to drift.
