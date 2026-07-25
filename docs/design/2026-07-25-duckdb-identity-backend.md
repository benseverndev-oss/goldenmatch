# DuckDB as an Identity Store backend — design + decision

**Date:** 2026-07-25 • **Status:** Proposed — perf motivation refuted by the ADBC spike; remaining case is strategic and needs its own measurement before building

## Question

`IdentityStore` supports three backends — `sqlite` (default, single-file),
`postgres` (server, the only one with a bulk write fast path), and `mongo`
(standalone class). There is **no DuckDB backend**: `store.py:278` raises
`NotImplementedError` for any other `backend` value, and `goldenmatch/identity/`
contains zero DuckDB code.

DuckDB is attractive on paper for exactly the gap `sqlite` leaves: it is a
single **file** with **no server** (like SQLite), but it has a columnar engine,
native **Arrow** ingest, `INSERT ... SELECT ... ON CONFLICT DO UPDATE` (upsert),
out-of-core execution that spills to disk, and it is already a first-class
optional dependency in this repo (`goldenmatch[duckdb]`, `backends/duckdb_backend.py`,
the chunked pipeline, the prepared-record store).

So: **should the Identity Store gain a `backend="duckdb"` that gives single-node
users the Postgres-class bulk write path — and the analytical read views — without
standing up a server?**

This doc is a companion to `2026-07-24-adbc-sqlite-bulk-writes.md`. That doc asked
whether *SQLite* should get an Arrow-native bulk path via ADBC. Its spike has now
run, and the result reshapes the DuckDB question below.

## What the ADBC spike settled (and why it matters here)

The ADBC spike (`scripts/spike_adbc_sqlite_ingest.py`, three arms — today's
batched `rowpath`, a stdlib `staging` table + `executemany` + upsert, and
Arrow-native `adbc` ingest into the same staging table) measured, byte-identical
output across all arms:

Authoritative `large-new-64GB` numbers, content-hash identical across all arms:

| N | arm | wall | peak RSS | db size |
|---|---|---|---|---|
| 100k | rowpath | 13.74 s | 0.662 GB | 212 MB |
| 100k | staging | 5.44 s | 0.659 GB | 212 MB |
| 100k | adbc | 3.29 s | 0.566 GB | 338 MB |
| 1M | rowpath | 175.94 s | 5.714 GB | 2175 MB |
| 1M | staging | 65.65 s | 5.715 GB | 2175 MB |
| 1M | adbc | 41.32 s | 5.294 GB | 3430 MB |
| 5M | rowpath | 987.39 s | 24.719 GB | 10906 MB |
| 5M | staging | 445.05 s | 26.039 GB | 10906 MB |
| 5M | adbc | 392.15 s | 24.543 GB | 17184 MB |

Per-scale harness verdict (`adbc` vs `staging`, GO if ≥1.5× wall OR ≥30% RSS):
**GO / GO / NO-GO** — 1.65× wall·14% RSS, 1.59× wall·7% RSS, 1.13× wall·6% RSS.

Two findings drive everything below:

1. **`staging` (stdlib, zero new dependency) captures the bulk of the win.** It
   is ~2.5× over `rowpath` at 100k, ~2.7× at 1M, ~2.2× at 5M, with identical peak
   RSS. The `emit_singletons=False` OOM was already removed by #2111; `staging`
   banks the remaining per-statement throughput win with no dependency. (`adbc`
   is a further ~1.6× faster than `staging` at 100k/1M, but that advantage decays
   to 1.13× at 5M — the scale where a bulk path is actually needed — and it is
   the *wall*, not the memory, that ADBC moves.)
2. **Arrow-native ingest does *not* fix the memory ceiling.** `adbc` ingests the
   Arrow table directly — no `to_pylist()`, no per-row Python dict — yet its peak
   RSS is only **14% → 7% → 6% below `staging`** across 100k/1M/5M, never within
   reach of the 30% bar, and the gap **narrows** with scale (at 5M, 24.5 vs 26.0
   GB). The doc's headline hypothesis — "eliminating the per-row Python object is
   the real prize" — is **refuted**: the memory floor is the **materialised Arrow
   table itself** (~JSON-in-string payloads across the identity + record columns)
   plus the engine's write buffers, not the `to_pylist` delta on top of it.

