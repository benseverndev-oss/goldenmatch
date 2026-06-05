# SQL-Native Extensions (graph + embedding UDFs, native-direct)

The warehouse-first SQL surface: GoldenMatch's graph kernels and the local embedder
exposed as UDFs on **DuckDB, Postgres, and DataFusion**, computed **native-direct** —
pure Rust, no embedded-CPython JSON bridge. The SQL sibling of the Python toolkit:
the same entity-resolution primitives, callable from the warehouse.

**Status:** SHIPPED — issue #509 fully delivered across three PRs (#740 graph half,
#743 embed half, #745 DataFusion). **Spec:**
`docs/superpowers/specs/2026-06-04-sql-native-graph-embed-udfs-design.md`. **Plan:**
`docs/superpowers/plans/2026-06-04-sql-native-graph-embed-udfs.md`. **Decision:**
[../decisions/0005-sql-native-direct-udfs.md](../decisions/0005-sql-native-direct-udfs.md).
**Code-level notes:** `packages/rust/extensions/CLAUDE.md`.

## The reframe (why #509 was a rework, not greenfield)
PR #503 had already shipped all three UDFs — but as exactly the anti-pattern #509
names: **JSON-in/JSON-out through the embedded-CPython bridge** (`pgrx → pyo3 →
goldenmatch.native`) and `embed_local` over the Python in-house path. #509 replaced
that placeholder with the native-direct contract. The pre-existing
`goldenmatch_record_fingerprint` (pure-Rust, bridge-free) was the proven template.

## The shared kernel: `graph-core`
A new **pyo3-free** crate `packages/rust/extensions/graph-core` (mirrors the
`score-core` / `fingerprint-core` precedent): `connected_components`,
`dedup_pairs_max_score`, a first-seen `str↔i64` `Dict`, and Arrow columnar entry
points. ONE source of truth consumed by all three surfaces:
- the `native` pyo3 ext keeps thin `#[pyfunction]` shims delegating to it (+ a new
  `connected_components_arrow`);
- the pgrx `postgres` ext calls it directly in pure Rust;
- the `datafusion-udf` FFI crate calls its **arrow-free slice kernels** (it is
  arrow-58 while graph-core is arrow-55 — passing only `Vec`s sidesteps the mismatch).

## The embedding kernel: `goldenmatch-embed` wheel
`goldenmatch_embed_local` runs `goldenembed-rs` (the pyo3-free ONNX embed runtime,
#508 — no network, no torch). A new maturin/abi3 wheel
`packages/rust/extensions/embed-py` wraps it for Python (DuckDB), confining `ort`/ONNX
to that one wheel and keeping `goldenembed` pyo3-free. Postgres + DataFusion call the
`goldenembed` crate directly.

## Per-surface contract (identical values, idiomatic encoding)

| Function | DuckDB | Postgres | DataFusion |
|---|---|---|---|
| `goldenmatch_connected_components` | Arrow list cols → `BIGINT[][]` (+ `_str`) | `bigint[]`→`TABLE(component,member)` (+ `_str`) | FFI `List` cols → `List<List<Int64>>` |
| `goldenmatch_pair_dedup` | list cols → `STRUCT(a,b,s)[]` (+ `_str`) | `bigint[]`→`TABLE(a,b,s)` (+ `_str`) | FFI → `List<Struct<a,b,s>>` |
| `goldenmatch_embed_local` | `goldenmatch-embed` wheel → JSON array | `goldenembed` crate → `float8[]` | existing env-var `goldenmatch_embed` (see divergence) |

- **Accept-both ids:** int64 on the bare name; strings on a `_str` sibling (first-seen
  `Dict` maps `str↔i64` around the kernel). DuckDB rejects same-name overloading, so the
  `_str` split is explicit on every surface for cross-backend name parity.
- **Wire encoding is per-engine** (Arrow on DuckDB/DataFusion, native arrays/tables on
  Postgres) — the *values* are identical, which is the lockstep contract (already true
  for the older JSON-vs-table-returning functions).
- **Versions:** `goldenmatch_pg` + `goldenmatch-duckdb` 0.5.0 → **0.6.0** (new handwritten
  `sql/goldenmatch_pg--0.6.0.sql` + `--0.5.0--0.6.0.sql` upgrade; 0.5.0 was a released
  tag so the bump was mandatory). `goldenmatch-embed` 0.1.0 + `goldenmatch-embed-v*`
  publish workflow. `graph-core` is an internal path-dep crate.

## Relationship to existing code
- **Replaces** the #503 bridge graph/embed functions; the now-dead
  `goldenmatch_bridge::api::{connected_components, pair_dedup, embed_local}` were removed.
- **Parallels** `score-core` / `fingerprint-core` (the existing pyo3-free shared crates).
- The graph kernels are the SQL-callable form of the Python `core/cluster.py` +
  `core/pairs.py` primitives (behavior-exact ports, CI parity-gated).

## Verification
- **Local:** `graph-core` `cargo test` (pyo3-free, 7 tests); DuckDB graph + cross-backend
  parity pytest (DuckDB value == native kernel).
- **CI-only (Windows can't link `ort`/libclang):** Postgres PG 15/16/17 (`cargo pgrx` +
  `CREATE EXTENSION` on the 0.6.0 surface + `pg_test`); `embed_wheel` lane (maturin build +
  load/embed the `tiny_model` fixture + best-effort in-house cosine parity); the
  datafusion FFI lane (`test_datafusion_ffi_udf.py`).

## Known follow-ups (non-blocking, not part of #509 acceptance)
- DataFusion `_str` (string-id) graph variants — int64-only this pass.
- DataFusion embed name/arg lockstep: its `goldenmatch_embed` loads the model from
  `GOLDENEMBED_MODEL_DIR` at construction, not a per-call `model_path` (doesn't fit a
  construct-time-model ScalarUDF) — a documented divergence, not a gap.

---
**Classification:** architecture/shipped • **Last updated:** 2026-06-05
