# Postgres backend for MemoryStore — design spec

_Date: 2026-07-01_
_Status: approved (brainstorm), pre-implementation_
_Repo: goldenmatch (monorepo) — `packages/python/goldenmatch`_

## Summary

Add a **Postgres backend** to `MemoryStore` (Learning Memory persistence) so a
run can read/write corrections + adjustments in a shared Postgres database,
multi-tenant, isolated by `dataset`. Today `MemoryStore` is SQLite-only
(`backend != "sqlite"` → `NotImplementedError`), which forces a local file per
store — unusable from a stateless, multi-tenant web backend (golden-truth) where
state must live in Postgres.

After this change:
`MemoryStore(backend="postgres", connection="postgresql://…")` works, and
`config.memory = MemoryConfig(enabled=True, backend="postgres", connection=<dsn>,
dataset=<tenant>)` makes `dedupe_df` apply + persist corrections natively — no
SQLite, no per-tenant file.

This spec is the **engine half** of the Learning Memory feature. The consumer
(golden-truth: correction capture + `config.memory` wiring + UI) is a separate
spec that depends on the contract at the bottom of this doc.

## Current state (verified against `main`)

- `core/memory/store.py`: `MemoryStore(backend="sqlite", path=".goldenmatch/memory.db", connection=None)`. `__init__` handles `sqlite`; else raises `NotImplementedError`. `connection` is an unused placeholder.
- `_SCHEMA` — two tables:
  - `corrections (id PK, id_a, id_b, decision, source, trust, field_hash, record_hash, original_score, matchkey_name, reason, dataset, created_at, field_name, original_value, corrected_value, cluster_score, cluster_outcome, UNIQUE(id_a, id_b, dataset))`.
  - `adjustments (matchkey_name TEXT PRIMARY KEY, threshold, field_weights, sample_size, learned_at)` — **keyed by matchkey_name alone**.
- ~15 methods, all `self._conn.execute(...)` with `?` placeholders. `add_correction` upserts via DELETE+INSERT in a transaction (trust-wins).
- `core/pipeline.py` already constructs the store as
  `MemoryStore(backend=config.memory.backend, path=config.memory.path, connection=config.memory.connection)` — **so the run path needs no change** once the backend exists.
- `MemoryConfig` fields already present: `enabled, backend, path, connection, trust, learning, reanchor, dataset`.

## Design

### 1. Dialect driver (keep one public class)

Refactor `MemoryStore` so SQL-dialect specifics live behind a small internal
driver selected in `__init__`:

- `_SqliteDriver` — wraps the existing `sqlite3` connection; `?` placeholders;
  DELETE+INSERT upsert; `executescript` DDL. Preserves today's behavior exactly.
- `_PostgresDriver` — wraps a `psycopg` (v3) connection; `%s` placeholders;
  `INSERT … ON CONFLICT … DO UPDATE` upsert; executes DDL statements individually.

The ~15 method bodies stay shared and dialect-neutral by going through the
driver for: `execute(sql, params) -> rows`, `executemany`, `commit`/transaction
context, the parameter placeholder, and the upsert idiom. Rationale: branching
`self._backend` inside every method, or forking two full subclasses, both
duplicate the query logic; a thin driver keeps one class + one copy of each
query shaped by the driver's placeholder/upsert. (This is a targeted refactor of
an existing file that has grown a second backend's worth of responsibility —
in-scope, not gratuitous.)

### 2. `psycopg` as an optional extra

Add `psycopg[binary]` (v3) under an optional extra `postgres`
(`goldenmatch[postgres]`). Lazy-import inside the postgres branch (mirrors the
lazy `sqlite3`/`import os` in the sqlite branch) so the base install and every
non-Postgres caller are unaffected. A clear error if the extra is missing:
`ImportError("backend='postgres' requires: pip install goldenmatch[postgres]")`.

### 3. Postgres schema

Mirror the two tables with Postgres types, created idempotently
(`CREATE TABLE IF NOT EXISTS`) on connect:

- `corrections`: `id TEXT PRIMARY KEY, id_a BIGINT, id_b BIGINT, decision TEXT,
  source TEXT, trust DOUBLE PRECISION, field_hash TEXT, record_hash TEXT,
  original_score DOUBLE PRECISION, matchkey_name TEXT, reason TEXT, dataset TEXT,
  created_at TIMESTAMPTZ DEFAULT now(), field_name TEXT, original_value TEXT,
  corrected_value TEXT, cluster_score DOUBLE PRECISION, cluster_outcome TEXT`.
- Upsert on the natural key. SQLite uses `UNIQUE(id_a, id_b, dataset)` and
  matches NULL dataset via `IS ?`. Postgres treats `NULL` as distinct in a
  unique constraint, so ON CONFLICT can't target a NULL dataset directly. Use a
  **`COALESCE(dataset, '')` sentinel in a unique index** and target it:
  `CREATE UNIQUE INDEX … ON corrections (id_a, id_b, COALESCE(dataset, ''))`;
  `INSERT … ON CONFLICT (id_a, id_b, COALESCE(dataset, '')) DO UPDATE SET …`.
  Keep the trust-wins semantics: `DO UPDATE … WHERE EXCLUDED.trust >= corrections.trust`.
