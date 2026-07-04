# Cross-surface parity roadmap

**Status:** living backlog. **Owner:** ER platform. **Created:** 2026-07-04.

## The goal

Every GoldenMatch kernel should run as **one shared pyo3-free Rust `*-core`
crate, compiled/exposed natively on every surface it makes sense for** — Python
(native wheel + fallback), edge TS/WASM, DuckDB, Postgres — so output is
byte-identical and there is exactly one source of truth. This extends the
"Rust is the reference" line (`2026-07-01-rust-is-the-reference-roadmap.md`) from
"Rust vs Python" to "Rust vs *every surface*".

**The bar is native, not bridged.** A capability reachable on SQL only through
the JSON / embedded-CPython bridge UDFs (`duckdb/.../core_apis.py`,
`postgres/src/quick.rs`) does **not** count as cross-surface parity — that is
Python-in-a-box, not the shared kernel compiled per engine. Parity = the
`*-core` kernel itself running on the surface.

## Where we are (2026-07-04)

Only **HNSW** (goldenhnsw) is native on all four surfaces today. **sketch /
MinHash-LSH** joins it when PR #1413 merges. Everything else is half-covered, in
one of two camps:

| Kernel | core | Python | TS/WASM | DuckDB | PG | Camp |
|---|:--:|:--:|:--:|:--:|:--:|---|
| HNSW (goldenhnsw) | ✅ | ✅ | ✅ | ✅ | ✅ | **full** |
| sketch / MinHash-LSH | ✅ | ✅ | 🟡#1413 | 🟡#1413 | 🟡#1413 | **full (pending)** |
| score (jaro/lev/token) | ✅ | ✅ | ✅ | ❌ | ❌ | edge-only |
| graph (CC + pair-dedup) | ✅ | ✅ | ❌ | ✅ | ✅ | SQL-only |
| fingerprint (record) | ✅ | ✅ | 🟡P3 | ✅ | ✅ | **full (pending P3)** |
| embed (goldenembed) | ✅ | ✅ | ❌ | ✅ | ✅ | SQL-only (edge blocked) |
| perceptual (pHash) | ✅ | ✅ | ✅ | ❌ | ❌ | edge-only |
| autoconfig | ✅ | ✅ | ✅ | ❌ | ❌ | edge-only |
| suggest (healer) | ✅ | ✅ | ✅ | ❌ | ❌ | edge-only |
| goldencheck | ✅ | ✅ | ✅ | ❌ | ❌ | edge-only |
| goldenprofile | ✅ | ✅ | ✅ | ❌ | ❌ | edge-only |
| goldengraph | ✅ | ✅ | ✅ | ❌ | ❌ | edge-only |
| goldenanalysis | ✅ | ✅ | ✅ | ❌ | ❌ | edge-only |
| goldenflow | ✅ | ✅ | ✅#1412 | ❌ | ❌ | edge-only |

- **Camp A — edge-native, no SQL:** score, perceptual, autoconfig, suggest,
  goldencheck, goldenprofile, goldengraph, goldenanalysis, goldenflow.
  → close by adding a native DuckDB kernel + a pgrx `#[pg_extern]`.
- **Camp B — SQL-native, no edge:** graph, fingerprint, embed.
  → close by adding a `*-wasm` crate + a TS reroute.

## Prioritization (value ÷ effort)

