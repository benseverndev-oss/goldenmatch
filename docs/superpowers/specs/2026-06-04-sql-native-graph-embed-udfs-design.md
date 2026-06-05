# SQL-native graph + embedding UDFs — native-direct (DuckDB / Postgres / DataFusion)

- **Issue:** #509 (part of #504 Phase 5, SQL-native expansion)
- **Date:** 2026-06-04
- **Status:** Design approved (brainstorming); pending spec review + plan
- **Depends on:** #508 (`goldenembed-rs`, landed — PR #734); #504 native kernels (landed)

## Problem

#509 asks for warehouse-first ER primitives exposed directly in SQL:

- `goldenmatch_connected_components(...)` / `goldenmatch_pair_dedup(...)` backed by
  the native graph kernels — **not** the JSON-bridge round-trip.
- `goldenmatch_embed_local(text, model_path)` backed by `goldenembed-rs` — no
  Vertex/OpenAI network dependency inside the warehouse.
- Both backends kept in lockstep (DuckDB UDF + pgrx wrapper + handwritten SQL),
  tested on both, identical SQL outputs.

### The reframe: this is a rework, not greenfield

PR #503 (epic #504) **already shipped a first implementation** of all three UDFs
on both backends — but as exactly the anti-pattern #509 says to replace:

- **DuckDB** (`duckdb/goldenmatch_duckdb/core_kernels.py`): JSON-in/JSON-out
  scalar UDFs; `goldenmatch_embed_local` wraps the **Python in-house** embedder
  (`provider="inhouse"`), not `goldenembed-rs`.
- **Postgres** (`postgres/src/kernels.rs`): `#[pg_extern]` functions that wrap
  `goldenmatch_bridge::api::{connected_components, pair_dedup, embed_local}` —
  i.e. the **CPython JSON-bridge round-trip** (`pgrx → pyo3 → goldenmatch.native`).

