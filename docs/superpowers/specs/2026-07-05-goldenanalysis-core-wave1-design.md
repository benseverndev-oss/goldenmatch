# GoldenAnalysis Rust cutover — Wave 1 (frame-stat kernels) — design

**Status:** approved (brainstorm 2026-07-05), pending spec review
**Thesis:** pure-Rust `-core` kernels + Arrow zero-copy + smart-pipe/dumb-kernels +
scaffolding-cutover (keep the proven Python `_*_pure` as the byte-identical
reference, translate to Rust, prove parity, then it's just the fallback).
**Context:** `analysis-core` (pyo3-free) + `analysis-native` (pyo3 shim, Arrow-in) +
`analysis-wasm` already exist with `histogram`/`quantile` as the proven toehold
(measured 5.8–9.9× on Linux incl. Arrow conversion). This is Wave 1 of the 4-wave
GoldenAnalysis cutover. Related: `project_goldenanalysis_roadmap`,
`project_wasm_acceleration_fold`, goldenflow-core (the pattern).

## 1. What Wave 1 cuts over

The three `core/aggregate.py` frame-stat kernels the analyzers rely on that are
still **pure-Python only** (no native path, unlike histogram/quantile):
- `null_ratio_per_column(df) -> dict[str, float]` — per-column null fraction.
- `duplicate_row_ratio(df) -> float` — fraction of rows in an exact-duplicate group
  (size ≥ 2), counting every member. Empty frame → 0.0.
- `distinct_count(series) -> int` — **new** `aggregate.py` function extracting the
  `df[col].n_unique()` that `frame_summary` currently calls inline (polars
  semantics: null counts as one distinct value). `frame_summary` is refactored to
  call `agg.distinct_count`.

These feed `frame_summary` (the foundational analyzer) and `cluster_dist`.

## 2. The Arrow-in-core departure (deliberate, thesis-aligned)

The existing `analysis-core` kernels are pure-slice (`histogram(&[f64], i64)`) — the
shims convert Arrow→slice at the boundary. The frame kernels are **multi-column and
heterogeneous-type** (null_count over any column type; row-hashing across all
columns; distinct over any type), which cannot reduce to `&[f64]`. So Wave 1
introduces **Arrow into `analysis-core`** for these kernels — exactly pillar 3
(arrow-rs as the universal layout, as goldenflow's `native-flow` `*_arrow` kernels
already do). `analysis-core` gains an `arrow` dep (default-features-off, minimal
features); the pure numeric kernels (histogram/quantile) stay slice-based.

## 3. Design

### 3.1 `analysis-core` — 3 pure-Rust, Arrow-in kernels

```rust
use arrow::array::{Array, RecordBatch};
use arrow::row::{RowConverter, SortField};

/// Per-column null fraction; empty batch => 0.0 for every column.
pub fn null_ratio_per_column(batch: &RecordBatch) -> Vec<(String, f64)> {
    let n = batch.num_rows();
    batch.schema().fields().iter().zip(batch.columns()).map(|(f, col)| {
        let r = if n == 0 { 0.0 } else { col.null_count() as f64 / n as f64 };
        (f.name().clone(), r)
    }).collect()
}

/// Fraction of rows in an exact-duplicate group (size >= 2). Empty => 0.0.
/// Row-encodes all columns (arrow RowConverter) and counts rows whose encoding
/// appears >= 2 times. Null-equality matches polars is_duplicated (nulls equal).
pub fn duplicate_row_ratio(batch: &RecordBatch) -> f64 { /* RowConverter over all
    columns -> HashMap<Row, count>; sum counts where count>=2; / num_rows */ }

/// Distinct value count for one column (nulls: one distinct value if present),
/// matching polars n_unique. Row-encodes the single column and counts unique rows.
pub fn distinct_count(array: &dyn Array) -> i64 { /* RowConverter over [array] ->
    HashSet<Row>.len() */ }
```
Dependency-free of pyo3/polars; only `arrow`. Unit tests in the crate cover empty
batch, all-null column, dup groups, null-as-distinct.

### 3.2 `analysis-native` (pyo3) — Arrow shims

`histogram`/`quantile` take a single `PyArrowType<ArrayData>`. The frame kernels
take a **RecordBatch** — `PyArrowType<RecordBatch>` (arrow-rs's pyarrow bridge
supports it, zero-copy). `distinct_count` takes a single `PyArrowType<ArrayData>`
(any type). Three new `#[pyfunction]`s registered in the pymodule, delegating to
`analysis_core::*`. (The `native_symbols` gate — just rolled to goldenanalysis —
reconciles the 3 new exports automatically; the `aggregate.py` `native_module().X`
call-sites must match.)

### 3.3 `aggregate.py` — dispatch (mirror histogram/quantile exactly)

Each kernel gets the established shape:
```python
def null_ratio_per_column(df):
    if native_enabled("null_ratio_per_column"):
        return _null_ratio_per_column_native(df)
    return _null_ratio_per_column_pure(df)
```
`_*_pure` = the current bodies, renamed (the byte-identical reference). `_*_native`
converts the frame to an Arrow RecordBatch (`df.to_arrow()`) and calls
`native_module().<sym>`, mapping the result back to the dict/float/int the callers
expect. Add `distinct_count` (new) with `_distinct_count_pure` matching
`series.n_unique()`; refactor `frame_summary` to use `agg.distinct_count(df[col])`.

### 3.4 Gating (`_native_loader.py`)

Add the 3 symbols to `_GATED_ON` and the symbol map (alongside histogram/quantile).
Default `GOLDENANALYSIS_NATIVE=auto` runs them native iff the built kernel exports
them; pure fallback otherwise.

## 4. Parity gate (the scaffolding-cutover proof)

Extend `tests/core/test_native_parity.py`: for fixtures (empty frame, all-null
column, mixed-type frame with dup rows, high-cardinality + low-cardinality columns,
a null-bearing column), assert `_native(...) == _pure(...)`:
- `null_ratio_per_column`, `duplicate_row_ratio`: exact `==` (integer counts / a
  single deterministic division — no float non-associativity).
- `distinct_count`: exact `==` (integer).
The parity run needs the built kernel → CI native lane (like histogram/quantile's
existing parity). Box-safe part: the `_pure` functions + the dispatch wiring +
`frame_summary` still producing the same output through the pure path.

## 5. Scope boundaries (deliberate)

- **WASM deferred to Wave 1b.** `analysis-wasm` stays on histogram/quantile
  (`&[f64]`). The frame kernels need RecordBatch-in-wasm (arrow-rs in wasm is heavy,
  in tension with pillar-1 "lean WASM", and the box can't build/test wasm). The
  Python+native surface — the measured-fast one — is Wave 1's value; the wasm
  frame-parity surface is a scoped follow-on.
- **Only the 3 aggregate frame kernels.** cluster-dist math, match_rates,
  quality_rollup, cross-run regressions are Waves 2–4.
- **No orchestration change** — registry/narrative/render/history stay host (the
  "smart pipe").

## 6. Rollout / docs

- Single PR, branch `feat/goldenanalysis-core-wave1` off `origin/main`. Rust (core +
  native) + Python (aggregate dispatch, frame_summary refactor, gating) + parity
  test + a2a/native-gate manifests if the export set changes (native_symbols
  goldenanalysis manifest — the 3 new exports are auto-reconciled; verify the gate
  stays green). benzsevern gh; merge-queue → arm auto-merge, stop.
- **Build note:** the box cannot reliably `cargo build` analysis-core/native (and
  not wasm) — the Rust kernels are written + read-verified; the native parity RUN is
  CI (the goldenanalysis native lane), like the existing histogram/quantile parity.
  Box-safe: `_pure` reference + dispatch + the pure-path analyzer output + Rust unit
  tests authored (compiled in CI).
- CLAUDE.md / goldenanalysis docs: note Wave 1 kernels + the Arrow-in-core step.
- ruff on touched Python (#1451 lesson).

## 7. Risks

- **duplicate_row_ratio null-equality + type coverage** — polars `is_duplicated`
  treats nulls as equal and hashes across mixed types; the RowConverter encoding
  must match (nulls equal; all Arrow types the analyzers pass — numeric, string,
  bool, categorical). The mixed-type + null fixtures in §4 are the guard; if an
  Arrow type isn't RowConverter-encodable, document it as a pure-only column type
  (the dispatch can fall back per-frame).
- **`df.to_arrow()` chunking** — polars may hand back a chunked RecordBatch; the
  kernel must handle multi-chunk (combine or iterate). Fixture with a chunked frame.
- **Arrow-in-core weight** — adds `arrow` to `analysis-core`'s deps; keep
  default-features off with only what RowConverter/RecordBatch need, so the crate
  stays lean (and the future wasm story remains feasible).
- **Parity float-exactness** — the ratios are single divisions of exact integer
  counts → deterministic; `==` is safe (no tolerance needed), unlike a summed-float
  kernel.