Ranked. Each item is independently shippable as one cross-surface PR (or split
per surface, like #1413). "Kernel-ready" = the `*-core` exists and has no
wasm-hostile deps.

### Tier 1 — do next (high value, kernel-ready)

- [ ] **P1 · score → SQL (DuckDB + Postgres).** The actual matching math
  (jaro_winkler / levenshtein / token_sort in `score-core`). Highest leverage:
  it makes in-warehouse fuzzy scoring native instead of bridged, and it pairs
  with the SQL blocking that already exists (HNSW/LSH). Kernel-ready (pure Rust,
  no heavy deps; already compiles to wasm via `score-wasm`). Shape:
  `goldenmatch_score_pairs(a text[], b text[], method text) -> double[]` (+/or a
  scalar `goldenmatch_score(a text, b text, method text) -> double8`). *Effort:
  M.*
- [ ] **P2 · graph → edge (graph-wasm + TS reroute).** Connected-components /
  pair-dedup clustering (`graph-core`). With LSH blocking (#1413) on the edge,
  this makes the **whole ER pipeline run edge-native** (block → score → cluster)
  with no Node/Python. Kernel-ready (pure Rust). **First step: check whether the
  TS dedupe already hand-rolls a union-find** — if so this is also a
  divergence-risk fix (sketch/goldencheck class), which bumps the value.
  Shape: mirror `goldenhnsw-wasm` (typed-array in, pairs/labels out). *Effort: M.*

### Tier 2 — valuable, straightforward

- [x] **P3 · fingerprint → edge.** `record_fingerprint` (canonical record hash)
  — canonical keys everywhere (dedup joins, cache keys). On audit the only real
  gap was the **edge**: DuckDB's `goldenmatch_record_fingerprint` already calls
  the native-gated `record_fingerprint` (native-authoritative when the wheel is
  present — not the embedded-CPython bridge), and Postgres is native-direct; only
  the TS surface hand-rolled its own canonicalizer (a silent-divergence risk).
  Closed it with a `fingerprint-wasm` crate over `fingerprint-core::fingerprint_
  json` + a `recordFingerprint` reroute (JSON-primitive-safe records run the
  shared kernel; bigint/`Uint8Array` stay pure-TS) + a shared golden oracle. Done
  in the sketch/graph structural twin — `fixture_drift` auto-covers it. *(Done.)*
- [ ] **P4 · perceptual → SQL (DuckDB + Postgres).** Image pHash
  (`perceptual-core`). Easy (pure Rust, small), niche use (image/near-dup).
  Shape: `goldenmatch_perceptual_hash(bytes) -> int8` + a distance helper.
  *Effort: S.*

### Tier 3 — opportunistic (lower marginal value on the target surface)

- [ ] **P5 · goldencheck → SQL.** The 7 profiling kernels could be SQL UDFs, but
  they are table/column-shaped and SQL users can already call the Python; the
  marginal value of native SQL is lower. Do if a concrete SQL-profiling ask lands.
- [ ] **P6 · goldenprofile / goldenanalysis → SQL.** Same reasoning — profiling
  over whole columns; native SQL is nice-to-have, not load-bearing.
- [ ] **P7 · autoconfig / suggest → SQL.** Config-shaped (produce a config from a
  table sample); awkward as row-wise SQL UDFs. Low priority.
- [ ] **P8 · goldengraph → SQL.** KG engine; multi-table, heavy, not SQL-UDF
  shaped. Low priority.
- [ ] **P9 · goldenflow → SQL (native).** goldenflow transforms are already
  exposed on DuckDB/Postgres **via the bridge** (`goldenflow_*`); a native
  rewrite over `goldenflow-core` is a parity upgrade, not new capability. Do when
  touching goldenflow anyway.

### Tier 4 — deferred / blocked

- [ ] **P10 · embed → edge.** BLOCKED: `goldenembed` links `ort` (ONNX Runtime),
  which does not compile to wasm32. Edge embedding would need a separate path
  (e.g. `@huggingface/transformers`, already a TS peer dep) — a different design,
  not the "compile the core to wasm" pattern. Defer until there is an
  edge-embedding requirement; treat as its own project, not a parity item.

## Definition of done (the repeatable pattern)

**Camp A — add SQL to an edge/Python kernel:**
1. DuckDB: a native kernel file (`duckdb/goldenmatch_duckdb/<name>_kernels.py`)
   calling the kernel (native-gated), registered in `functions.py`; a test in
   `duckdb/tests/` (incl. a byte-parity check vs the Python/kernel reference).
2. Postgres: a `#[pg_extern]` in `postgres/src/kernels.rs` (native-direct, no
   CPython); **new** SQL version (base + migration), `.control` + Cargo bumps,
   `cp` lines in `ci.yml` + `publish-goldenmatch-pg.yml`; a `rust_pgrx` smoke
   with a config validated against the reference. `pgrx_sql_sync` (CI) verifies
   the function is wired into the SQL.

**Camp B — add edge to a SQL/Python kernel:**
1. A `<name>-wasm` crate (mirror `goldenhnsw-wasm` / `sketch-wasm`): wasm-bindgen
   over the `*-core`; typed-array/`BigUint64Array` boundary; no wasm-hostile deps
   (rayon is fine — it falls back to sequential; `ort`/native C is not).
2. TS embed: `scripts/build_<name>_wasm.mjs` (base64-inlined wasm + bindings,
   committed; copies the shared golden fixture); a synchronous `initSync` loader;
   a lean-registry `<name>WasmBackend.ts` (import-type-only, zero default-bundle
   cost); reroute the hand-written TS onto it with pure-TS as fallback.
3. Tests: a golden-fixture parity test (wasm == the shared oracle) + a
   reroute-equivalence test (wasm == pure-TS). CI: a `<name>_wasm` path filter +
   drift-guard step in the `typescript` lane. `fixture_drift` (CI) auto-covers
   the new fixture on any rust-extension change.

**Both camps benefit from the #1410 guards:** `fixture_drift` (no stale wasm
fixture) and `pgrx_sql_sync` (no `#[pg_extern]` missing from the SQL).

## Reference PRs (the pattern in action)

- HNSW cross-surface: #1401 (TS/WASM + DuckDB + Postgres, one PR / 3 commits).
- goldencheck Rust-source-of-truth (edge reroute): #1403.
- CI guards that make parity self-enforcing: #1410.
- sketch / MinHash-LSH cross-surface: #1413 (the Camp-B→everywhere template).