The per-scale harness verdict is GO / GO / NO-GO (adbc clears the 1.5× wall bar
at 100k/1M but decays to 1.13× at 5M, and never approaches the 30% RSS bar). The
**net** ADBC decision is still **NO-GO on the dependency** — the wall win is at
scales that don't need a bulk path and evaporates at 5M, the memory prize never
materialises, and it costs a native dependency + 58%-larger files + the
single-writer complexity. Ship stdlib `staging`. (Full reasoning in the ADBC
doc's "Spike result".)

**The load-bearing consequence for DuckDB:** DuckDB's bulk write, when handed the
same in-memory Arrow table, sits behind the *same* Arrow-table memory floor as
ADBC. Registering an Arrow table as a DuckDB view and running
`INSERT ... SELECT ... ON CONFLICT` is the same shape as `adbc_ingest` + the same
upsert — so on the metric the ADBC spike pre-committed to (peak RSS at scale),
**DuckDB has no reason to beat `staging` either**, unless it does something ADBC
did not: **stream the source instead of materialising it**.

## Why DuckDB is still a candidate (a different case than ADBC)

ADBC's only pitch was "Arrow-native write path." The spike killed that pitch on
the numbers. DuckDB's pitch is broader and does not stand or fall on write
throughput:

1. **Server-less bulk path.** Today the ONLY backend with `bulk_upsert_identities`
   / `bulk_upsert_records` / `bulk_add_edges` / `bulk_emit_events` is Postgres;
   SQLite raises `NotImplementedError` and `resolve_clusters` gates on
   `use_bulk_fast_path = _backend == "postgres"`. A DuckDB backend could give a
   single-node user the bulk path **without a Postgres server** — one file, one
   process. That is a product-positioning win independent of whether it beats
   `staging` by a millisecond.
2. **Native analytical read side.** The identity read surface is analytical —
   `v_identities`, `v_identity_pairs`, `v_identity_timeline`
   (`db/migrations/identity_v1.sql`), plus `entity_profile` / `identity_summary_stats`
   / `steward_worklist` (`identity/profile.py`). Those are aggregation queries
   DuckDB runs natively and SQLite runs slowly.
3. **Potential to actually break the Arrow floor — the one perf angle left.**
   Unlike the spike's in-memory arms, DuckDB can `read_parquet`/scan a source
   lazily and spill, so an ingest that streams from the *source* (rather than a
   fully-materialised in-memory Arrow table) could bound peak RSS below `staging`'s
   ~5.7 GB at 1M / ~26 GB at 5M. This is conditional on the write path handing DuckDB a lazy
   source, which the current resolve path does not — see the measurement below.
4. **Direction fit + already a dependency.** `duckdb>=0.9` and `pyarrow>=10` are
   already present; the Frame seam has an Arrow lane; the chunked/prepared-record
   subsystems already use DuckDB out-of-core. A DuckDB identity store is on the
   existing grain, not a new axis.

## Ceiling analysis — being explicit so we don't oversell

**Throughput:** no order-of-magnitude left to win. `staging` already banks it;
DuckDB's `INSERT ... SELECT ... ON CONFLICT` is the same accumulate-then-upsert
shape. Expect parity-to-modest, not a multiple.

**Memory, in-memory ingest:** DuckDB ≈ `staging` ≈ `adbc` — all gated by the
materialised frame. No win.

**Memory, streaming ingest:** *unknown and the only thing worth a spike.* If the
resolve write path can stream cluster aggregates to DuckDB in bounded batches (or
DuckDB scans a spilled source), peak RSS could drop below the frame floor. This
is the same "bounded streaming on the ~2.2 GB live floor" lever the FS
frame-residency work is chasing (`2026-07-20-fs-frame-residency-bucket-streaming-design.md`),
applied to the identity write side.

**Read latency:** clear DuckDB win on the analytical views; not why anyone
picks a write backend, but it compounds the server-less case.

## Options

**A. First-class `backend="duckdb"` Identity Store.** A full third backend: schema
DDL (translate `identity_v1.sql`), all CRUD, the four `bulk_*` methods (native via
an Arrow view + `ON CONFLICT` upsert), `migrate-ids`, audit seal/verify, and the
analytical views. `resolve_clusters` sets `use_bulk_fast_path` for `duckdb` too.
Largest surface — `store.py` is deeply per-backend inlined, so this is a Mongo-class
addition (its own branch or standalone class) plus a CI lane and the byte-identical
parity gate. The strongest end state for the "server-less bulk + analytical" pitch.

**B. DuckDB as a write *accelerator* for the SQLite store (`ATTACH`).** Keep SQLite
as the on-disk format and the row path; for bulk flushes only, DuckDB `ATTACH`es
the `.db` file and runs the Arrow-view `INSERT ... SELECT ... ON CONFLICT` into it.
Smaller surface, reuses the SQLite schema and every existing SQLite reader. But it
inherits the **single-writer sequencing** problem the ADBC doc's option B has
(DuckDB's SQLite `ATTACH` write is a second writer against #2111's held
transaction), the maturity of DuckDB's SQLite write path is a risk, and — given
the spike — it is unlikely to beat stdlib `staging` on either axis. Hard to justify.

**C. Do nothing DuckDB-specific.** Ship the ADBC spike's winner — stdlib `staging`
for the SQLite bulk path — and keep routing past-the-low-millions users to Postgres.
The null hypothesis, and consistent with the ADBC verdict. A DuckDB backend stays a
future option gated on a real server-less-analytical user need.

## The measurement that would decide (before building A)

DuckDB's write path does **not** deserve the ADBC spike's benefit of the doubt on
throughput — that question is answered. The only open, decision-changing question
is **memory under a streaming ingest.** Add two arms to the existing harness
(`spike_adbc_sqlite_ingest.py`) at 1M and 5M, byte-identical-gated like the rest:

- `duckdb_inmem` — register the same `pyarrow.Table` as a view,
  `INSERT INTO identity_nodes SELECT ... FROM v ON CONFLICT DO UPDATE`. Expected to
  match `staging` on RSS (confirms the floor). Cheap to add; settles it.
- `duckdb_stream` — write the source to Parquet first, then
  `INSERT ... SELECT ... FROM read_parquet(...) ON CONFLICT DO UPDATE` so DuckDB
  scans and spills instead of holding the frame. **This is the real test:** does
  streaming ingest bound peak RSS below `staging`'s ~5.7 GB at 1M / ~26 GB at 5M?

**Kill criteria (pre-committed, mirroring the ADBC doc):**

- If `duckdb_stream` does **not** beat `staging` by **≥ 30% peak RSS at 5M**, the
  perf case is dead → **Option C**, and any DuckDB backend must be justified
  purely by the server-less-analytical product need, not performance.
- If `duckdb_stream` **does** clear ≥ 30% RSS at 5M, that is the first real lever
  on the identity memory ceiling that does not require Postgres → pursue **Option
  A**, gated behind the `[duckdb]` extra + a kill switch, with the parity gate as
  the merge blocker.

## Traps to carry into implementation

1. **Byte-identical store parity is the merge gate.** Reuse
   `tests/identity/test_resolve_scaling.py`'s `_dump` canonicalisation (identities
   keyed by their record-id set, since `entity_id` is a random UUIDv7) — a faster
   backend that writes different bytes is not a win. Same discipline the ADBC
   spike's content-hash used.
