# Arrow-native SQLite bulk writes via ADBC — design + measurement plan

**Date:** 2026-07-24 • **Status:** Proposed — spike NOT yet run, no dependency added

## Question

`IdentityStore` has a bulk write fast path (`bulk_upsert_identities` /
`bulk_upsert_records` / `bulk_add_edges` / `bulk_emit_events`) that exists **only
for Postgres**; every one of them raises `NotImplementedError` on SQLite, and
`resolve_clusters` gates on it with a literal
`use_bulk_fast_path = getattr(store, "_backend", None) == "postgres"`.

The reason given was that SQLite has no `COPY`. That is true of the stdlib
`sqlite3` driver, but it is not true of SQLite as a target: Apache Arrow's
**ADBC SQLite driver** supports columnar bulk ingest (`adbc_ingest` from Python;
`adbc_core` + `adbc_driver_manager` from Rust, loading `libadbc_driver_sqlite`).

So: **should the SQLite identity backend adopt ADBC for its bulk write path,
and what would that actually buy?**

## What #2105 / PR #2111 already fixed, and what it left

#2111 addressed three things on the SQLite resolve path. Numbers are from a
Windows dev box and are directional only — Windows fsync is not Linux — except
the memory figure, which is a Python-heap measurement and transfers.

| | before | after |
|---|---|---|
| Prep Python heap | ~2,495 B **per input row** | same per row, but only for **referenced** rows |
| SQLite write | ~750 us/statement (autocommit) | ~30-90 us/statement (batched txn) |
| 100k rows / 5,000 identities | 31.28 s | 9.18 s |

What it left standing, and why ADBC is the candidate for it:

1. **The per-row Python object is still there.** #2111 *bounded how many rows*
   get turned into Python dicts; it did not remove the dict. The prep still
   builds a row dict + payload dict + hash + source + pk + record-id candidates
   per referenced row, at ~2.5 KB each. With **`emit_singletons=True` — the
   schema default — every row is referenced**, so the ~35 GB-at-14M problem
   returns in full. Bounding the input does not fix the default configuration;
   only removing the per-row Python object does.
2. **SQLite still has no bulk path at all.** Every eligible brand-new cluster
   goes through `upsert_identity` / `upsert_record` / `add_edge` / `emit_event`
   one statement at a time. Postgres accumulates and flushes in 4 COPY batches.

## Why ADBC maps cleanly onto what already exists

ADBC ingest is create/append, **not** upsert, so it cannot write `identity_nodes`
directly. But that is exactly the shape the Postgres path already uses — stage,
then upsert:

```
Postgres today:  COPY -> TEMP _stage_source_records -> INSERT ... SELECT ... ON CONFLICT DO UPDATE -> DROP
SQLite w/ ADBC:  adbc_ingest("_stage_source_records", batch, mode="create"/"append")
                                                  -> INSERT ... SELECT ... ON CONFLICT DO UPDATE -> DROP
```

SQLite has supported `INSERT ... ON CONFLICT DO UPDATE` since 3.24, so the second
half is a near-verbatim port of the Postgres SQL. The consequence is that
`use_bulk_fast_path` stops being backend-specific and the four `bulk_*` methods
get a real SQLite branch instead of a `NotImplementedError`.

**One SQLite-specific deviation, found while building the spike.** When an
UPSERT's INSERT takes its values from a `SELECT`, SQLite's parser cannot tell
whether `ON` opens the upsert clause or a join constraint, and fails with
``near "DO": syntax error``. The documented workaround is to give the SELECT a
WHERE clause — `FROM _stage_x WHERE true`. Postgres needs no such thing, so the
port is verbatim *except* here. Cheap, but it is the kind of thing that turns
into a confusing half-hour if it is discovered during implementation instead of
now.

## Ceiling analysis — what this can and cannot buy

Being explicit here because it is easy to oversell.

