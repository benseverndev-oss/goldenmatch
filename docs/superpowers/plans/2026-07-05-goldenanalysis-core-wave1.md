# GoldenAnalysis Cutover Wave 1 — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move 3 `aggregate.py` frame-stat kernels into the Rust `analysis-core`/`analysis-native` split (mirroring goldencheck-native/keys.rs), dispatched from Python, parity-gated `_native == _pure`.

**Architecture:** `analysis-core` (arrow-free) gains 2 kernels on interned `u64` columns; `analysis-native` interns Arrow columns → `u64` (adapt keys.rs `intern_column` + `canon_f64_bits`) and exposes 3 `#[pyfunction]`s taking `Vec<PyArrowType<ArrayData>>`; `aggregate.py` dispatches `native_enabled → _native (try/except → _pure)`. Python is the byte-identical reference.

**Tech Stack:** Rust (arrow-rs, pyo3), Python (polars, pytest), maturin.

**Spec:** `docs/superpowers/specs/2026-07-05-goldenanalysis-core-wave1-design.md`

**The 3 kernels:** `null_ratio_per_column` (shim-only: `null_count/n` per col), `duplicate_row_ratio` (core kernel on interned cols), `distinct_count` (core kernel on one interned col; **new** aggregate fn, replaces `df[col].n_unique()` in frame_summary).

**Polars float semantics (box-verified — the canon target):** `-0.0`→`+0.0`, all `NaN`→one, null = one distinct. `canon_f64_bits`: `if x.is_nan(){CANON_NAN} else if x==0.0 {0.0f64.to_bits()} else {x.to_bits()}`.

**Anchors (verified):** `goldencheck-native/src/keys.rs:19` `intern_column(ArrayData)->PyResult<Vec<u64>>` (Utf8/Int/UInt/Float/Bool, null→0, `intern_primitive!` macro; floats use `to_bits` at :114/:119 — ADD canon). `analysis-native/src/lib.rs`: `read_f64` :21, pyfunctions :42-51, `#[pymodule]` :56 (`add_function` :59-60). `analysis-core/src/lib.rs`: `histogram` :20, `quantile` :56. `aggregate.py`: `null_ratio_per_column` :24, `duplicate_row_ratio` :32, dispatch pattern :44-55. `_native_loader.py`: `_GATED_ON` :41, `_COMPONENT_SYMBOLS` :43-48. `frame_summary.py:67` (`df[col].n_unique()`). `tests/core/test_native_parity.py` (skips when unbuilt).