2. **The Postgres bulk path silently drops `payload`** (`bulk_upsert_records`'s
   column list omits it; `bulk_emit_events` carries no event payload) while the
   SQLite/row path stores it. Any DuckDB bulk path must carry payloads or it
   reintroduces the ADBC doc's silent-data-regression trap. Decide whether to fix
   Postgres to match at the same time.
3. **DuckDB is also single-writer per file.** Unlike ADBC-alongside-SQLite this is
   *one* connection if the whole backend is DuckDB (Option A), so no dual-writer
   deadlock — but concurrent multi-process writers still serialise, same as the
   SQLite WAL story. Don't advertise multi-writer.
4. **Schema/type encodings must reproduce exactly.** `golden_record`, `payload`,
   `field_scores`, `negative_evidence`, `controller_snapshot` are JSON-in-TEXT;
   timestamps are ISO strings via `.isoformat()`. DuckDB has richer native types
   (JSON, TIMESTAMP) — the backend must either store the same TEXT/ISO encodings
   or the parity gate will flag drift. Prefer matching the existing encodings.
5. **`ON CONFLICT ... FROM SELECT` parser quirk.** SQLite needs the `WHERE true`
   workaround (ADBC doc); confirm DuckDB's `INSERT ... SELECT ... ON CONFLICT`
   grammar and target-key requirements (DuckDB needs a UNIQUE/PK index on the
   conflict target) up front.
6. **New optional dependency discipline.** `backend="duckdb"` must degrade like
   `[polars]` / `_native_loader` — a graceful error when `duckdb` is absent, never
   a hard core dep. It is already a `[duckdb]` extra; the identity backend rides
   that.

## Recommendation

**Adopt Option C now** (ship the ADBC spike's `staging` winner for the SQLite bulk
path; keep Postgres for past-the-millions) and **do not build the DuckDB backend
on a performance argument** — the ADBC spike refuted the Arrow-native-write-path
motivation that DuckDB would otherwise inherit.

Keep the DuckDB backend as a **live proposal with a single gating experiment**: the
`duckdb_stream` arm above. It is the only path on which DuckDB could beat `staging`
on the metric that matters (memory at scale), *and* it is the only lever that would
hand a single-node user the bulk fast path without a Postgres server. If that arm
clears ≥ 30% RSS at 5M, Option A becomes the most valuable identity-scale work on
the board that does not require a server. If it does not, a DuckDB identity backend
is a product decision about server-less analytical storage, to be prioritised on
user demand — not a performance project.