**It cannot buy another large write-throughput multiple.** SQLite has no `COPY`.
The ADBC SQLite driver implements ingest as prepared INSERTs bound inside a
transaction — which is materially what #2111 already does. The 8-25x from
collapsing per-statement commits is **already banked**. Expect low single digits
from skipping per-row Python tuple construction and `sqlite3` type conversion,
not another order of magnitude.

**What it can buy:**

- **Elimination of the per-row Python object on the bulk path.** This is the
  real prize and the only thing that fixes `emit_singletons=True` at scale. If
  the accumulate-and-flush goes frame -> frame, the ~2.5 KB/row Python heap for
  bulk-eligible clusters goes to roughly zero.
- **One bulk code path for both SQL backends** instead of a Postgres-only fast
  path plus a SQLite slow path that silently diverge (see the payload trap below).
- **Direction fit.** `pyarrow>=10` and `duckdb>=0.9` are already core deps and
  the Frame seam has an Arrow lane, so an Arrow-native store write path is
  consistent with the "Rust is the reference" / Polars-eviction arc rather than
  a new axis.

## The blocking design problem: SQLite is single-writer

This is the part that decides whether the incremental version is even viable,
and it has no Postgres analogue.

An ADBC connection is **a different connection** from the stdlib `sqlite3` one
`IdentityStore` holds. SQLite permits exactly one write transaction at a time
across connections; a second writer gets `SQLITE_BUSY`. And #2111 now holds an
explicit write transaction around the whole resolve loop via `bulk_writes()` —
which is precisely where the bulk flush would fire. So a naive "open an ADBC
connection alongside the existing one" deadlocks against our own transaction.

Three options:

**A. Move the whole `IdentityStore` SQLite backend onto ADBC.** One connection,
no contention, and every write (row path and bulk path) becomes Arrow-native.
Cleanest end state; largest migration — every `_exec` / `_fetchone` / `_fetchall`
call site, the schema bootstrap, `PRAGMA` handling, and `in_transaction`
semantics that #2111's batching depends on.

**B. Dual connection with explicit sequencing.** Keep stdlib `sqlite3` for the
row path; open ADBC only for bulk flushes, and sequence so the stdlib write
transaction is committed and closed before the ADBC ingest runs, then reopened.
Smaller change, but it fragments the transaction into "row-path txn / ADBC txn /
row-path txn" per flush, which weakens the atomicity story and adds a real
`SQLITE_BUSY` surface under any concurrent reader (WAL helps readers, not a
second writer).

**C. Do nothing.** #2111 already removed the OOM for `emit_singletons=False` and
took the write path from ~750 us to ~30-90 us per statement. If measurement says
the remaining Python-object cost is not the binding constraint at the scales
people actually run SQLite at, the honest answer is to leave it and point users
at Postgres past the low millions.

**Leaning A over B**, because B's benefit is bounded by the same "no COPY in
SQLite" ceiling while its cost is a permanently more confusing transaction model.
But A is only worth its migration cost if the spike shows the Python-object
elimination is worth real money. Hence: measure first.

## Traps to carry into implementation

1. **The Postgres bulk path silently drops payloads.** `bulk_upsert_records`'s
   column list omits `payload`, and its `ON CONFLICT DO UPDATE` does not touch it
   — so a record written via the Postgres bulk path has `payload = NULL`, while
   the SQLite row path stores it. `bulk_emit_events` likewise carries no event
   `payload`, while the row path emits
   `{"cluster_id", "member_count", "record_ids"}`. **Extending the fast path to
   SQLite without carrying payloads would be a silent data regression for every
   existing SQLite user.** Either carry payloads on the bulk path (and decide
   whether to fix Postgres to match) or keep payload-bearing clusters off it.
2. **Entity ids are the durability invariant.** Any new write path needs the
   byte-identical store-contents parity gate — the
   `tests/identity/test_resolve_scaling.py::_dump` canonicalisation (keying each
   identity by its record-id set, since entity ids are random UUIDv7) is the
   existing harness for this and should be reused.
