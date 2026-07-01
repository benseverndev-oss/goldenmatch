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
  `MemoryStore(backend=config.memory.backend, path=config.memory.path, connection=config.memory.connection)` — so the **existing** `backend`/`path`/`connection`
  plumbing needs no change. (The two new params this spec adds — `table_prefix`
  and threading `dataset` into the learner — do require small pipeline edits; see
  §4/§5 + Files touched.)
- `get_corrections` and `count_corrections` **already** accept a `dataset`
  argument; `corrections_since` does not. The real gap is that `MemoryLearner`
  never passes a dataset to any of them (§4).
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

### 2. `psycopg` — reuse the existing `postgres` extra

**No new extra.** `pyproject.toml` already declares
`postgres = ["psycopg[binary]>=3.1", "psycopg-pool>=3.2", "alembic>=1.13"]`
(used today by `identity/store.py`). `MemoryStore` reuses it. Lazy-import
`psycopg` inside the postgres branch (mirrors the lazy `sqlite3`/`import os` in
the sqlite branch) so the base install and every non-Postgres caller are
unaffected. A clear error if the extra is missing:
`ImportError("backend='postgres' requires: pip install goldenmatch[postgres]")`.
Note: `psycopg-pool` and `alembic` are in that extra for the identity store's
sake — this feature uses **neither** (pooling and Alembic migrations are out of
scope; §6 + Out of scope). We use a single owned `psycopg` connection and
`CREATE TABLE IF NOT EXISTS` DDL on connect (matching `IdentityStore`).

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

**Interface impact + SQLite back-compat.** `save_adjustment` / `get_adjustment` /
`get_all_adjustments` gain an optional `dataset: str | None = None` parameter.
- Postgres: `dataset` is the first PK column (NULL → `''` sentinel).
- SQLite: `dataset=None` (default) behaves exactly as today (matchkey-only). The
  MVP only requires **not breaking** SQLite callers — the defaults keep every
  current call site (`learner.py`, `corrections.py`, CLI, TUI, MCP) unchanged.
- `LearnedAdjustment` gains a `dataset: str | None = None` field so reads are
  tenant-tagged. `get_all_adjustments(dataset=None)` on Postgres returns all
  rows, each `LearnedAdjustment` carrying its `dataset`; with a `dataset` it
  filters. SQLite: unchanged (field stays None).

**The learn side must be dataset-scoped too (required, not "confirm later").**
This is the crux the first review surfaced: isolating the *storage* of
adjustments is useless if `learn()` still pools corrections across tenants.
Today `MemoryLearner.learn()` calls `self._store.get_corrections()` with **no
dataset filter** and groups by `matchkey_name`; `has_new_corrections()` calls
`count_corrections()` / `corrections_since()` unfiltered; and
`pipeline._apply_memory_pre` builds `MemoryLearner(...)` + calls `learn()`
**without** threading `config.memory.dataset` (only `_apply_memory_post` →
`apply_corrections` gets `dataset=config.memory.dataset`). Required changes:
- `corrections_since` gains an optional `dataset: str | None = None` filter
  (`get_corrections` and `count_corrections` already have one). Postgres:
  `WHERE COALESCE(dataset,'')=…`; SQLite: `dataset=None` → today's unfiltered
  behavior, back-compat.
- `MemoryLearner.__init__` gains an optional `dataset: str | None = None`;
  `learn()` and `has_new_corrections()` pass it to the store reads and
  `save_adjustment(..., dataset=self._dataset)`. Default None → today's pooled
  behavior for SQLite one-file-per-dataset callers.
- `pipeline._apply_memory_pre` threads `config.memory.dataset` into
  `MemoryLearner(store, dataset=config.memory.dataset)` — mirroring how
  `_apply_memory_post` already passes `dataset=config.memory.dataset`.

Net: for a Postgres run, `learn()` reads only that tenant's corrections and
writes the adjustment under `(dataset, matchkey_name)`; no cross-tenant pooling.

### 5. Table coexistence

golden-truth's Postgres holds app tables too, so the engine's tables must be
namespaceable. Add an optional `table_prefix: str = ""` to `MemoryStore`
(e.g. `goldenmatch_corrections`, `goldenmatch_adjustments`). Default empty = bare
`corrections`/`adjustments` (matches SQLite). DDL + every query interpolate the
prefix, which is **regex-validated** (`^[A-Za-z_][A-Za-z0-9_]*$`) at construction
— never raw user input (mirrors the readers'/writers' `_safe_*_identifier`
guard).

**Config path (required — this param must reach the config-driven pipeline).**
Because golden-truth drives memory via `config.memory` → `dedupe_df` (not by
constructing `MemoryStore` directly), `table_prefix` needs a route through the
config:
- Add `table_prefix: str = ""` to `MemoryConfig` (`config/schemas.py`).
- Thread it in `pipeline._open_memory_store`:
  `MemoryStore(backend=config.memory.backend, path=config.memory.path,
  connection=config.memory.connection, table_prefix=config.memory.table_prefix)`.