**Environment / SOP:**
- Branch `feat/goldenanalysis-core-wave1` off `origin/main`.
- **The box cannot reliably `cargo build`/maturin the Rust** → Rust tasks are written + read-verified; the native parity RUN is CI (goldenanalysis native lane). Python `_pure` + dispatch + fallback + `frame_summary` pure-path + the parity-test *structure* are box-safe: `PYTHONPATH=packages/python/goldenanalysis POLARS_SKIP_CPU_CHECK=1 GOLDENANALYSIS_NATIVE=0 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest ...` (`NATIVE=0` forces the pure path).
- Run **ruff** on touched Python (#1451 lesson). benzsevern gh; merge-queue → arm + stop.

---

## Task 1: `analysis-core` — 2 arrow-free kernels + unit tests (CI-built)

**Files:** `packages/rust/extensions/analysis-core/src/lib.rs`

> The box can't build Rust — write + read-verify; CI compiles + runs the `#[test]`s.

- [ ] **Step 1: Add the 2 kernels** (after quantile):
```rust
use std::collections::{HashMap, HashSet};

/// Fraction of rows in an exact-duplicate group (size >= 2). Empty => 0.0.
/// `columns`: interned u64 ids, one Vec per column, each length `n_rows`.
pub fn duplicate_row_ratio(columns: &[Vec<u64>], n_rows: usize) -> f64 {
    if n_rows == 0 { return 0.0; }
    let mut counts: HashMap<Vec<u64>, usize> = HashMap::new();
    for i in 0..n_rows {
        let row: Vec<u64> = columns.iter().map(|c| c[i]).collect();
        *counts.entry(row).or_insert(0) += 1;
    }
    let dup: usize = counts.values().copied().filter(|&c| c >= 2).sum();
    dup as f64 / n_rows as f64
}

/// Distinct value count for one interned column (null id counts as a value),
/// matching polars n_unique.
pub fn distinct_count(column: &[u64]) -> i64 {
    column.iter().copied().collect::<HashSet<u64>>().len() as i64
}
```

- [ ] **Step 2: Add `#[cfg(test)]` unit tests**: empty (`duplicate_row_ratio(&[], 0)==0.0`); a dup group of 3 across 2 cols contributes 3/n; no-dup → 0; `distinct_count` on `[1,1,2,0]`→3, null-id (0) counts as a value; single-column dup via one Vec.

- [ ] **Step 3: Read-verify** (no `pyo3`/`arrow`/`polars` import added — pure std). Commit.
```bash
git add packages/rust/extensions/analysis-core/src/lib.rs
git commit -m "feat(analysis-core): duplicate_row_ratio + distinct_count kernels (interned u64)"
```

---

## Task 2: `analysis-native` — intern + 3 pyfunctions (CI-built)

**Files:** `packages/rust/extensions/analysis-native/src/lib.rs`, `Cargo.toml`

- [ ] **Step 1: Port `intern_column` from keys.rs + add float canon.** Copy `goldencheck-native/src/keys.rs`'s `intern_column(data: ArrayData) -> PyResult<Vec<u64>>` (the `intern_primitive!` macro + Utf8/LargeUtf8/Int*/UInt*/Float32/Float64/Boolean arms, null→0, unsupported→`PyTypeError`) into analysis-native. **Change the Float32/Float64 arms** to canonicalize before `to_bits`:
```rust
fn canon_f64_bits(x: f64) -> u64 {
    if x.is_nan() { 0x7ff8_0000_0000_0000 }      // one canonical NaN
    else if x == 0.0 { 0.0f64.to_bits() }         // -0.0 and +0.0 fold
    else { x.to_bits() }
}
// Float64 arm: intern on `canon_f64_bits(arr.value(i))`; Float32: `canon_f64_bits(arr.value(i) as f64)`.
```
(Confirm goldencheck-native is buildable-adjacent / the macro is copyable; if `Cargo.toml` needs the same arrow array types, they're already imported for `read_f64`.)

- [ ] **Step 2: Add the 3 `#[pyfunction]`s** + register in the pymodule (:56):
```rust
#[pyfunction]
fn duplicate_row_ratio(cols: Vec<PyArrowType<ArrayData>>) -> PyResult<f64> {
    let interned: Vec<Vec<u64>> = cols.into_iter().map(|c| intern_column(c.0)).collect::<PyResult<_>>()?;
    let n = interned.first().map(|c| c.len()).unwrap_or(0);
    Ok(analysis_core::duplicate_row_ratio(&interned, n))
}
#[pyfunction]
fn distinct_count(col: PyArrowType<ArrayData>) -> PyResult<i64> {
    Ok(analysis_core::distinct_count(&intern_column(col.0)?))
}
#[pyfunction]
fn null_ratio_per_column(cols: Vec<PyArrowType<ArrayData>>) -> PyResult<Vec<f64>> {
    Ok(cols.into_iter().map(|c| {
        let arr = arrow::array::make_array(c.0);
        let n = arr.len();
        if n == 0 { 0.0 } else { arr.null_count() as f64 / n as f64 }
    }).collect())
}
```
Register: `m.add_function(wrap_pyfunction!(duplicate_row_ratio, m)?)?;` (×3). (`intern_column` handles the dtype coverage; `null_ratio` needs no intern — just `null_count`.)

- [ ] **Step 3: Read-verify** brace balance, imports (`ArrayData`, `make_array`, the intern array types), pymodule registrations. Commit.
```bash
git add packages/rust/extensions/analysis-native/src/lib.rs packages/rust/extensions/analysis-native/Cargo.toml
git commit -m "feat(analysis-native): intern-based frame kernels (dup ratio, distinct, null ratio) + float canon"
```

---

## Task 3: `aggregate.py` dispatch + `distinct_count` + frame_summary + gating (box-safe)

**Files:** `packages/python/goldenanalysis/goldenanalysis/core/aggregate.py`, `analyzers/frame_summary.py`, `core/_native_loader.py`

- [ ] **Step 1: Write failing tests** for the pure-path + dispatch + fallback (box-safe, `GOLDENANALYSIS_NATIVE=0`): `distinct_count([...])` matches `series.n_unique()`; `null_ratio_per_column`/`duplicate_row_ratio` unchanged through the pure path; `frame_summary` output unchanged. (Parity vs native is Task 4 / CI.)

- [ ] **Step 2: Rename current bodies to `_*_pure`** and add the dispatch wrappers (mirror histogram/quantile), with the **try/except → pure** fallback for the frame kernels:
```python
def null_ratio_per_column(df):
    if native_enabled("null_ratio_per_column"):
        try:
            ratios = _null_ratio_per_column_native(df)   # native returns list[float] in column order
            return dict(zip(df.columns, ratios))
        except (TypeError, ValueError):
            pass
    return _null_ratio_per_column_pure(df)

def _null_ratio_per_column_native(df):
    return native_module().null_ratio_per_column([df[c].to_arrow() for c in df.columns])
```
Same shape for `duplicate_row_ratio` (native takes the column arrays, returns float) and `distinct_count` (native takes one `series.to_arrow()`, returns int). Keep `_*_pure` = the exact current logic.

- [ ] **Step 3: Add `distinct_count`** (new): `_distinct_count_pure(series) -> int: return series.n_unique()`; public `distinct_count` dispatches native (single col, try/except→pure). Refactor `frame_summary.py:67` `df[col].n_unique()` → `agg.distinct_count(df[col])`.

- [ ] **Step 4: Gating** (`_native_loader.py`): add `"null_ratio_per_column"`, `"duplicate_row_ratio"`, `"distinct_count"` to `_COMPONENT_SYMBOLS` (value == symbol) AND `_GATED_ON`.

- [ ] **Step 5: Run box-safe tests + ruff.**
`cd packages/python/goldenanalysis && PYTHONPATH=$(pwd) POLARS_SKIP_CPU_CHECK=1 GOLDENANALYSIS_NATIVE=0 D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/ -q -k "aggregate or frame_summary or distinct"` → pass (pure path).
`ruff check` the 3 files → clean.

- [ ] **Step 6: Commit.**
```bash
git add packages/python/goldenanalysis/goldenanalysis/core/aggregate.py packages/python/goldenanalysis/goldenanalysis/analyzers/frame_summary.py packages/python/goldenanalysis/goldenanalysis/core/_native_loader.py
git commit -m "feat(goldenanalysis): native dispatch for frame-stat kernels + distinct_count + gating"
```

---

## Task 4: Parity fixtures (box-safe reference; native run = CI)

**Files:** `packages/python/goldenanalysis/tests/core/test_native_parity.py`

- [ ] **Step 1: Extend the parity test** (mirrors the histogram/quantile parity; skips when the native kernel isn't built). For each fixture, assert `native == _pure`:
  - empty frame; all-null column; null-bearing column.
  - mixed-type (str/int/float/bool) frame with a dup group of ≥3.
  - **float-edge:** a float column with `-0.0`, `+0.0`, `NaN`, and duplicate NaN-bearing rows (asserts the canon matches polars: `distinct_count` folds, `duplicate_row_ratio` treats them equal).
  - high- and low-cardinality columns for `distinct_count`.
- [ ] **Step 2: Add a box-safe dtype-fallback test** (no native needed): with `GOLDENANALYSIS_NATIVE` forcing native but a `List`/`Struct` column, assert `null_ratio_per_column`/`duplicate_row_ratio` return the pure result (the try/except fired). (If native isn't built locally, this still exercises the try/except → pure path.)
- [ ] **Step 3: Commit.**
```bash
git add packages/python/goldenanalysis/tests/core/test_native_parity.py
git commit -m "test(goldenanalysis): parity fixtures for frame kernels (float-edge, dtype fallback)"
```

---

## Task 5: Docs + PR

- [ ] **Step 1: Verify the `native_symbols` gate stays green** for goldenanalysis (the 3 new exports are now referenced by `aggregate.py` `native_module().X` AND registered in analysis-native — box-safe: `python scripts/check_native_symbols.py goldenanalysis` should show them referenced+registered, `missing`=∅). Update `parity/native_symbols/goldenanalysis.allow` only if a real gap appears.
- [ ] **Step 2: CLAUDE.md / goldenanalysis docs** — note Wave 1 kernels + the intern-in-native / arrow-free-core split (mirrors keys.rs) + WASM deferred to Wave 1b.
- [ ] **Step 3: Push + PR + arm auto-merge (STOP).** PR body: Wave 1 of the GoldenAnalysis Rust cutover; 3 frame-stat kernels via the keys.rs intern pattern; float canon (box-verified polars semantics); dtype fallback; parity-gated (CI native lane); WASM + waves 2–4 as follow-ons. Note the Rust is CI-built (box can't compile).

---

## Notes for the implementer

- **Rust is CI-built** — write + read-verify; the native parity RUN is the goldenanalysis CI native lane. The box validates the `_pure` reference + dispatch + fallback + Rust unit-test authoring.
- **Reuse keys.rs `intern_column`** — don't reinvent; only ADD `canon_f64_bits` to the float arms.
- **The dtype fallback is load-bearing** — analyzers pass arbitrary frames; native-enabled must degrade to pure, never raise.
- **`_COMPONENT_SYMBOLS` is the functional gate** (`_GATED_ON` is doc); `aggregate.py` `native_enabled("X")` names must match its keys.
- **Run ruff** (#1451 lesson).
- **Don't touch** cluster_dist/match_rates/quality_rollup/regressions (Waves 2–4) or the orchestration.