3. **New dependency.** `adbc-driver-sqlite` ships a bundled shared library. It
   must be an **extra** with a graceful fallback to the current path, matching
   the `[polars]` / `_native_loader` idiom — never a hard core dep.
4. **The Rust story is a dynamic load, not a crate.** `adbc_core` +
   `adbc_driver_manager` use `ManagedDriver::load_dynamic_from_filename(...)`,
   i.e. you locate/ship `libadbc_driver_sqlite.{so,dylib,dll}` per platform. This
   repo has scar tissue here (`ort`/`onnxruntime` do not link locally on Windows;
   the native wheel's macOS arch matrix). Do not assume the Python win transfers
   to a Rust surface for free.
5. **Schema/type mapping.** `golden_record`, `payload`, `field_scores`,
   `negative_evidence`, `controller_snapshot` are JSON-in-TEXT; timestamps are
   ISO strings via `.isoformat()`. The ingest must reproduce those exact
   encodings or stored bytes drift.

## Spike — decide before building

**Built.** `packages/python/goldenmatch/scripts/spike_adbc_sqlite_ingest.py`,
run by the `spike-adbc-sqlite-ingest` workflow on `large-new-64GB` (**not**
locally — see the bench-runner rule). Three arms over the real identity schema,
N in {100k, 1M, 5M}:

| arm | what it is | new dependency |
|---|---|---|
| `rowpath` | today's batched row path via the `IdentityStore` API | no |
| `staging` | staging table + stdlib `executemany` + the same upsert | **no** |
| `adbc` | `adbc_ingest` into the same staging table + the same upsert | yes |

All three start from the same `pyarrow.Table`; `rowpath` and `staging` pay the
Arrow -> Python-rows conversion inside their timed region, because that
conversion *is* the cost under examination. Each arm runs in its own subprocess
so peak RSS is that arm's alone.

**Correctness gate:** every arm's resulting database is content-hashed over
`identity_nodes` + `source_records`, so a faster arm that writes different bytes
reports a mismatch instead of looking like a win. Already exercised: `staging`
comes out byte-identical to `rowpath`.

`adbc-driver-sqlite` is layered in by the workflow with `uv run --with`; it does
**not** enter `uv.lock` or the package dependencies unless this spike says GO.
The ADBC API surface the spike relies on is verified working against a WAL-mode
SQLite file: `dbapi.connect(uri=path)` is the file-connect form (the published
examples only show in-memory), `adbc_ingest` creates the staging table, and the
staged upsert runs on the same ADBC connection.

### Kill criteria

- If **(c) staging + `executemany`** captures most of (b)'s win, ship (c) and
  **drop ADBC entirely** — same benefit, zero new dependency. This is the outcome
  I consider most likely and it should be the null hypothesis.
- If (b) beats (c) by **< 1.5x wall and < 30% peak RSS** at 1M, **NO-GO** — the
  dependency and the single-writer complexity are not worth it.
- If (b) clearly wins on RSS at 5M, proceed with **option A**, gated behind an
  extra plus a kill switch, with the parity gate from trap 2 as the merge blocker.

The spike prints this verdict itself rather than leaving it to interpretation.

**Read the `batched_row_path` flag in the output.** The spike reports whether
#2111's SQLite transaction batching is present; without it the `rowpath` baseline
is the old per-statement-autocommit path and every other arm looks far better
than it is. Do not run the spike for a decision until #2111 has landed.

## Relationship to other work

- Builds on **PR #2111** (#2105). That PR must land first — it introduces the
  `bulk_writes()` SQLite transaction that option B has to sequence around, and
  the parity-test harness this work reuses.
- Independent of the distributed resolver (#627), which is Ray-path and
  Postgres-only (`pipeline.py` dispatches only when `is_ray_dataset(clusters)`
  and hard-requires `backend == "postgres"`).
- If option A lands, the `emit_singletons=True` guidance added to
  `identity-graph.mdx` in #2111 should be revisited — the reason for the warning
  is exactly the per-row Python object this would remove.