- `adjustments`: **PK `(dataset, matchkey_name)`** (composite) —
  `dataset TEXT NOT NULL DEFAULT '', matchkey_name TEXT, threshold DOUBLE
  PRECISION, field_weights TEXT (JSON), sample_size INTEGER, learned_at TIMESTAMPTZ,
  PRIMARY KEY (dataset, matchkey_name)`.

### 4. Adjustments scoped by `(dataset, matchkey_name)` — the key decision

The SQLite schema keys `adjustments` by `matchkey_name` alone. That is correct
for the one-file-per-dataset SQLite model, but in a **shared Postgres** serving
many tenants it collides: tenant B's `learn()` overwrites tenant A's learned
threshold for the same matchkey. So the Postgres backend scopes adjustments by
`(dataset, matchkey_name)`, and `save_adjustment` / `get_adjustment` /
`get_all_adjustments` accept/filter a `dataset`.

**Interface impact + SQLite back-compat:** these three methods gain an optional
`dataset: str | None = None` parameter.
- Postgres: uses it as the first PK column (NULL → `''` sentinel).
- SQLite: when `dataset` is None (the default), behaves exactly as today
  (matchkey-only). Optionally, SQLite may also honor a passed `dataset` by adding
  a `dataset` column + composite key behind a schema migration, but the MVP only
  requires **not breaking** existing SQLite callers — the added param defaults
  keep every current call site (`learner.py`, `corrections.py`, CLI, TUI, MCP)
  working unchanged.
- `MemoryLearner.learn()` (which calls `save_adjustment`) passes the `dataset`
  it learned over when one is in scope; today it learns per-matchkey, so it
  threads the store's active dataset through. Confirm the learner's call sites in
  implementation and thread `dataset` where the store is Postgres.

### 5. Table coexistence

golden-truth's Postgres holds app tables too. Add an optional
`table_prefix: str = ""` (or `schema`) to `MemoryStore` so the Postgres tables
can be namespaced (e.g. `goldenmatch_corrections`, `goldenmatch_adjustments`).
Default empty = bare `corrections`/`adjustments` (matches SQLite). golden-truth
sets a prefix/schema. DDL + every query interpolate the (validated,
regex-guarded) prefix — never user input.

### 6. Concurrency

Postgres handles concurrent writers natively; no WAL step. The atomic
DELETE+INSERT becomes a single `INSERT … ON CONFLICT DO UPDATE`, which is atomic
and race-safe.

## Contract (consumed by the golden-truth integration spec)

- `MemoryStore(backend="postgres", connection="postgresql://…", table_prefix="goldenmatch_")`.
- `MemoryConfig(enabled=True, backend="postgres", connection=<dsn>, dataset=<tenant/org id>, trust=…, learning=…, reanchor=True)` → `dedupe_df` applies + persists corrections in Postgres, isolated by `dataset`.
- Corrections isolated by `dataset` (existing column); adjustments isolated by `(dataset, matchkey_name)` (new).
- Same `Correction` / `LearnedAdjustment` shapes as SQLite; `apply_corrections` /
  `MemoryLearner` unchanged (they take a store object, dialect-agnostic).

## Testing

DB-gated (skip cleanly when no test Postgres DSN is set, mirroring the repo's
DB-gated test convention). Use a `GOLDENMATCH_TEST_PG_DSN` env var (or a
testcontainer if the suite already has one).

- **Parity:** the same `Correction` written + read back is byte-identical across
  sqlite and postgres (`add_correction` → `get_pair_correction`,
  `get_corrections`, `count_corrections`, `corrections_since`).
- **Trust-wins upsert:** a lower-trust correction does not overwrite a
  higher-trust one; equal trust = latest wins — same as SQLite.
- **NULL-dataset upsert:** two writes to the same pair with `dataset=None` upsert
  (don't duplicate) via the COALESCE sentinel.
- **Adjustments tenant-isolation:** `save_adjustment(dataset="A")` and
  `save_adjustment(dataset="B")` for the same matchkey coexist; `get_adjustment`
  returns the right one per dataset; neither overwrites the other.
- **SQLite back-compat:** every existing SQLite test still passes with the new
  optional `dataset` param defaulted (no behavioral change).
- **Missing extra:** `backend="postgres"` without `psycopg` raises the actionable
  ImportError.
- **`learn()` round-trip on Postgres:** ≥`threshold_min` corrections for a
  matchkey in dataset A produce a `LearnedAdjustment` stored under `(A, matchkey)`.

## Out of scope (this spec)

- golden-truth's correction-capture UI, `config.memory` wiring, and Postgres
  table provisioning — separate spec.
- Adding a `dataset` column to the SQLite `adjustments` schema (only the
  non-breaking optional param is required here).
- Connection pooling / an injected-connection constructor — MVP takes a DSN and
  owns one connection (a pool/injected-conn overload can follow).
