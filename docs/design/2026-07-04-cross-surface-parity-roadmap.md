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
| embed (goldenembed) | ✅ | ✅ | 🟡P10 | ✅ | ✅ | **full (pending P10)** |
| perceptual (pHash) | ✅ | ✅ | ✅ | 🟡P4 | 🟡P4 | **full (pending P4)** |
| autoconfig | ✅ | ✅ | ✅ | ❌ | ❌ | edge-only |
| suggest (healer) | ✅ | ✅ | ✅ | ❌ | ❌ | edge-only |
| goldencheck | ✅ | ✅ | ✅ | ❌ | ❌ | edge-only |
| goldenprofile | ✅ | ✅ | ✅ | ❌ | ❌ | edge-only |
| goldengraph | ✅ | ✅ | ✅ | ❌ | ❌ | edge-only |
| goldenanalysis | ✅ | ✅ | ✅ | ❌ | ❌ | edge-only |
| goldenflow | ✅ | ✅ | ✅#1412 | ✅v0.1.1 | ❌ | edge + DuckDB (PG left) |

- **Camp A — edge-native, no SQL:** score, perceptual, autoconfig, suggest,
  goldencheck, goldenprofile, goldengraph, goldenanalysis.
  → close by adding a native DuckDB kernel + a pgrx `#[pg_extern]`.
  (**goldenflow** left this camp: the compiled `goldenflow-duckdb` extension
  shipped as `v0.1.1` (74 transforms native in DuckDB, ADR 0032) — only the
  pgrx `#[pg_extern]` side remains.)
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
- [x] **P2 · graph → edge (graph-wasm + TS reroute).** Connected-components /
  pair-dedup clustering (`graph-core`). With LSH blocking (#1413) on the edge,
  this makes the **whole ER pipeline run edge-native** (block → score → cluster)
  with no Node/Python. `graph-wasm` + the `buildClusters` reroute (cluster.ts)
  landed earlier; the audit found **two more hand-rolled union-finds**
  (`ann-blocker.ts` micro-blocking, `graph-er.ts` multi-table clustering) still
  bypassing the kernel — the exact divergence-risk the first-step note warned of.
  Closed by extracting **one** shared `connectedComponents` primitive
  (`graphComponents.ts`: wasm backend when enabled, else a canonical pure-TS
  union-find) and routing all three sites through it — no hand-rolled union-find
  left on the edge. Toggle-invariant + output-preserving (equivalence tests
  `graph-wasm-reroute` + `graph-components-reroute`). *(Done.)*

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
- [x] **P4 · perceptual → SQL (DuckDB + Postgres).** Image pHash
  (`perceptual-core`). The kernel takes a decoded luma **grid** (image decode is
  a thin upstream concern, not in the core), so the SQL shape is
  `goldenmatch_perceptual_phash(grid double[] flat, ncols int) -> int8` (the u64
  hash bit-reinterpreted to signed i64 — DuckDB `BIGINT` == Postgres `int8`, so a
  hash stored from either surface compares equal) + the load-bearing
  `goldenmatch_perceptual_hamming(a int8, b int8) -> int` near-dup blocking
  predicate. Postgres native-direct over `perceptual-core` (v0.11→0.12); DuckDB
  a native-gated UDF over `goldenmatch.core.perceptual`. Same pinned pHash on all
  four surfaces (Rust golden / pgrx smoke / DuckDB test / Python). *(Done.)*

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
- [ ] **P9 · goldenflow → SQL (native). Re-scoped 2026-07-04; 2/8 done.** The 8
  SQL-exposed `goldenflow_*` transforms are `email_normalize`, `phone_e164`,
  `date_iso8601`, `name_proper`, `url_normalize`, `address_standardize`, `strip`,
  `collapse_whitespace`. The original premise (`goldenflow-core` backs them) was
  wrong for most, so this is a **per-transform** de-bridge, not a sweep:
  - ✅ **`strip`** / **`collapse_whitespace`** (Postgres de-bridged native-direct
    over the new `goldenflow-core::text`). Both are `mode="expr"` **polars** ops
    (`str.strip_chars()`, `str.replace_all(r"\s{2,}"," ")`), which use the Unicode
    `White_Space` set == Rust `char::is_whitespace` — so a std-only port is
    byte-identical, **proven** against a 29-case polars-generated Unicode corpus
    (`goldenflow-core/tests/text_golden.rs`: NBSP/VT/NEL/line-sep/U+205F/U+3000/
    U+2009/U+1680 are whitespace, ZWSP U+200B is not). No `regex` dep needed;
    same signatures, so no SQL/version change (the P1 `goldenmatch_score`
    pattern). DuckDB already runs the polars transform (the reference) — no change.
  - **`phone_e164`** has a core kernel (`phone::e164`) but it is **deliberately
    NANP-only** (`_native.py`: `nanp_only=True`, region `US`; international rows
    return null and the Python "tier-3" path settles them). De-bridging fully to
    native would *change results* for non-NANP input — NOT a drop-in; skip.
  - **`email_normalize` / `date_iso8601` / `name_proper` / `url_normalize` /
    `address_standardize`** have **no** `goldenflow-core` kernel yet. Porting is
    real work (`date`/`email` moderate; `url`/`address` are rules engines). Each
    must land in `goldenflow-core` *with* a polars byte-parity corpus first, then
    the Postgres extern de-bridges (no version bump).
  - Note: the embedded-CPython **bridge** anti-pattern is **Postgres-only** — the
    DuckDB `goldenflow_*` UDFs use in-process polars, not the embedded CPython.
    Remaining: port + de-bridge `date`/`email`, then `url`/`address`; low marginal
    value, do when touching goldenflow-core anyway.

### Tier 4 — was blocked, now unblocked

- [x] **P10 · embed → edge. Unblocked 2026-07-04 — the ONNX dep was
  unnecessary.** The block was `goldenembed` linking `ort` (ONNX Runtime), which
  doesn't compile to wasm32. But on audit the ONNX graph is just `MatMul ->
  (optional Add) -> LpNormalization` — i.e. a **linear projection**
  (`L2norm((feats @ W) + b)`); a fused f64-accumulate matmul kernel already
  existed in the pyo3 `native` crate, and the weights are already exported to
  `weights.npz`. So ONNX Runtime was overkill for one matmul. Extracted
  **`goldenembed-core`** (pyo3/ort/fs-free: char-n-gram featurizer + the
  `project` head) — the roadmap `*-core` pattern — which `goldenembed`
  re-exports (consumers untouched) and which compiles cleanly to wasm32. Then
  **`goldenembed-wasm`** + `goldenmatch/core/goldenembed-wasm` (an `Embedder`
  taking the projection weights as a `Float32Array`) runs the SAME kernel at the
  edge. **Cosine-tolerance parity** (not byte-identity — f32 accumulation order
  differs; the output feeds thresholded cosine blocking, and ONNX itself already
  differs from numpy at this scale): worst cosine distance **1.8e-7** vs the numpy
  reference, pinned in Rust (`goldenembed-core/tests/project_parity.rs`) and TS
  (`goldenembed-wasm.parity.test.ts`) golden harnesses. *(Done.)*
  - ✅ **Follow-up done: SQL surfaces no longer link ONNX Runtime.** `ort` is now
    a non-default `onnx` cargo feature; `goldenembed::load` reads `weights.npz`
    and `embed()` runs the native `project` matmul by default (the `model.onnx`/
    `ort` path only under `--features onnx`, for onnx-only deployments with no
    `weights.npz`). A `weights.rs` npz/npy reader (over the existing `zip` dep) +
    a committed model fixture + a native-load integration test
    (`goldenembed/tests/native_load.rs`, cosine < 1e-5 vs numpy) prove the whole
    chain. The 4 consumers (postgres / datafusion-udf / goldenhnsw / embed-py)
    keep the identical `goldenembed` dep and just stop pulling `ort` — smaller
    extensions, no per-process ONNX Runtime init. Bonus: the runtime now also
    loads **weights-only** models (no `model.onnx`), which the old ort path
    couldn't.

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

## Surface #5 — cloud-warehouse UDFs (BigQuery, Snowflake)

The four surfaces above (Python / edge-TS / DuckDB / Postgres) were the original
scope. Cloud warehouses are a **fifth** surface, and a cheap one: the committed
`*-wasm` kernels are *already* portable — reaching BigQuery/Snowflake is
**packaging, not porting** (no new Rust). The kernel travels base64-inline inside
a JS UDF and runs the exact committed bytes, so it's byte-identical to every
other surface by construction.

**Mechanism (verified 2026-07-04):**
- **BigQuery** — `CREATE FUNCTION … LANGUAGE js` runs V8 with WebAssembly; the
  base64 wasm is inlined in the body, instantiated **once per worker** (cached on
  `globalThis`, reused across rows). ~1 MB inline budget — all scalar kernels fit.
- **Snowflake** — JS UDFs can run wasm too, but the source-size cap (~100 KB) and
  mandatory full-inline mean only the *small* kernels fit (graph 37 KB, hnsw
  62 KB, sketch 65 KB, goldenembed 81 KB); fingerprint (156 KB base64) and up
  exceed it. Snowflake also already has a Python/Snowpark UDF path
  (`goldenmatch/snowflake/udfs.py`) — but that's CPython-in-a-box, **not** native
  per the bar above.

**The generator (Camp C — warehouse packaging):**
`packages/typescript/goldenmatch/scripts/generate_warehouse_udfs.mjs` reads a
committed `*WasmBytes.ts` + its wasm-bindgen glue and flattens them into a
self-contained UDF body: the async/`fetch`/`import.meta` init path is dropped
(only synchronous `new WebAssembly.Module`+`Instance` remains), ES `import`/
`export` are stripped, `TextEncoder`/`TextDecoder` are routed through inlined
UTF-8 polyfills (used only where the sandbox lacks them), and `console.*` is
removed. Output: committed, copy-paste-deployable `warehouse/bigquery/*.sql` (no
GCS bucket, no external library reference).

**Parity gate:** `tests/parity/warehouse-bigquery.parity.test.ts` extracts each
UDF's JS body verbatim from the shipped `.sql`, runs it in a fresh V8 realm
(`node:vm`, no Node globals) — both with host text codecs and with them deleted
(forcing the polyfills) — and asserts it reproduces the **shared golden oracle**
byte-for-byte. This validates the wasm+glue logic in BigQuery's engine family
(V8); it is a simulation, not a live-warehouse test (a one-off smoke query
confirms host acceptance — see `warehouse/README.md`). CI: a `warehouse_udf`
path filter + a pure-node regenerate-and-diff drift guard in the `typescript`
lane.

**Status:**
- ✅ **`goldenmatch_fingerprint`** (BigQuery) — `fingerprint-core::fingerprint_json`,
  the record-id hash. Ships the generator + Node/V8 parity harness + drift guard.
- ⬜ **`goldenmatch_score`** (BigQuery) — needs a `score_one(a,b,method)` scalar
  export on `score-wasm` (only NxN `score_matrix` today) + a committed base64
  blob; the flagship fuzzy scorer for in-warehouse `WHERE score(...) > t`.
- ⬜ **Snowflake** — the sub-100 KB kernels (sketch / hnsw / goldenembed / graph)
  via the same generator with a Snowflake DDL emitter.

## Reference PRs (the pattern in action)

- HNSW cross-surface: #1401 (TS/WASM + DuckDB + Postgres, one PR / 3 commits).
- goldencheck Rust-source-of-truth (edge reroute): #1403.
- CI guards that make parity self-enforcing: #1410.
- sketch / MinHash-LSH cross-surface: #1413 (the Camp-B→everywhere template).
- warehouse UDFs (surface #5, BigQuery fingerprint): the Camp-C packaging template.