The existing `goldenmatch_record_fingerprint` (pure-Rust, bridge-free — "the
decoupling lever" per its own doc comment) is the proven template for what these
UDFs should become.

**This worktree replaces the #503 placeholder with the native-direct contract.**

## Goals

- Graph UDFs call the native kernels **directly** (no embedded CPython on the
  Postgres/DataFusion path; no JSON round-trip on DuckDB).
- Columnar **Arrow** I/O on the Arrow-native engines (DuckDB, DataFusion);
  native PG arrays on Postgres. Same kernel logic, identical values.
- Accept **both** int64 record IDs (zero-mapping fast path) and **string** IDs
  (dictionary-mapped str↔i64 around the i64 kernel).
- `goldenmatch_embed_local` runs the **same `goldenembed-rs` ONNX kernel** on all
  surfaces (new pyo3 wheel for DuckDB; direct crate call for Postgres/DataFusion),
  matching the Python in-house path within tolerance.

## Non-goals

- No network embedding providers (Vertex/OpenAI) in SQL — out of scope by design.
- No streaming / stateful surfaces.
- No change to the existing `goldenmatch_record_fingerprint` contract.
- Backward compatibility with the #503 JSON-string UDF signatures — **clean break**
  (the placeholder just landed; same names are repointed to native-direct shapes).

## Scope decisions (from brainstorming)

| Decision | Choice |
|---|---|
| Scope | All three UDFs, **graph-first** build order |
| DuckDB embed backend | **New pyo3 binding** to `goldenembed-rs` (single-kernel lockstep) |
| Graph I/O encoding | **Arrow on DuckDB/DataFusion, native arrays on Postgres** |
| Record-ID handling | **Accept both** — int64 fast path + string dictionary mapping |
| Embed binding packaging | **New thin wrapper wheel** (`goldenmatch-embed`), keeps `goldenembed` pyo3-free |
| #503 JSON UDFs | **Replace in place** (clean break) |
| DataFusion surface | **Include** — third Arrow-native surface sharing `graph-core` |

## Architecture

### New component: `graph-core` (pyo3-free crate)

Mirrors the existing `score-core` precedent ("intentionally pyo3-free; the
`native` crate keeps thin `#[pyfunction]` shims that delegate here; the FFI UDFs
call these `pub fn`s").

Today the graph kernels live in the **pyo3-coupled** `native` crate
(`cluster.rs`, `pairs.rs` — `use pyo3`, `#[pyfunction]`, `PyArrowType`). To let
pgrx and DataFusion call them without CPython, extract the pure logic:

- `graph-core::connected_components(edges: &[(i64,i64,f64)], all_ids: &[i64]) -> Vec<Vec<i64>>`
- `graph-core::dedup_pairs_max_score(pairs: &[(i64,i64,f64)]) -> Vec<(i64,i64,f64)>`
- Columnar helpers over Arrow `Int64Array`/`Float64Array` (pure `arrow`, no
  `pyarrow`/pyo3) for the Arrow-native callers.

The `native` crate's `cluster.rs`/`pairs.rs` keep their `#[pyfunction]` +
`PyArrowType` shims but delegate to `graph-core` (behavior-exact; existing
native parity tests must still pass).

### New component: `goldenmatch-embed` (pyo3 wheel)

Thin pyo3/abi3 wrapper crate over `goldenembed`, packaged with maturin — mirrors
the `goldenmatch-native` split. Keeps `goldenembed` itself pyo3-free and confines
the heavy `ort`/ONNX runtime to this one wheel.

- Python API: `GoldenEmbed.load(dir) -> model`; `model.embed(texts: list[str]) -> list[list[float]]`.
- Loader/discovery parallel to `goldenmatch-native` (`goldenmatch_embed._embed`).
- New publish workflow (tag `goldenmatch-embed-v*`), versioned independently.

## Surface-by-surface contract

All three engines expose the same function names and produce **identical values**;
wire encoding is idiomatic per engine.

### `goldenmatch_connected_components`

Input: a candidate-pair set `(id_a, id_b, score)` + optional universe of ids.
Output: components (each a sorted list of member ids).

| Engine | Signature | Kernel path |
|---|---|---|
| DuckDB | Arrow columns `(id_a, id_b, score [, all_ids])`; returns list-of-lists | pyarrow → `graph-core` columnar |
| Postgres | `(id_a bigint[]/text[], id_b ..., score float8[] [, all_ids ...])` | PG arrays → `graph-core` (pure Rust) |
| DataFusion | Arrow UDF over `(id_a, id_b, score)` | `arrow` arrays → `graph-core` |

### `goldenmatch_pair_dedup`

Input: `(id_a, id_b, score)`. Output: canonical pairs, max score per `{a,b}`.
Same per-engine encoding as above.

### ID handling (accept both)

- **int64 columns/arrays** → straight into the kernel (zero mapping).
- **Utf8/dictionary (DuckDB/DataFusion) or `text[]` (Postgres)** → wrapper builds
  a deterministic str↔i64 dictionary, runs the i64 kernel, maps results back to
  the original string ids. Dictionary build is parity-tested.

### `goldenmatch_embed_local(text, model_path)`

`model_path` = a saved in-house model directory (ONNX + featurizer config).

| Engine | Backend |
|---|---|
| DuckDB | new `goldenmatch-embed` wheel (goldenembed-rs ONNX) |
| Postgres | `goldenembed` crate **directly** (pure Rust, no CPython bridge) |
| DataFusion | existing `goldenmatch_embed` UDF (`embed_udf.rs`) already does this |

Returns the embedding vector (float array / `FixedSizeList<Float32>` on Arrow).

## Data flow (graph UDF, DuckDB, string ids)

```
SQL: SELECT goldenmatch_connected_components(id_a, id_b, score) ...
  → DuckDB hands Arrow Utf8/Int64 columns to the Python UDF (zero-copy via pyarrow)
  → wrapper: if Utf8/dict → build str↔i64 dict; else pass int64 through
  → graph-core columnar kernel (pure Rust) computes components on i64
  → wrapper maps i64 components back to original ids
  → return Arrow list<list<id>>
```

No JSON parse, no per-row Python, no CPython interpreter spin-up on the kernel.

## Error handling

- **Type errors** (wrong Arrow type, malformed array): raise a clear engine-native
  error (DuckDB exception / `pgrx::error!` / DataFusion `Execution`), naming the
  function + offending column — mirrors the existing kernel `PyValueError` messages.
- **Embed errors** (missing model dir, bad ONNX): surfaced as the engine-native
  error; the embed model lock-poison path already handled in `embed_udf.rs`.
- The #503 fail-soft `{"error": ...}` JSON convention is **dropped** for these
  functions (clean break to typed columnar I/O; errors become real SQL errors).

## Testing strategy

| Layer | Where | Notes |
|---|---|---|
| `graph-core` units | local `cargo test` | pyo3-free; int64 + edge cases + dict mapping |
| `native` shims still parity | CI (needs goldenmatch Python) | existing tests must stay green after delegation |
| DuckDB graph UDFs | **local** pytest | Arrow int64 + string-dict cases; no ONNX needed |
| DuckDB embed UDF | **CI-only** | `ort` won't link locally on Windows; `cargo check` locally |
| Postgres (all) | **CI-only** | pgrx needs libclang/Linux; PG 15/16/17 lanes |
| DataFusion UDFs | CI (`cargo test`) | Arrow end-to-end |
| Cross-backend parity | CI | DuckDB value == PG value == DataFusion value == kernel; embed == Python in-house within tolerance |

Local-build constraint: the embed half is CI-validated on Windows (per
`feedback_ort_onnxruntime_no_local_link`). Graph half is fully local-testable on
DuckDB + `graph-core`.

## Versioning / packaging

- `graph-core`: new internal crate (path dep of `native`, `postgres`, `datafusion-udf`).
- `goldenmatch-embed`: new pyo3 wheel `0.1.0` + publish workflow (`goldenmatch-embed-v*`).
- `goldenmatch-duckdb`: `0.5.0 → 0.6.0` (Arrow graph UDFs, embed via wheel).
- `goldenmatch_pg`: `0.5.0 → 0.6.0` — new handwritten `sql/goldenmatch_pg--0.6.0.sql`
  + `--0.5.0--0.6.0.sql` upgrade script (drops old JSON signatures, creates new).
- `datafusion-udf`: graph UDFs added (version bump per its own scheme).

## Build sequence (graph-first)

1. Extract `graph-core` (pyo3-free) + rewire `native` `#[pyfunction]` shims; native parity green.
2. DuckDB graph UDFs → Arrow columnar (accept-both ids).
3. Postgres graph UDFs → native-direct over PG arrays (replace bridge `kernels.rs`).
4. DataFusion graph UDFs (Arrow) over `graph-core`.
5. Cross-backend graph parity tests.
6. `goldenmatch-embed` wheel + publish workflow.
7. DuckDB embed UDF → wheel; Postgres embed UDF → direct `goldenembed` crate.
8. Embed parity tests (vs Python in-house within tolerance).

## Acceptance (from #509)

- `goldenmatch_connected_components` / `goldenmatch_pair_dedup` available + tested
  on DuckDB and Postgres (and DataFusion), native-direct (no JSON-bridge).
- `goldenmatch_embed_local` produces vectors matching the Python in-house path
  within tolerance on every surface, backed by `goldenembed-rs`.
- Identical SQL outputs across backends; both-backend tests green in CI.

## Risks / open items

- **`graph-core` extraction must be behavior-exact** — the union strategy is
  irrelevant to component membership (documented in `cluster.rs`), but the
  `build_clusters_arrow` path has more surface; keep delegation thin and rerun all
  native parity tests.
- **`goldenmatch-embed` wheel packaging** — bundling the ONNX runtime in a maturin
  wheel across platforms is the same multi-platform-publish work `goldenmatch-native`
  faced; reuse that workflow shape. CI-only validation on Windows.
- **str↔i64 dictionary determinism** — must be stable so DuckDB and PG agree on
  component grouping when ids are strings (grouping is order-independent, but the
  returned ids must round-trip exactly).
- **PG `text[]` ergonomics** — large pair sets as PG arrays; confirm acceptable vs
  a table-reading variant (deferred; arrays match the lockstep contract).
