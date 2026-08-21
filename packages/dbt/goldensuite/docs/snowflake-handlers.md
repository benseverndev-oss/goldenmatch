# Snowflake handler module

The Snowflake-side surface in this package ships in two halves:

| Half | Where it lives | What it does |
|---|---|---|
| **Outside** | `dbt-goldensuite/macros/` | How Snowflake calls into goldenmatch (CREATE FUNCTION DDL, dbt macros). |
| **Inside** | `goldenmatch.snowflake.udfs` | The Python functions Snowflake's UDF / Stored Procedure HANDLER clauses point at. |

PR #553 shipped the dbt macros half of the outside; `cli/snowflake.py`
(the registration CLI that would `CREATE FUNCTION`/`CREATE PROCEDURE`
against a live account) has not shipped. This module ships the inside.

## Phase 1 -- scalar UDFs (working)

Per-string transforms and read-only identity lookups. All run as pure
Snowpark Python UDFs -- no Snowpark `Session` required, no writes.

### GoldenFlow transforms (8)

| Handler | Wraps |
|---|---|
| `normalize_email` | `goldenflow.transforms.email.email_normalize` |
| `normalize_phone` | `goldenflow.transforms.phone.phone_e164` |
| `normalize_date` | `goldenflow.transforms.dates.date_iso8601` |
| `normalize_name_proper` | `goldenflow.transforms.names.name_proper` |
| `canonicalize_url` | `goldenflow.transforms.url.url_normalize` |
| `canonicalize_address` | `goldenflow.transforms.address.address_standardize` |
| `strip` | `str.strip` |
| `whitespace_normalize` | `" ".join(s.split())` |

All eight accept `str | None` and return `str | None`. NULLs propagate
cleanly because Snowflake hands the Python UDF `None` for NULL inputs.

### Identity reads (5)

| Handler | Wraps |
|---|---|
| `identity_resolve(record_id, db_path)` | `goldenmatch.identity.query.find_by_record` |
| `identity_view(entity_id, db_path)` | `goldenmatch.identity.query.get_entity` |
| `identity_history(entity_id, db_path)` | `goldenmatch.identity.query.history` |
| `identity_conflicts(dataset, db_path)` | `goldenmatch.identity.query.find_conflicts` |
| `identity_list(dataset, status, db_path)` | `goldenmatch.identity.query.list_entities` |

The `db_path` argument points at a SQLite `IdentityStore` file. Pass
the empty string to use the default `identity.db` at the Snowflake
IMPORTS stage root (the location the
`goldenmatch snowflake init --identity-db` flag uploads to). Inside a
UDF the IMPORTS dir is resolved via
``sys._xoptions['snowflake_import_directory']``; outside Snowflake
(local tests) the handler falls back to `GOLDENMATCH_UDF_IMPORTS`,
then to the package directory.

The IdentityStore is opened *read-only* per call -- Snowpark UDFs run
sandboxed and the IMPORTS filesystem is read-only anyway, so any
write attempt errors fast. Writes happen via Phase 2 stored
procedures.

### Identity backends

`goldenmatch.identity.store.IdentityStore` supports four backends;
the UDF handlers above are the SQLite-on-stage read path specifically.
A Snowpark stored-procedure caller with a live `Session` can instead
open the identity graph directly against Snowflake tables:

| Backend | Storage | Notes |
|---|---|---|
| `sqlite` | Local file | Default. What the Phase 1 UDF handlers above read from a staged IMPORTS file. |
| `postgres` | Postgres | `psycopg`-based; supports pipeline mode and bulk COPY. |
| `mongo` | MongoDB | Document store; no `bulk_*` fast path. |
| `snowflake` | Snowflake tables | `IdentityStore(backend="snowflake", connection=..., database=..., schema=...)`. `MERGE`-based writes (Snowflake enforces neither `PRIMARY KEY` nor `UNIQUE`), and a `bulk_*` staged-write fast path via `write_pandas` + `MERGE`. See `goldenmatch.identity.snowflake_backend.SnowflakeIdentityStore`. |

