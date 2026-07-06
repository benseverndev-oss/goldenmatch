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

## 2. Architecture — mirror `goldencheck-native/keys.rs` (arrow-free core + intern in native)

**Prior art solves this already.** `goldencheck-native/keys.rs` row-hashes across
heterogeneous columns for `composite_key_search`/`discover_functional_dependencies`
via the same shape these kernels need: the shim decodes each Arrow column and
**interns** its values to `u64` ids (`intern_column`), and the pyo3-free
`goldencheck-core` operates on `&[u64]` columns — arrow lives ONLY in `-native`.
Wave 1 follows this exactly, NOT the (rejected) Arrow-in-core + `RowConverter`
approach:
- **`analysis-core` stays arrow-free** (lean, wasm-trivial — pillar 1) — kernels
  operate on interned `u64` columns.
- **`analysis-native` interns** Arrow columns → `u64` (reusing/adapting keys.rs
  `intern_column`), WITH float canonicalization to match polars (below).
- Cross-column identity is a tuple of per-column `u64` ids. Null → id 0 (matches
  polars: nulls equal, null = one distinct). Unsupported dtypes → `PyTypeError`
  (like keys.rs), caught in Python → pure fallback (§3.3).
- **`null_ratio_per_column` needs NO core kernel** — the shim computes
  `null_count / n` per column directly (`Array::null_count()`).

