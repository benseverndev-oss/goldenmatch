# 0005 — SQL graph + embedding UDFs go native-direct (replace the bridge)

**Status:** accepted (2026-06-05, Ben) • **Spec:** `docs/superpowers/specs/2026-06-04-sql-native-graph-embed-udfs-design.md` • **Shipped:** PRs #740 / #743 / #745

## Context
Issue #509 asked for warehouse-first ER primitives in SQL: `connected_components` /
`pair_dedup` over the native kernels (NOT the JSON-bridge round-trip) and
`embed_local` over `goldenembed-rs` (no network). PR #503 had already shipped all
three — but through the embedded-CPython JSON bridge, i.e. the exact thing the issue
says to avoid. So #509 is a **rework of the placeholder into the native-direct
contract**, with the existing pure-Rust `goldenmatch_record_fingerprint` as the
proven template. Six scoping forks were put to Ben; he chose the ambitious option on
each.

## Decision
1. **Native-direct, drop the bridge.** Graph fns call a new pyo3-free `graph-core`
   crate; embed calls `goldenembed-rs` directly. No embedded CPython on the
   Postgres/DataFusion path. The dead `bridge::api` fns are deleted.
2. **One shared kernel (`graph-core`),** mirroring `score-core` — consumed by the
   `native` pyo3 shims, the pgrx ext, and the DataFusion FFI crate.
3. **Arrow where it's free, native arrays where it isn't.** Columnar Arrow I/O on
   DuckDB + DataFusion; native PG arrays on Postgres. Same kernel, identical values.
4. **Accept-both ids** — int64 fast path + string `_str` siblings (first-seen
   `str↔i64` dictionary). Separate names because DuckDB rejects same-name overloading.
5. **New `goldenmatch-embed` wheel** for the DuckDB embed path — a thin pyo3 wrapper
   over `goldenembed`, keeping that crate pyo3-free and confining `ort`/ONNX to one wheel.
6. **Clean break + DataFusion expansion** — the #503 JSON-string signatures are
   replaced in place (0.5.0→0.6.0), and DataFusion is added as a third Arrow-native
   surface beyond #509's named DuckDB+Postgres.

## Consequences / honest flags
- **CI-only verification** for most of it (Windows can't link `ort`/libclang) — the
  graph half is locally testable, the embed + DataFusion halves are CI-gated.
- **Per-engine wire shapes diverge** (Arrow vs PG arrays/tables; PG embed returns
  `float8[]` while DuckDB returns JSON). Values are identical — that's the contract.
- **DuckDB embed now needs the optional `goldenmatch-embed` wheel + `onnx`** (the
  model's `model.onnx` export is onnx-gated); the happy-path test `importorskip`s both.
- **DataFusion embed keeps its env-var construct-time model** (`goldenmatch_embed`),
  not a per-call `model_path` — a documented divergence, not full lockstep.

## Alternatives not taken
- Keep the #503 JSON bridge (declined — it's the anti-pattern #509 names).
- DuckDB embed via the Python in-house path instead of a goldenembed-rs wheel
  (declined — chose true single-kernel lockstep across surfaces).
- Uniform Arrow on both backends incl. a PG Arrow↔array bridge (declined — PG isn't
  columnar; native arrays for zero real gain).
- DuckDB + Postgres only (declined — added DataFusion as the third surface).

---
**Classification:** decision/accepted • **Last updated:** 2026-06-05