- SQLite ignores a set `table_prefix` for MVP (or honors it — either is fine; the
  requirement is that Postgres can namespace and existing SQLite callers are
  unaffected since the field defaults to "").

### 6. Concurrency

Postgres handles concurrent writers natively; no WAL step. The atomic
DELETE+INSERT becomes a single `INSERT … ON CONFLICT DO UPDATE`, which is atomic
and race-safe.

## Contract (consumed by the golden-truth integration spec)

- `MemoryStore(backend="postgres", connection="postgresql://…", table_prefix="goldenmatch_")`.
- `MemoryConfig(enabled=True, backend="postgres", connection=<dsn>, dataset=<tenant/org id>, table_prefix="goldenmatch_", trust=…, learning=…, reanchor=True)` → `dedupe_df` applies + persists + learns corrections in Postgres, isolated by `dataset` (both the apply/write path and the `learn()` path).
- Corrections isolated by `dataset` (existing column); adjustments isolated by `(dataset, matchkey_name)` (new).
- Same `Correction` / `LearnedAdjustment` shapes as SQLite; `apply_corrections` /
  `MemoryLearner` unchanged (they take a store object, dialect-agnostic).

## Testing

Reuse the repo's existing DB-gated Postgres convention — **do not invent a new
env var/fixture**. `tests/_pg_helpers.py` already provides `HAS_POSTGRES`
(gated on `GOLDENMATCH_TEST_DATABASE_URL`) and the `pg_url_fixture`; the identity
store + pgvector tests use it. Skip cleanly when unset.

- **Parity:** the same `Correction` written + read back is equivalent across
  sqlite and postgres (`add_correction` → `get_pair_correction`,
  `get_corrections`, `count_corrections`, `corrections_since`).
- **Trust-wins upsert:** a lower-trust correction does not overwrite a
  higher-trust one; equal trust = latest wins — same as SQLite.
- **NULL-dataset upsert:** two writes to the same pair with `dataset=None` upsert
  (don't duplicate) via the COALESCE sentinel.
- **Corrections dataset filter:** `get_corrections(dataset="A")` /
  `count_corrections(dataset="A")` return only A's rows on Postgres; `dataset=None`
  returns all.
- **Adjustments tenant-isolation:** `save_adjustment(adj, dataset="A")` and
  `save_adjustment(adj, dataset="B")` for the same matchkey coexist;
  `get_adjustment(name, dataset=…)` returns the right one; neither overwrites the
  other; `get_all_adjustments(dataset="A")` returns only A's, each carrying
  `dataset="A"`.
- **`learn()` per-dataset isolation (the crux):** ≥`threshold_min` corrections
  for a matchkey in dataset A and a *different* set in dataset B produce two
  independent `LearnedAdjustment`s under `(A, mk)` and `(B, mk)` — `learn()` with
  a dataset does NOT pool across tenants.
- **SQLite back-compat:** every existing SQLite test still passes with the new
  optional params defaulted (no behavioral change); `MemoryLearner()` with no
  dataset behaves as today.
- **`table_prefix`:** a prefixed Postgres store creates/reads
  `goldenmatch_corrections`/`goldenmatch_adjustments`; an invalid prefix raises at
  construction.
- **Missing extra:** `backend="postgres"` without `psycopg` raises the actionable
  ImportError.

## Files touched

- `core/memory/store.py` — dialect driver + postgres branch; `dataset` param on
  `corrections_since` (new) + `save_adjustment`/`get_adjustment`/
  `get_all_adjustments` (`get_corrections`/`count_corrections` already have it);
  `table_prefix` (+ regex guard); `dataset` field on `LearnedAdjustment`.
- `core/memory/learner.py` — `MemoryLearner.__init__(dataset=…)`; thread it
  through `learn()` + `has_new_corrections()`.
- `core/pipeline.py` — `_apply_memory_pre` passes `dataset=config.memory.dataset`
  to `MemoryLearner`; `_open_memory_store` passes
  `table_prefix=config.memory.table_prefix`.
- `config/schemas.py` — `MemoryConfig.table_prefix: str = ""`.
- `pyproject.toml` — **no change** (the `postgres` extra already exists).
- `tests/` — new DB-gated `test_memory_store_postgres.py` (+ any learner test);
  reuse `tests/_pg_helpers.py`.

## Out of scope (this spec)

- golden-truth's correction-capture UI, `config.memory` wiring, and Postgres
  table provisioning — separate spec.
- Adding a `dataset` column to the SQLite `adjustments` schema (only the
  non-breaking optional param is required here).
- Connection pooling / an injected-connection constructor — MVP takes a DSN and
  owns one connection (a pool/injected-conn overload can follow).