**Float canonicalization (empirically determined from polars, box-verified):**
polars folds `-0.0 == +0.0` (`n_unique([-0.0,0.0])==1`) and all `NaN` to one
(`n_unique([nan,nan])==1`); nulls are one distinct value. So the intern maps:
```rust
fn canon_f64_bits(x: f64) -> u64 {
    if x.is_nan() { CANON_NAN }          // all NaN -> one id
    else if x == 0.0 { 0.0f64.to_bits() } // -0.0 and +0.0 -> +0.0 (x==0.0 catches both)
    else { x.to_bits() }
}
```
(keys.rs's `to_bits` alone does NOT canonicalize `-0.0`/`NaN` — this is the added
step the review flagged; it's what makes byte-parity hold on float columns.)

## 3. Design

### 3.1 `analysis-core` — 2 pure-Rust kernels on interned `u64` (arrow-free)

```rust
use std::collections::{HashMap, HashSet};

/// Fraction of rows in an exact-duplicate group (size >= 2). Empty => 0.0.
/// `columns`: interned u64 ids, one Vec per column, all length n_rows.
pub fn duplicate_row_ratio(columns: &[Vec<u64>], n_rows: usize) -> f64 {
    if n_rows == 0 { return 0.0; }
    let mut counts: HashMap<Vec<u64>, usize> = HashMap::new();
    for i in 0..n_rows {
        let row: Vec<u64> = columns.iter().map(|c| c[i]).collect();
        *counts.entry(row).or_insert(0) += 1;
    }
    let dup: usize = counts.values().filter(|&&c| c >= 2).sum();
    dup as f64 / n_rows as f64
}

/// Distinct value count for one interned column (null id counts as a value).
pub fn distinct_count(column: &[u64]) -> i64 {
    column.iter().copied().collect::<HashSet<u64>>().len() as i64
}
```
No pyo3/arrow/polars. Unit tests: empty, all-same, dup group of 3 (contributes 3),
null-id-as-distinct, single-column dup.

### 3.2 `analysis-native` (pyo3) — intern + shims (`Vec<PyArrowType<ArrayData>>`)

Adapt `goldencheck-native/keys.rs::intern_column(PyArrowType<ArrayData>) -> Vec<u64>`
into analysis-native (or a shared helper), adding `canon_f64_bits` for floats. Three
`#[pyfunction]`s (NOT `PyArrowType<RecordBatch>` — polars `.to_arrow()` on a frame
returns a `pyarrow.Table` which won't bridge; the proven pattern is per-column
`ArrayData`):
- `duplicate_row_ratio(cols: Vec<PyArrowType<ArrayData>>) -> PyResult<f64>` — intern
  each col (all same len), call `analysis_core::duplicate_row_ratio`.
- `distinct_count(col: PyArrowType<ArrayData>) -> PyResult<i64>` — intern, call core.
- `null_ratio_per_column(cols: Vec<PyArrowType<ArrayData>>) -> PyResult<Vec<f64>>` —
  per-col `null_count / n` (no core kernel); returns ratios in column order.
Registered in the pymodule (the `native_symbols` gate reconciles the 3 new exports;
the `aggregate.py` `native_module().X` call-sites must match).

### 3.3 `aggregate.py` — dispatch with dtype fallback (mirror histogram/quantile)

```python
def null_ratio_per_column(df):
    if native_enabled("null_ratio_per_column"):
        try:
            return _null_ratio_per_column_native(df)
        except (TypeError, ValueError):   # unsupported dtype -> proven fallback
            pass
    return _null_ratio_per_column_pure(df)
```
`_*_pure` = the current bodies, renamed (the byte-identical reference). `_*_native`
builds `[df[c].to_arrow() for c in df.columns]` (each a single-chunk `pyarrow.Array`
— chunk-safe, no `combine_chunks()` needed) and calls `native_module().<sym>`,
mapping back to the dict/float/int the callers expect (null_ratio: zip returned
ratios with `df.columns`). Add `distinct_count` (new) with `_distinct_count_pure`
matching `series.n_unique()`; refactor `frame_summary` to `agg.distinct_count(df[col])`.
The `try/except → pure` is load-bearing: analyzers pass arbitrary frames
(List/Struct/Categorical/Decimal) that `intern_column` rejects; native-enabled must
degrade to pure, not raise.

### 3.4 Gating (`_native_loader.py`)

Add the 3 symbols to **`_COMPONENT_SYMBOLS`** (the load-bearing map — under
`GOLDENANALYSIS_NATIVE=auto`, dispatch runs native iff the symbol is exported) AND
to `_GATED_ON` (now byte-exact sign-off documentation). `aggregate.py`'s
`native_enabled("X")` names must match the `_COMPONENT_SYMBOLS` keys.

## 4. Parity gate (the scaffolding-cutover proof)

Extend `tests/core/test_native_parity.py`, asserting `_native(...) == _pure(...)`
across fixtures — including the ones the review flagged as the real risk:
- empty frame; all-null column; a null-bearing column (null = one distinct / nulls
  equal).
- mixed-type frame (str/int/float/bool) with exact-duplicate rows (dup group of ≥3).
- high- and low-cardinality columns.
- **FLOAT-EDGE (load-bearing):** a column containing `-0.0`, `+0.0`, `NaN`, and
  duplicate `NaN`-bearing rows — the canonicalization must reproduce polars
  (`n_unique([-0.0,0.0])==1`, `n_unique([nan,nan])==1`; dup rows on `-0.0`/`+0.0` and
  on `NaN`). This is the fixture whose absence would let a silent divergence ship.
- **UNSUPPORTED-DTYPE:** a frame with a `List`/`Struct`/`Categorical`/`Decimal`
  column — assert the native-enabled path returns the SAME result as pure (i.e. the
  `try/except → pure` fallback fired, no raise).
Exactness: `null_ratio_per_column`/`duplicate_row_ratio`/`distinct_count` are integer
counts / a single deterministic division → exact `==`, no tolerance. The parity RUN
needs the built kernel → CI goldenanalysis native lane (like histogram/quantile).
Box-safe: the `_pure` reference + dispatch wiring + `frame_summary` pure-path output
+ the authored Rust unit tests (compiled in CI). Also add a Python test that the
`_native` dtype-fallback returns the pure result for an unsupported-dtype frame
(exercises the try/except without the kernel).

## 5. Scope boundaries (deliberate)

- **WASM deferred to Wave 1b.** `analysis-wasm` stays on histogram/quantile
  (`&[f64]`). The frame kernels need Arrow-column interning in a wasm shim (heavier,
  and the box can't build/test wasm). The
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
- CLAUDE.md / goldenanalysis docs: note Wave 1 kernels + the intern-in-native / arrow-free-core split (mirrors goldencheck-native/keys.rs).
- ruff on touched Python (#1451 lesson).

## 7. Risks (post-review — the big ones are resolved by mirroring keys.rs)

- **Float canonicalization (RESOLVED in design, must be fixtured)** — polars folds
  `-0.0→+0.0` and all `NaN`→one; `canon_f64_bits` (§2) reproduces this. The
  float-edge fixture (§4) is the proof; without it a divergence ships silently.
- **Unsupported dtypes (RESOLVED in design)** — `intern_column` raises on non
  str/int/float/bool; the `try/except → _pure` (§3.3) degrades instead of raising.
  The unsupported-dtype fixture (§4) guards it. (Trade-off: those column types run
  pure even when native is enabled — acceptable; they're rare in analysis frames and
  the result is identical.)
- **FFI bridge (RESOLVED)** — `Vec<PyArrowType<ArrayData>>` from per-column
  `Series.to_arrow()` (single-chunk `pyarrow.Array`) is the proven keys.rs pattern;
  no `pyarrow.Table`/RecordBatch, so the `.to_arrow()`-chunking risk is gone.
- **intern semantics drift from keys.rs** — reusing keys.rs's intern means inheriting
  its type handling; verify its string/int/bool interning matches polars n_unique for
  those types too (int/bool are exact; string interning by value is exact). The
  mixed-type fixture covers it.
- **CI-only kernel build** — the box can't build analysis-core/native; the parity RUN
  is CI. The float-edge + unsupported-dtype semantics are the risk that can't be
  locally proven against the built kernel — the fixtures are authored to fail loudly
  in CI if the canonicalization is off. (polars' float folding was box-verified up
  front, so the canonicalization target is known, not guessed.)