## Phase 2 -- stored procedures (scaffolds + NotImplementedError)

Operations that read or write Snowflake tables can't run as pure
UDFs -- they need a Snowpark `Session`, which only stored procedures
get. Phase 2 ships the SP variants in a follow-up.

| Scaffold | Reason it's deferred |
|---|---|
| `correction_add` | Writes to MemoryStore -- needs a Snowflake-native MemoryStore backend (writes via Session, not SQLite-on-stage). |
| `scan_table` | No longer blocked on a store: needs only a Snowpark Session to read the relation into a Polars frame and hand it to `goldencheck.engine.scanner.scan_dataframe`, which already accepts an in-memory table. |
| `health_score` | No longer blocked on a store: same Session-to-profile path as `scan_table`. |
| `DedupeFull` / `DedupeClusters` / `DedupePairs` | Read the input relation, call `goldenmatch.dedupe_df(df, cfg)`, write the output back. Wait on the registration CLI (`cli/snowflake.py`, not yet shipped) to expose them as callable Stored Procedures. |

Each scaffold raises `NotImplementedError("... ships in Phase 2 ...")`
with a workable remediation hint pointing at out-of-band paths today.

## Single source of truth across paths

Both the Snowpark Python UDF path (from
[snowflake-setup.md](snowflake-setup.md)) and the SPCS service path
(from [snowflake-spcs.md](snowflake-spcs.md)) call into the same
`goldenmatch.snowflake.udfs` module. The two paths can't diverge:

| Path | Where handlers run | Module imported |
|---|---|---|
| Snowpark Python UDF | Snowflake's UDF sandbox | `goldenmatch.snowflake.udfs.<func>` via the `HANDLER` clause |
| SPCS service | Container running goldenmatch[native] | Same `goldenmatch.snowflake.udfs.<func>` re-exported through Flask routes in `spcs/server.py` |

The SPCS server's `# TODO(spcs):` markers from PR #553 are gone --
the routes now delegate directly to the handler module.

## Testing

Phase 1 handlers are unit-tested in
`tests/snowflake/test_udfs.py` (28 tests):

  - 8 transform handlers: input/output check against real goldenflow
    primitives.
  - 5 identity reads: against an on-disk SQLite IdentityStore the
    test seeds via the public `IdentityStore` API.
  - 6 Phase 2 scaffolds: assert `NotImplementedError` with the
    documented "ships in Phase 2" message.
  - IMPORTS-directory resolution (env override, relative path,
    absolute path, empty fallback).

All run with no live Snowflake connection.

## Phase 2 design notes

When the SP migration lands, the natural shape:

1. **MemoryStore Snowflake backend.** Add a `snowflake` backend
   alongside `sqlite` / `postgres` in
   `goldenmatch.core.memory.store.MemoryStore`. Writes go through
   `session.write_pandas(...)` against a
   `<db>.goldenmatch.corrections` table.

2. **GoldenCheck Snowflake scan.** Add `scan_relation(session,
   relation_name)` to `goldencheck.engine.scanner` -- reads the
   relation into Polars via Snowpark, profiles + scans, returns the
   same `list[Finding]` shape the file-based path emits.

3. **Dedupe Stored Procedures.** Each of the three output shapes
   becomes a Snowpark SP receiving Session as first arg, reading the
   relation, calling `goldenmatch.dedupe_df`, writing back via
   `result.write.save_as_table(...)`. The dbt
   `goldenmatch_dedupe` materialization switches from
   `CREATE TABLE ... AS SELECT * FROM TABLE(<udtf>(...))` to
   `CALL goldenmatch.goldenmatch_dedupe_full('<in>', '<config>', '<out>')`
   plus a `SELECT * FROM <out>`.

Tracking issue to be filed alongside this PR.
