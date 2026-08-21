# Snowflake-native stores and Phase 2 handlers

Issue: [#2699](https://github.com/benseverndev-oss/goldenmatch/issues/2699)
Date: 2026-08-20
Status: design approved, plan pending

## Problem

`IdentityConfig.backend` accepts `sqlite`, `postgres` and `mongo`. None is
durable when goldenmatch runs *inside* Snowflake: the only writable filesystem
in a Python UDF / UDTF / stored-procedure sandbox is the worker's `/tmp`, which
dies with the worker.

#2699 establishes that everything around the store already works --
`goldenmatch=3.13.1 NATIVE=ON` runs in a Snowflake sandbox on aarch64, and
`auto_configure_df` + identity-enabled `dedupe_df` produce a complete store
in-sandbox. What is missing is somewhere durable to put it.

The issue's own workaround -- round-tripping the SQLite file's tables through
`write_pandas(..., overwrite=True)` -- is rejected here for the four reasons it
gives: it rewrites the whole store every run, serialises through pandas and
SQLite on one worker, is lossy by construction (everything cast to string), and
reimplements persistence outside the store, so the store's invariants hold only
as well as the round-trip preserves them.

## Scope

Four pieces, in one spec at the maintainer's direction. Piece 1 is the only one
with no upstream dependency; 2-4 are independent of it and of each other.

1. **Snowflake `IdentityStore` backend** -- what #2699 asks for.
2. **Snowflake `MemoryStore` backend** -- unblocks `correction_add`.
3. **`scan_table` / `health_score`** -- Snowpark read into the existing
   `goldencheck.engine.scanner.scan_dataframe`.
4. **`DedupeFull` / `DedupeClusters` / `DedupePairs` + a registration CLI** --
   the dedupe stored procedures, plus the `goldenmatch snowflake init` path the
   docs already claim exists.

Explicitly out of scope: changing `sqlite` / `postgres` / `mongo` behaviour;
lifting the vectorized-UDTF single-worker ceiling; any throughput claim for
piece 4 (see Provisional).

## Corrections to existing documentation

Found while scoping; all are stale claims that would mislead the next reader.
Fixing them is part of this work.

| Location | Claim | Reality |
|---|---|---|
| `snowflake/udfs.py` module docstring | Phase 2 ships "once the Snowflake-native `MemoryStore` and `IdentityStore` backends land" | Only `correction_add` depends on a store, and on `MemoryStore`, not `IdentityStore`. The other five do not. |
| `scan_table` docstring | blocked because `scan_file` "currently expects a file path" | `scanner.scan_dataframe` exists (`scanner.py:245`) and takes a frame. |
| `udfs.py` module docstring | "The `goldenmatch snowflake init` CLI registers UDFs" | `cli/snowflake.py` does not exist. The dbt macros do. |
| 3 scaffold docstrings | "Tracking issue: `docs/snowflake-handlers.md`" | Path is `packages/dbt/goldensuite/docs/snowflake-handlers.md`. |
| `snowflake-handlers.md` | "PR #553 shipped the outside" (naming `cli/snowflake.py`) | The macros shipped; the CLI did not. |
| `IdentityConfig.backend` field description | "('sqlite' or 'postgres')" | Omits `mongo`; will omit `snowflake`. |

## Architecture

### Module layout

| File | Change |
|---|---|
| `goldenmatch/snowflake/_store_sql.py` | **new** -- shared plumbing for both stores |
| `goldenmatch/identity/snowflake_backend.py` | **new** -- `SnowflakeIdentityStore` |
| `goldenmatch/core/memory/snowflake_backend.py` | **new** -- `SnowflakeMemoryStore` |
| `goldenmatch/identity/store.py` | construction branch, ~52 dispatch branches, `supports_bulk` |
| `goldenmatch/core/memory/store.py` | construction branch, 12 dispatch branches |
| `goldenmatch/identity/resolve.py` | bulk gate becomes a capability check |
| `goldenmatch/snowflake/udfs.py` | six Phase 2 implementations; docstring fixes |
| `goldenmatch/cli/snowflake.py` | **new** -- `goldenmatch snowflake init` |
| `goldenmatch/config/schemas.py` | `IdentityConfig.connection` widened, `schema` added |
| `packages/dbt/goldensuite/spcs/server.py` | Session-from-env for the six handlers |

The delegation pattern is `mongo_backend.py`'s, verbatim: a per-method
`if self._backend == "snowflake": return self._sf.<method>(...)` early return,
with the backend object built in `__init__` and the SQL paths untouched.

Rejected alternative: a `_sf_sql()` dialect translator extending
`_exec`/`_fetchone`/`_fetchall`. It requires rewriting `ON CONFLICT DO UPDATE`
and `INSERT OR IGNORE` into `MERGE` by string surgery across a 2663-line file
with 52 methods and 9 tables. The seam is in the wrong place.

Rejected alternative: routing Snowflake through the existing SPCS container to a
Postgres store. It works and is cheap, but the store is then not
Snowflake-native, so warehouse governance and time travel -- the issue's stated
payoff -- do not apply.

### `_store_sql.py` responsibilities

- **Connection resolution.** Accepts a Snowpark `Session` (uses its public
  `.connection`), a live `SnowflakeConnection`, a dict of connector kwargs, or an
  account string with the `SNOWFLAKE_*` env fallback that
  `db/connector_snowflake.py` already implements. Inside a stored procedure the
  caller passes the `Session` it was handed.
- **Case-insensitive row wrapper.** Snowflake uppercases unquoted identifiers, so
  `DictCursor` yields `{'ENTITY_ID': ...}` while every `_row_to_*` helper indexes
  `row["entity_id"]`. The wrapper reconciles this. Quoting identifiers in the DDL
  would also work but leaves anyone hand-querying the tables typing
  `SELECT "entity_id"`, which is not a thing to ship.
- **`ensure_schema()`** -- idempotent DDL plus a `_gm_schema_version` row.
- **`merge_one()`** -- single-row upsert / insert-if-absent as a `MERGE`.
- **`stage_and_merge()`** -- the bulk path: `CREATE OR REPLACE TRANSIENT TABLE
  <stage> LIKE <target>` then `pandas_tools.write_pandas` then `MERGE` then
  `DROP`.

**Config schema.** `IdentityConfig.connection` is typed `str | None` today and
documented as "Database connection string used when the backend is postgres". It
widens to `str | dict | None` so connector kwargs can be expressed in config, and
gains `schema: str = "GOLDENMATCH"`. A Snowpark `Session` is always passed
programmatically, never through config -- it is not serialisable.

Parameters use the connector's default `pyformat`. Because these are new modules
writing native SQL, they do not inherit the existing `?` placeholders, so nothing
depends on setting a paramstyle on a connection we may not own.

## Schema

Nine tables mirroring `_SCHEMA` in `identity/store.py`: `identity_nodes`,
`source_records`, `evidence_edges`, `identity_events`, `audit_seals`,
`identity_aliases`, `identity_record_block_keys`, `identity_relationships`,
`identity_runs`. Plus MemoryStore's `corrections` and `adjustments`.

Type mapping: `VARIANT` for `golden_record` / `payload` / `field_scores` /
`negative_evidence` / `controller_snapshot`; `TIMESTAMP_NTZ` for timestamps;
`NUMBER AUTOINCREMENT START 1 INCREMENT 1` for `edge_id` / `event_id`, which
preserves the integer-id semantics callers rely on.

`PRAGMA user_version` has no analogue; the schema version (currently 7) lives in
a `_gm_schema_version` table.

### The correctness trap: Snowflake does not enforce constraints

`PRIMARY KEY`, `UNIQUE` and `FOREIGN KEY` are metadata only in Snowflake;
`NOT NULL` is the sole enforced constraint. Today's idempotency rests on
constraints being enforced:

- `add_edge` uses `INSERT OR IGNORE` against
  `UNIQUE(entity_id, record_a_id, record_b_id, kind, run_name)`
- `add_alias` uses `INSERT OR REPLACE` against the alias primary key
- `upsert_identity` / `upsert_record` use `ON CONFLICT(...) DO UPDATE`
- `source_records.entity_id` references `identity_nodes` with
  `ON DELETE SET NULL`

Ported naively, every one of these silently duplicates rows on Snowflake, and a
replayed run corrupts the graph rather than being a no-op. All four become
explicit `MERGE`: `WHEN NOT MATCHED THEN INSERT` for the ignore/insert cases,
`WHEN MATCHED THEN UPDATE` for the upserts, and an explicit statement in
`retire_identity` for the FK cascade.

This is the single highest-risk item in the spec and gets a dedicated
idempotency-under-replay test.

## Write paths

Singleton writes are **immediate** -- one `MERGE` per call, so visibility
semantics are identical to sqlite and postgres and no caller learns a new mental
model. No buffered-delta write mode is introduced. Throughput comes from the
existing bulk contract, not from changing when writes become visible.

`bulk_upsert_identities` / `bulk_upsert_records` / `bulk_add_edges` /
`bulk_emit_events` mirror the Postgres shape one-for-one, substituting
`write_pandas` for `COPY` and `MERGE` for `INSERT ... ON CONFLICT`.

Stage tables carry a `uuid4` suffix. Two concurrent writers sharing a fixed stage
name is the same collision #2699 hit with fixed temp dirs for the `.so` extract.

### Existing batching seams

| Seam | Snowflake behaviour |
|---|---|
| `bulk_writes()` | Real: `BEGIN`/`COMMIT` around the batch. Snowflake autocommits per statement otherwise. |
| `write_pipeline()` | No-op (psycopg-specific), as for Mongo/SQLite. |
| `bulk_copy_barrier()` | No-op -- there is no pipeline to suspend. |
| `bulk_flush_checkpoint()` | Follows the SQLite/Mongo path. |
| `initial_load_writes()` | Does not engage in v1. Noted as a later optimization: on empty tables the `MERGE` can degrade to a bare `write_pandas`. |

### Capability gate

`resolve.py:718` currently reads:

```python
_bulk_backend = getattr(store, "_backend", None)
use_bulk_fast_path = (
    _bulk_backend in ("postgres", "sqlite") and _bulk_fast_path_enabled()
)
```

This becomes a `supports_bulk` property check, so the fast path is a declared
capability rather than a hard-coded backend-name allowlist. Without it a
Snowflake store takes the per-row path and every write is a warehouse
round-trip -- exactly the failure the issue is filed about.

## Phase 2 handlers

All six take a Snowpark `Session` as their first argument. Stored procedures
receive one implicitly; the current scaffolds do not declare it, which is free to
change since they only raise. `packages/dbt/goldensuite/spcs/server.py`
delegates to these same functions, so one implementation lights up both the
stored-procedure surface and the SPCS container surface.

| Handler | Implementation |
|---|---|
| `correction_add` | `MemoryStore(backend="snowflake", connection=session)` then `add_correction`, returning the UUID7 |
| `scan_table` | `session.table(relation)` to a frame, then `scan_dataframe`, returning JSON findings |
| `health_score` | same read, then `DatasetProfile.health_score` |
| `DedupeFull` / `DedupeClusters` / `DedupePairs` | read relation, `dedupe_df`, rows back |

The dedupe handlers ship as **stored procedures, not vectorized UDTFs**:
single-node by construction, with the ceiling documented rather than papered
over. `over (partition by 1)` would send the whole frame to one worker anyway,
so a UDTF would buy the appearance of distribution without the fact of it.

`cli/snowflake.py` supplies the missing registration half: stage upload plus
`CREATE FUNCTION` / `CREATE PROCEDURE` DDL, with #2699's packaging recipe baked
into the handler bootstrap -- `manylinux_2_28_aarch64` wheels for
`goldenmatch-native` and `goldenphonetic`, extraction to a **unique** temp dir at
module scope, and a `sys.path` prepend. The unique-dir detail is load-bearing: a
fixed path lets a second worker `dlopen` a half-written `.so` and take a bus
error on an mmap of a truncated file.

## Testing

`fakesnow` (DuckDB-backed, patches `snowflake.connector`) joins the `dev` extra.
A probe run on 0.11.13 confirmed the constructs this design depends on: DDL with
`VARIANT` and `PRIMARY KEY`, `AUTOINCREMENT`, `MERGE INTO` with both `WHEN`
branches, `write_pandas`, `executemany`, `DictCursor`, and both paramstyles.

Two fakesnow quirks the implementation must respect:

1. `write_pandas` into a session `TEMP` table fails -- it resolves
   `main.<TABLE>` while the session sits on `<DB>.<SCHEMA>`. Hence a named
   transient stage table, which is the better Snowflake choice regardless.
2. `write_pandas` must be reached as `pandas_tools.write_pandas(...)`. A
   `from`-import binds the real function before `fakesnow.patch()` runs, and it
   then fails against the fake connection.

Suites under `tests/snowflake/`:

- **CRUD coverage** across all 52 identity methods and 12 memory methods.
- **Idempotency under replay** -- the constraint trap above. Replaying a run must
  not duplicate edges, aliases, nodes or records.
- **Bulk-vs-singleton equivalence** -- the same input through both paths yields
  identical rows.
- **Cross-backend parity** -- one fixture through sqlite and through
  snowflake-on-fakesnow, asserting identical `IdentityNode` / `SourceRecord` /
  `EvidenceEdge` / `IdentityEvent` objects. This is the load-bearing guardrail
  against semantic drift, mirroring `test_cross_surface_contract.py`.
- **Dispatch coverage** -- every public store method has a `snowflake` branch,
  by introspection, mirroring `test_mongo_dispatch.py`.

A `GOLDENMATCH_SNOWFLAKE_TEST_DSN` env var runs the same suites against live
Snowflake. Skipped by default; no CI job depends on it.

## Documentation

Fix the six stale claims tabulated above, then the standard rollout sweep: the
docs site, `CHANGELOG.md`, README, the generated config matrix (`IdentityConfig`
gains a backend value and a field), and a codemap regen -- new Python symbols
otherwise turn two checks red.

## Provisional

Stated plainly rather than smoothed over.

- **The dedupe stored procedures ship with no throughput claim.** #2699's own
  scope note stands: the single-worker UDTF ceiling is unmeasured, and it
  interacts with where the store lives. Nobody should promise numbers until
  someone measures.
- **`write_pandas` at large row counts inside a UDF sandbox is untested.** The
  sandbox's memory ceiling and the staging round-trip are both unknowns.
- **fakesnow is a DuckDB fake, and the three things it is most likely to lie
  about are the three this design leans on**: `MERGE` semantics, `VARIANT`
  round-tripping, and constraint non-enforcement. DuckDB *does* enforce primary
  keys, so a duplicate-insert bug can pass on the fake and fail on Snowflake --
  or, worse, the reverse, where a `MERGE` that fakesnow accepts is rejected by
  the real optimizer. Until the live-gated suite is run, "works" means "works on
  fakesnow".
- **`initial_load_writes` does not engage.** Not a regression; the from-empty
  fast path simply has no Snowflake implementation in v1.
