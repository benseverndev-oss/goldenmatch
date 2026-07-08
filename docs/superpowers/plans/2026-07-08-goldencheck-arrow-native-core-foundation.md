# GoldenCheck Arrow-native core foundation (Wave 0) — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `goldencheck-core` genuinely Arrow-native (kernels consume `arrow-rs` arrays directly), build a reusable parity-oracle harness, fix the `approximate_fd` symbol-probe, and flip the loader/CI so the Rust kernel is the default authority — all proven on the 5 already-byte-exact kernels, with zero new check kernels.

**Architecture:** Move the Arrow boundary *down* from the pyo3 `goldencheck-native` crate into `goldencheck-core`. Today `-native` decodes Arrow (`intern_column`, the Float64 decode) and passes plain slices to `-core`; after this, `-core`'s public API takes `&dyn Array`/`&[ArrayRef]`, interns/decodes internally, and delegates to the *unchanged* slice algorithms (kept as private, unit-tested helpers). `-native` thins to pyarrow↔`ArrayRef` marshalling. The 5 kernels are byte/set-exact today, so the existing `tests/core/test_native_parity.py` is the regression gate — it must stay green through the whole refactor.

**Tech Stack:** Rust (`arrow-rs` 55, `rustc-hash`, `pyo3` abi3), Python 3.11+ (Polars, pyarrow, pytest), GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-07-08-goldencheck-arrow-native-core-foundation-design.md`

---

## Conventions used in every task

**Rust build/test preamble** (copy-paste before any `cargo` command; from `packages/rust/extensions/CLAUDE.md`):
```bash
export PATH="/c/Users/bsevern/.cargo/bin:$PATH" && export RUSTUP_HOME="C:/Users/bsevern/.rustup" && export CARGO_HOME="C:/Users/bsevern/.cargo"
```
(If the toolchain isn't found on the D: exFAT checkout, fall back to the direct-binary paths in the `reference_rustup_proxy_exfat_direct_binary` memory.)

**Build core standalone (pyo3-free, arrow without pyarrow):**
```bash
cargo test --manifest-path packages/rust/extensions/goldencheck-core/Cargo.toml
```

**Build the in-tree native ext — CORRECTED (discovered during execution 2026-07-08):**
`scripts/build_goldencheck_native.py` **does not exist in this checkout** (it's referenced by `ci.yml:932` + CLAUDE.md but was never committed — the `goldencheck_native` CI job is currently broken; Task 9 CREATES the script, modeled on `packages/python/goldenflow/scripts/build_native.py`). For LOCAL builds in Tasks 4-8, build the ext with maturin into the **repo-root** `.venv` (there is NO per-package venv; tooling — python 3.13, pyarrow, pytest, maturin — lives in `D:\show_case\goldenmatch\.venv`):
```bash
cd packages/rust/extensions/goldencheck-native && /d/show_case/goldenmatch/.venv/Scripts/maturin.exe develop --release
```
This installs the `goldencheck_native` package, satisfying the loader's 2nd discovery path (`goldencheck_native._native`) so `native_available()` is True. Per `feedback_verify_rust_builds_explicitly`: grep build output for `^error`; never trust a piped tail.

**Run Python parity (native present) — CORRECTED:**
```bash
cd /d/show_case/goldenmatch && GOLDENCHECK_NATIVE=1 .venv/Scripts/python.exe -m pytest packages/python/goldencheck/tests/core/test_native_parity.py -v
```
(Repo-root `.venv/Scripts/python.exe`; `uv run` misses workspace members. Fixture paths anchor to `__file__` so CWD doesn't matter.)

**Commit discipline:** conventional commits, one commit per task's final step. Branch is already `feat/goldencheck-arrow-native-core` (do NOT create a new branch; the spec is already committed there). GitHub auth for any push: `gh auth switch --user benzsevern` first (see `feedback_github_auth_switch`) — but this plan does not push; it only commits locally.

**KEY INVARIANT:** the native `#[pyfunction]` signatures for benford + the 5 keys kernels **do not change** (Python still passes pyarrow arrays). Only fuzzy's native signature changes (Task 5). This means `test_native_parity.py` is a valid regression gate for Tasks 3-4 without edits.

---

## Task 1: Add arrow to goldencheck-core + scaffold the arrow_support module

**Files:**
- Modify: `packages/rust/extensions/goldencheck-core/Cargo.toml`
- Modify: `packages/rust/extensions/goldencheck-core/src/lib.rs:1-24`
- Create: `packages/rust/extensions/goldencheck-core/src/arrow_support.rs`

- [ ] **Step 1: Add the arrow dependency (umbrella crate, no pyarrow feature).**

In `goldencheck-core/Cargo.toml` `[dependencies]`, after `rustc-hash = "2"`, add:
```toml
# Arrow-native kernel input. The umbrella `arrow` crate, SAME version + form as
# goldencheck-native (which enables the `pyarrow` feature) so ArrayData/ArrayRef
# are one concrete type across the crate boundary. default-features = false keeps
# csv/ipc/json/compute out; we only read array buffers. We deliberately do NOT
# enable `pyarrow` here -- that feature pulls pyo3, and this crate stays pyo3-free.
arrow = { version = "55", default-features = false }
```
Also update the file header comment (lines 1-6): change "pure compute over slices, no Python, no Arrow" to "pure compute over Arrow arrays, no Python (pyo3-free)". Keep the "can later back a DuckDB/DataFusion SQL surface" sentence — it's now closer to true.

- [ ] **Step 2: Update lib.rs module docstring + declare the new module.**

In `src/lib.rs`, update the docstring (lines 9-13) from "take plain slices (`&[f64]`, `&[u64]`) ... carry no Python or Arrow types" to describe the new shape: "expose an Arrow-native public API (`&dyn Array` / `&[ArrayRef]`); the internal slice algorithms stay pyo3-free and are wrapped by thin Arrow-decoding entry points. The `goldencheck-native` crate now only marshals pyarrow↔Arrow." Add `mod arrow_support;` alongside the existing `mod` lines and `pub use arrow_support::intern_column;`.

- [ ] **Step 3: Create the arrow_support module with just a doc stub + re-export placeholder.**

`src/arrow_support.rs` (interning moves here in Task 2; for now a minimal compilable stub so the crate builds with arrow linked):
```rust
//! Arrow-native decoding helpers shared by the kernels: column interning to
//! dense `u64` value-ids (for the key/FD kernels) and typed numeric extraction
//! (for Benford). Moved down from the `goldencheck-native` pyo3 shim so the
//! Arrow boundary lives in the pyo3-free core. No pyo3, no Python.
use arrow::array::Array;
use arrow::error::ArrowError;

/// Placeholder to force `arrow` to link in Task 1; replaced in Task 2.
#[allow(dead_code)]
pub(crate) fn arrow_linked(a: &dyn Array) -> Result<usize, ArrowError> {
    Ok(a.len())
}
```
Remove `intern_column` from the `pub use` in Step 2 until Task 2 defines it (or make Task 2's diff additive — cleaner: in Step 2 do NOT add the `pub use intern_column` line yet; add it in Task 2).

- [ ] **Step 4: Verify core builds standalone (pyo3-free, arrow linked).**

Run: `cargo test --manifest-path packages/rust/extensions/goldencheck-core/Cargo.toml 2>&1 | grep -E "^error|test result"`
Expected: no `^error` lines; all existing benford/keys/fuzzy unit tests still `test result: ok`. This confirms arrow links without pyo3 and breaks nothing.

- [ ] **Step 5: Commit.**
```bash
git add packages/rust/extensions/goldencheck-core/Cargo.toml packages/rust/extensions/goldencheck-core/src/lib.rs packages/rust/extensions/goldencheck-core/src/arrow_support.rs
git commit -m "feat(goldencheck-core): link arrow-rs, scaffold arrow_support module"
```

---

## Task 2: Move `intern_column` into core with Arrow unit tests

Moves the ~120-line Arrow dtype dispatch from `goldencheck-native/src/keys.rs` into `goldencheck-core/src/arrow_support.rs`, verbatim in logic, but as pyo3-free (returns `Result<Vec<u64>, ArrowError>` instead of `PyResult`). Native keeps its own copy compiling for now (removed in Task 4).

**Files:**
- Modify: `packages/rust/extensions/goldencheck-core/src/arrow_support.rs`

- [ ] **Step 1: Write failing Arrow unit tests for interning.**

Append to `arrow_support.rs`:
```rust
#[cfg(test)]
mod tests {
    use super::*;
    use arrow::array::{Int64Array, StringArray, Float64Array};
    use std::sync::Arc;

    #[test]
    fn interns_strings_dense_from_one_nulls_zero() {
        let a = StringArray::from(vec![Some("x"), Some("y"), Some("x"), None]);
        let ids = intern_column(&a).unwrap();
        assert_eq!(ids[0], ids[2]);       // same value -> same id
        assert_ne!(ids[0], ids[1]);       // different value -> different id
        assert_eq!(ids[3], 0);            // null -> reserved 0
        assert!(ids[0] >= 1 && ids[1] >= 1);
    }

    #[test]
    fn interns_ints_and_floats_by_value() {
        let a = Int64Array::from(vec![Some(5), Some(5), Some(6), None]);
        let ids = intern_column(&a).unwrap();
        assert_eq!(ids[0], ids[1]);
        assert_ne!(ids[0], ids[2]);
        assert_eq!(ids[3], 0);
        let f = Float64Array::from(vec![Some(1.5), Some(1.5), Some(2.5)]);
        let fids = intern_column(&f).unwrap();
        assert_eq!(fids[0], fids[1]);
        assert_ne!(fids[0], fids[2]);
    }

    #[test]
    fn unsupported_dtype_errors() {
        use arrow::array::Date32Array;
        let a = Date32Array::from(vec![1, 2, 3]);
        assert!(intern_column(&a).is_err());
    }

    #[test]
    fn arc_dyn_array_accepted() {
        let a: Arc<dyn Array> = Arc::new(StringArray::from(vec!["a", "b"]));
        assert_eq!(intern_column(a.as_ref()).unwrap().len(), 2);
    }
}
```

- [ ] **Step 2: Run tests to verify they fail (no `intern_column`).**

Run: `cargo test --manifest-path packages/rust/extensions/goldencheck-core/Cargo.toml intern 2>&1 | grep -E "^error|cannot find"`
Expected: compile error `cannot find function intern_column`.

- [ ] **Step 3: Implement `intern_column(&dyn Array)` in core.**

Port `goldencheck-native/src/keys.rs::intern_column` (lines 19-138) into `arrow_support.rs`, changing:
- signature `fn intern_column(data: ArrayData) -> PyResult<Vec<u64>>` → `pub fn intern_column(array: &dyn Array) -> Result<Vec<u64>, ArrowError>`
- inputs: instead of `StringArray::from(data)` (consuming `ArrayData`), downcast the borrowed array: `array.as_any().downcast_ref::<StringArray>().unwrap()` guarded by the `match array.data_type()`. Use `arrow::array::cast::as_*` helpers or `downcast_ref`.
- the null-sentinel logic (id 0 = null, dense 1.. for values), the per-dtype `intern_primitive!` macro, float keying by `to_bits()`, and the boolean id mapping (true→1, false→2) are **identical** — preserve them byte-for-byte so interning is stable.
- error arm: `other => Err(ArrowError::InvalidArgumentError(format!("key/FD kernels do not support Arrow dtype {other:?}; cast to string/int/float/bool first")))`.

Imports at top of `arrow_support.rs`:
```rust
use arrow::array::{
    Array, BooleanArray, Float32Array, Float64Array, Int16Array, Int32Array, Int64Array,
    Int8Array, LargeStringArray, StringArray, UInt16Array, UInt32Array, UInt64Array, UInt8Array,
};
use arrow::datatypes::DataType;
use arrow::error::ArrowError;
use rustc_hash::FxHashMap;
```
Delete the `arrow_linked` placeholder. Add `pub use arrow_support::intern_column;` to `lib.rs` now.

- [ ] **Step 4: Run tests to verify they pass.**

Run: `cargo test --manifest-path packages/rust/extensions/goldencheck-core/Cargo.toml 2>&1 | grep -E "^error|test result"`
Expected: no `^error`; `test result: ok` including the 4 new interning tests.

- [ ] **Step 5: Commit.**
```bash
git add packages/rust/extensions/goldencheck-core/src/arrow_support.rs packages/rust/extensions/goldencheck-core/src/lib.rs
git commit -m "feat(goldencheck-core): move intern_column into core as Arrow-native helper"
```

---

## Task 3: Benford Arrow-in-core

Public core API takes `&dyn Array`; the slice algorithm becomes a private helper. Native `profile.rs` delegates to the new API (its Python signature is unchanged).

**Files:**
- Modify: `packages/rust/extensions/goldencheck-core/src/benford.rs:69-89`
- Modify: `packages/rust/extensions/goldencheck-native/src/profile.rs`

- [ ] **Step 1: Add a failing Arrow unit test in benford.rs.**

In `benford.rs` `mod tests`, add:
```rust
#[test]
fn arrow_matches_slice_and_drops_nulls() {
    use arrow::array::Float64Array;
    let arr = Float64Array::from(vec![Some(1.5), None, Some(200.0), None, Some(9.9)]);
    let via_arrow = benford_leading_digits(&arr).unwrap();
    let via_slice = benford_leading_digits_slice(&[1.5, 200.0, 9.9]);
    assert_eq!(via_arrow, via_slice);
}

#[test]
fn arrow_rejects_non_float64() {
    use arrow::array::Int64Array;
    let arr = Int64Array::from(vec![1, 2, 3]);
    assert!(benford_leading_digits(&arr).is_err());
}
```

- [ ] **Step 2: Run to verify it fails.**

Run: `cargo test --manifest-path packages/rust/extensions/goldencheck-core/Cargo.toml benford 2>&1 | grep -E "^error|cannot find"`
Expected: `cannot find function benford_leading_digits` with `&dyn Array` (current one takes `&[f64]`).

- [ ] **Step 3: Rename the slice fn and add the Arrow entry point.**

In `benford.rs`:
- Rename `pub fn benford_leading_digits(values: &[f64])` → `pub(crate) fn benford_leading_digits_slice(values: &[f64])` (body unchanged).
- Add the new public Arrow API (moves the null-drop from native into core):
```rust
use arrow::array::{Array, Float64Array};
use arrow::datatypes::DataType;
use arrow::error::ArrowError;

/// Leading-digit (1..=9) histogram for a Float64 Arrow column. Null slots are
/// dropped (their backing f64 is undefined), matching the Python reference which
/// only sees non-null values. Non-Float64 input is an error (the caller casts in
/// Polars before `.to_arrow()`).
pub fn benford_leading_digits(array: &dyn Array) -> Result<[u64; 9], ArrowError> {
    if array.data_type() != &DataType::Float64 {
        return Err(ArrowError::InvalidArgumentError(format!(
            "benford_leading_digits expects a Float64 array, got {:?}",
            array.data_type()
        )));
    }
    let arr = array.as_any().downcast_ref::<Float64Array>().unwrap();
    let vals: Vec<f64> = if arr.null_count() == 0 {
        arr.values().to_vec()
    } else {
        (0..arr.len()).filter(|&i| !arr.is_null(i)).map(|i| arr.value(i)).collect()
    };
    Ok(benford_leading_digits_slice(&vals))
}
```
Update the existing slice unit tests (`basic_digits`, `empty_is_all_zero`) to call `benford_leading_digits_slice`.

- [ ] **Step 4: Point native profile.rs at the new core API.**

Rewrite `goldencheck-native/src/profile.rs` `benford_leading_digits` body to marshalling-only — no dtype check, no null filtering (core owns both now):
```rust
use arrow::array::{ArrayData, make_array};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

#[pyfunction]
pub fn benford_leading_digits(values: PyArrowType<ArrayData>) -> PyResult<[u64; 9]> {
    let array = make_array(values.0);
    goldencheck_core::benford_leading_digits(array.as_ref())
        .map_err(|e| pyo3::exceptions::PyTypeError::new_err(e.to_string()))
}
```
(`make_array(ArrayData) -> ArrayRef` is the arrow-rs way to get a `&dyn Array`.)

- [ ] **Step 5: Build core + native, run Rust + Python parity.**
```bash
cargo test --manifest-path packages/rust/extensions/goldencheck-core/Cargo.toml benford 2>&1 | grep -E "^error|test result"
cd packages/python/goldencheck && python scripts/build_goldencheck_native.py 2>&1 | grep -E "^error|error\[" ; echo "build rc=$?"
GOLDENCHECK_NATIVE=1 .venv/Scripts/python.exe -m pytest tests/core/test_native_parity.py -k benford -v
```
Expected: core tests ok; native build clean; the 4 benford parity tests PASS (byte-identical histograms — the refactor preserved behavior).

- [ ] **Step 6: Commit.**
```bash
git add packages/rust/extensions/goldencheck-core/src/benford.rs packages/rust/extensions/goldencheck-native/src/profile.rs
git commit -m "feat(goldencheck-core): Benford kernel takes Arrow array in core; thin native shim"
```

---

## Task 4: Keys/FD Arrow-in-core

The five combinatorial kernels get Arrow-facing public wrappers in core (interning via `arrow_support::intern_column`); the slice algorithms are renamed to `*_ids` privates. Native `keys.rs` becomes marshalling-only and its local `intern_column` is deleted.

**Files:**
- Modify: `packages/rust/extensions/goldencheck-core/src/keys.rs`
- Modify: `packages/rust/extensions/goldencheck-native/src/keys.rs`

- [ ] **Step 1: Add failing Arrow unit tests in core keys.rs.**

In `keys.rs` `mod tests`, add (exercising the Arrow wrappers, reusing the existing scenarios):
```rust
#[test]
fn arrow_fd_holds_matches_ids() {
    use arrow::array::StringArray;
    let city = StringArray::from(vec!["a", "b", "c", "a"]);
    let country = StringArray::from(vec!["x", "x", "y", "x"]);
    assert!(functional_dependency_holds(&city, &country).unwrap());
    assert!(!functional_dependency_holds(&country, &city).unwrap());
}

#[test]
fn arrow_discover_fd_matches_ids() {
    use arrow::array::{ArrayRef, Int64Array};
    use std::sync::Arc;
    let zip: ArrayRef = Arc::new(Int64Array::from(vec![1, 1, 2, 3]));
    let city: ArrayRef = Arc::new(Int64Array::from(vec![10, 10, 10, 30]));
    let cols = vec![zip, city];
    let fds = discover_functional_dependencies(&cols).unwrap();
    assert!(fds.contains(&(0, 1)));
    assert!(!fds.contains(&(1, 0)));
}

#[test]
fn arrow_composite_key_matches_ids() {
    use arrow::array::{ArrayRef, Int64Array};
    use std::sync::Arc;
    let a: ArrayRef = Arc::new(Int64Array::from(vec![1, 1, 2, 2]));
    let b: ArrayRef = Arc::new(Int64Array::from(vec![10, 20, 10, 20]));
    let keys = composite_key_search(&[a, b], 3, &[false, false]).unwrap();
    assert_eq!(keys, vec![vec![0, 1]]);
}
```

- [ ] **Step 2: Run to verify failure.**

Run: `cargo test --manifest-path packages/rust/extensions/goldencheck-core/Cargo.toml keys 2>&1 | grep -E "cannot find|mismatched|^error"`
Expected: type/arity errors (current fns take `&[&[u64]]`, not Arrow).

- [ ] **Step 3: Rename slice algorithms to `*_ids` and add Arrow wrappers.**

In `keys.rs`:
- Rename the five public fns to private-in-crate `*_ids` (bodies unchanged): `tuple_distinct_count` stays as-is (internal); `functional_dependency_holds` → `functional_dependency_holds_ids`, `discover_functional_dependencies` → `discover_functional_dependencies_ids`, `fd_violation_rows` → `fd_violation_rows_ids`, `discover_approximate_fds` → `discover_approximate_fds_ids`, `composite_key_search` → `composite_key_search_ids`. Mark them `pub(crate)`. Update the existing `mod tests` calls to the `*_ids` names.
- Add Arrow-facing public wrappers. Helper to intern a slice of arrays:
```rust
use arrow::array::{Array, ArrayRef};
use arrow::error::ArrowError;
use crate::arrow_support::intern_column;

fn intern_all(columns: &[ArrayRef]) -> Result<Vec<Vec<u64>>, ArrowError> {
    columns.iter().map(|a| intern_column(a.as_ref())).collect()
}

pub fn functional_dependency_holds(lhs: &dyn Array, rhs: &dyn Array) -> Result<bool, ArrowError> {
    let l = intern_column(lhs)?;
    let r = intern_column(rhs)?;
    if l.len() != r.len() {
        return Err(ArrowError::InvalidArgumentError(
            "functional_dependency_holds: lhs and rhs differ in length".into()));
    }
    Ok(functional_dependency_holds_ids(&l, &r))
}

pub fn discover_functional_dependencies(columns: &[ArrayRef]) -> Result<Vec<(usize, usize)>, ArrowError> {
    if columns.is_empty() { return Ok(Vec::new()); }
    let ids = intern_all(columns)?;
    let refs: Vec<&[u64]> = ids.iter().map(|c| c.as_slice()).collect();
    Ok(discover_functional_dependencies_ids(&refs))
}

pub fn discover_approximate_fds(columns: &[ArrayRef], min_confidence: f64) -> Result<Vec<(usize, usize, usize)>, ArrowError> {
    if columns.is_empty() { return Ok(Vec::new()); }
    let ids = intern_all(columns)?;
    let refs: Vec<&[u64]> = ids.iter().map(|c| c.as_slice()).collect();
    Ok(discover_approximate_fds_ids(&refs, min_confidence))
}

pub fn fd_violation_rows(det: &dyn Array, dep: &dyn Array) -> Result<Vec<usize>, ArrowError> {
    let d = intern_column(det)?;
    let p = intern_column(dep)?;
    if d.len() != p.len() {
        return Err(ArrowError::InvalidArgumentError(
            "fd_violation_rows: det and dep differ in length".into()));
    }
    Ok(fd_violation_rows_ids(&d, &p))
}

pub fn composite_key_search(columns: &[ArrayRef], max_size: usize, single_unique: &[bool]) -> Result<Vec<Vec<usize>>, ArrowError> {
    if columns.is_empty() { return Ok(Vec::new()); }
    let ids = intern_all(columns)?;
    let n_rows = ids[0].len();
    let refs: Vec<&[u64]> = ids.iter().map(|c| c.as_slice()).collect();
    Ok(composite_key_search_ids(&refs, n_rows, max_size, single_unique))
}
```
Update `lib.rs` `pub use keys::{...}` to export the new Arrow-facing names (drop `tuple_distinct_count` / `functional_dependency_holds` slice exports; export the Arrow fns).

- [ ] **Step 4: Run core tests.**

Run: `cargo test --manifest-path packages/rust/extensions/goldencheck-core/Cargo.toml 2>&1 | grep -E "^error|test result"`
Expected: no `^error`; all existing `*_ids` unit tests + 3 new Arrow tests pass.

- [ ] **Step 5: Rewrite native keys.rs to marshalling-only; delete native intern_column.**

Rewrite `goldencheck-native/src/keys.rs`: delete `intern_column` (lines 19-138) and the `rustc-hash`/dtype imports it needed. Each `#[pyfunction]` now decodes pyarrow → `ArrayRef` via `make_array` and calls the core Arrow API. Example:
```rust
use arrow::array::{make_array, ArrayData, ArrayRef};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

fn to_arrays(v: Vec<PyArrowType<ArrayData>>) -> Vec<ArrayRef> {
    v.into_iter().map(|a| make_array(a.0)).collect()
}
fn map_err(e: arrow::error::ArrowError) -> PyErr {
    pyo3::exceptions::PyTypeError::new_err(e.to_string())
}

#[pyfunction]
pub fn composite_key_search(field_arrays: Vec<PyArrowType<ArrayData>>, max_size: usize, single_unique: Vec<bool>) -> PyResult<Vec<Vec<usize>>> {
    goldencheck_core::composite_key_search(&to_arrays(field_arrays), max_size, &single_unique).map_err(map_err)
}
// ...same shape for functional_dependency_holds, discover_functional_dependencies,
//    discover_approximate_fds, fd_violation_rows (single-array fns use make_array on .0).
```
Keep the exact `#[pyo3(signature = ...)]` attributes so Python call sites are unchanged. Remove `rustc-hash` from `goldencheck-native/Cargo.toml` if now unused (grep the crate first).

Note: today native raises `PyValueError` for lhs/rhs length mismatch (`keys.rs:181,240`); routing all `ArrowError` through `map_err` → `PyTypeError` changes that class. It's invisible (no test asserts it; all Python callers use bare `except Exception`), but if you want to preserve it, special-case the length-mismatch `ArrowError` to `PyValueError` in the two-array fns. Acceptable either way — note it in the commit body.

- [ ] **Step 6: Build native, run the keys/FD parity suite.**
```bash
cd packages/python/goldencheck && python scripts/build_goldencheck_native.py 2>&1 | grep -E "^error|error\[" ; echo "rc=$?"
GOLDENCHECK_NATIVE=1 .venv/Scripts/python.exe -m pytest tests/core/test_native_parity.py -k "composite or functional or discover_fd or approximate" -v
```
Expected: clean build; all composite-key / FD / approx-FD parity tests PASS (set-identical).

- [ ] **Step 7: Commit.**
```bash
git add packages/rust/extensions/goldencheck-core/src/keys.rs packages/rust/extensions/goldencheck-core/src/lib.rs packages/rust/extensions/goldencheck-native/src/keys.rs packages/rust/extensions/goldencheck-native/Cargo.toml
git commit -m "feat(goldencheck-core): key/FD kernels take Arrow arrays in core; native intern_column removed"
```

---

## Task 5: Fuzzy Arrow-in-core (the ceremony case — flagged)

**DECISION FLAG:** fuzzy's input is a column's *distinct* values (a small set), not a raw column, and the kernel author documented that Arrow "buys nothing here." Converting it to Arrow-in honors the approved "Arrow-in universally" rule but is uniformity, not performance, and introduces a null-index contract. If the user vetoes at the execution gate, SKIP this task and leave fuzzy taking `list[str]` (documented as the single non-Arrow-in kernel). Otherwise:

**Contract:** the Arrow input must be a `Utf8`/`LargeUtf8` array of **non-null** distinct values; cluster indices map 1:1 to input positions (as today). A null present → error (the caller `fuzzy_values.py` already passes `drop_nulls().unique()`, so this holds).

**Files:**
- Modify: `packages/rust/extensions/goldencheck-core/src/fuzzy.rs:123-190`
- Modify: `packages/rust/extensions/goldencheck-native/src/fuzzy.rs`
- Modify: `packages/python/goldencheck/goldencheck/profilers/fuzzy_values.py:133-137`
- Modify: `packages/python/goldencheck/goldencheck/cell_quality.py:50` — **this is a SECOND direct caller of the native symbol** (`near_duplicate_value_clusters(values, ...)` with `values: list[str]`). It MUST be converted too, or the bridge silently dead-falls-back to Python — the exact #688 footgun this program kills.
- Modify: `packages/python/goldencheck/benchmarks/deep_profile_benchmark.py:175` — a third `list[str]` caller (not CI-gated, but breaks at runtime under this task).
- Modify: `packages/python/goldencheck/tests/core/test_native_parity.py:213`

- [ ] **Step 1: Grep for ALL call sites of the native fuzzy symbol — there are THREE.**

Run: `grep -rn "near_duplicate_value_clusters" packages/python/goldencheck`
Expected hits: `profilers/fuzzy_values.py`, `cell_quality.py`, `benchmarks/deep_profile_benchmark.py`, and the parity test. Every one passes a `list[str]` today and MUST be converted to pass a pyarrow string array. Do NOT assume any caller "uses the profiler" — `cell_quality.py:50` calls the native symbol directly. Record every hit; each gets an edit in Step 6 and appears in the Step 8 commit.

- [ ] **Step 2: Add failing Arrow unit test in fuzzy.rs.**
```rust
#[test]
fn arrow_clusters_match_string_slice() {
    use arrow::array::StringArray;
    let arr = StringArray::from(vec!["California", "Californa", "CALIFORNIA", "Texas"]);
    let via_arrow = near_duplicate_clusters(&arr, 0.8).unwrap();
    assert_eq!(via_arrow, vec![vec![0, 1, 2]]);
}

#[test]
fn arrow_rejects_nulls() {
    use arrow::array::StringArray;
    let arr = StringArray::from(vec![Some("a"), None, Some("b")]);
    assert!(near_duplicate_clusters(&arr, 0.8).is_err());
}
```

- [ ] **Step 3: Run to verify failure.**

Run: `cargo test --manifest-path packages/rust/extensions/goldencheck-core/Cargo.toml fuzzy 2>&1 | grep -E "cannot find|mismatched|^error"`
Expected: current `near_duplicate_clusters` takes `&[String]`, so the `&dyn Array` call fails to compile.

- [ ] **Step 4: Rename the slice fn; add the Arrow wrapper.**

In `fuzzy.rs`:
- Rename `pub fn near_duplicate_clusters(values: &[String], ...)` → `pub(crate) fn near_duplicate_clusters_strs(values: &[String], ...)` (body unchanged). Update `mod tests` to call `_strs`.
- Add:
```rust
use arrow::array::{Array, LargeStringArray, StringArray};
use arrow::datatypes::DataType;
use arrow::error::ArrowError;

/// Cluster the distinct string `values` of a column into edit-distance-close
/// groups. Input must be a null-free Utf8/LargeUtf8 array; indices map 1:1.
pub fn near_duplicate_clusters(array: &dyn Array, min_similarity: f64) -> Result<Vec<Vec<usize>>, ArrowError> {
    if array.null_count() > 0 {
        return Err(ArrowError::InvalidArgumentError(
            "near_duplicate_clusters expects a null-free array (pass distinct non-null values)".into()));
    }
    let values: Vec<String> = match array.data_type() {
        DataType::Utf8 => {
            let a = array.as_any().downcast_ref::<StringArray>().unwrap();
            (0..a.len()).map(|i| a.value(i).to_string()).collect()
        }
        DataType::LargeUtf8 => {
            let a = array.as_any().downcast_ref::<LargeStringArray>().unwrap();
            (0..a.len()).map(|i| a.value(i).to_string()).collect()
        }
        other => return Err(ArrowError::InvalidArgumentError(format!(
            "near_duplicate_clusters expects Utf8/LargeUtf8, got {other:?}"))),
    };
    Ok(near_duplicate_clusters_strs(&values, min_similarity))
}
```
Update `lib.rs` `pub use fuzzy::near_duplicate_clusters;` (name unchanged, now Arrow-facing).

- [ ] **Step 5: Update native fuzzy.rs to take a pyarrow array.**
```rust
use arrow::array::{make_array, ArrayData};
use arrow::pyarrow::PyArrowType;
use pyo3::prelude::*;

#[pyfunction]
#[pyo3(signature = (values, min_similarity))]
pub fn near_duplicate_value_clusters(values: PyArrowType<ArrayData>, min_similarity: f64) -> PyResult<Vec<Vec<usize>>> {
    let array = make_array(values.0);
    goldencheck_core::near_duplicate_clusters(array.as_ref(), min_similarity)
        .map_err(|e| pyo3::exceptions::PyTypeError::new_err(e.to_string()))
}
```

- [ ] **Step 6: Update the Python call site + parity test to pass an Arrow array.**

`fuzzy_values.py:130-137` — replace the `list[str]` with the distinct-values Arrow array:
```python
distinct = col.drop_nulls().unique()          # already null-free
values: list[str] = distinct.to_list()        # keep for reverse-mapping variants below
...
if native_enabled("fuzzy_values"):
    try:
        clusters = native_module().near_duplicate_value_clusters(distinct.to_arrow(), _MIN_SIMILARITY)
    except Exception:  # noqa: BLE001
        clusters = _python_clusters(values, _MIN_SIMILARITY)
```
(`values` list is still needed for `variants = [values[i] for i in cluster]` — keep it; `distinct.to_arrow()` is the native input. Both index-align because `distinct` is the shared source.)

`cell_quality.py:50` — the second direct caller. It has `values: list[str]` in scope; convert to a pyarrow string array (import polars is already present, or use pyarrow):
```python
import pyarrow as pa
clusters = native_module().near_duplicate_value_clusters(pa.array(values, type=pa.string()), _MIN_SIMILARITY)
```
Keep the surrounding `try/except -> _python fallback` intact, and keep `values` for the index-based reverse mapping.

`benchmarks/deep_profile_benchmark.py:175` — third caller; same `pa.array(values, type=pa.string())` conversion.

`test_native_parity.py:213` — pass an Arrow array:
```python
import pyarrow as pa
nat = native_module().near_duplicate_value_clusters(pa.array(values, type=pa.string()), fv._MIN_SIMILARITY)
```

- [ ] **Step 7: Build + run fuzzy parity (Rust + Python).**
```bash
cargo test --manifest-path packages/rust/extensions/goldencheck-core/Cargo.toml fuzzy 2>&1 | grep -E "^error|test result"
cd packages/python/goldencheck && python scripts/build_goldencheck_native.py 2>&1 | grep -E "^error|error\[" ; echo "rc=$?"
GOLDENCHECK_NATIVE=1 .venv/Scripts/python.exe -m pytest tests/core/test_native_parity.py -k fuzzy tests/profilers -k fuzzy -v
```
Expected: core fuzzy tests ok; native build clean; fuzzy parity + profiler tests PASS.

- [ ] **Step 8: Commit.**
```bash
git add packages/rust/extensions/goldencheck-core/src/fuzzy.rs packages/rust/extensions/goldencheck-core/src/lib.rs packages/rust/extensions/goldencheck-native/src/fuzzy.rs packages/python/goldencheck/goldencheck/profilers/fuzzy_values.py packages/python/goldencheck/goldencheck/cell_quality.py packages/python/goldencheck/benchmarks/deep_profile_benchmark.py packages/python/goldencheck/tests/core/test_native_parity.py
git commit -m "feat(goldencheck-core): fuzzy value clustering takes Arrow array (uniformity; null-free contract)"
```
Verify all three callers were updated: `grep -rn "near_duplicate_value_clusters" packages/python/goldencheck | grep -v "to_arrow\|pa.array"` should return only the native-symbol definition/import lines, never a `list[str]` call site.

---

## Task 6: Full-surface build + both-lane parity gate

De-risking checkpoint: prove the whole refactor is byte/set-identical before touching the loader/CI.

**Files:** none (verification only)

- [ ] **Step 1: Explicit Rust build verification (both crates).**
```bash
cargo test --manifest-path packages/rust/extensions/goldencheck-core/Cargo.toml 2>&1 | tee /tmp/core.log | grep -E "test result"
grep -E "^error" /tmp/core.log && echo "CORE BUILD HAS ERRORS" || echo "core clean"
cd packages/python/goldencheck && python scripts/build_goldencheck_native.py 2>&1 | tee /tmp/nat.log | grep -E "Finished|error\[|^error" 
```
Expected: core `test result: ok`, no `^error`; native build `Finished`.

- [ ] **Step 2: Full parity suite, native lane.**
```bash
cd packages/python/goldencheck && GOLDENCHECK_NATIVE=1 .venv/Scripts/python.exe -m pytest tests/core/test_native_parity.py -v
```
Expected: every parity test PASSES (all 5 kernels byte/set-identical after the refactor).

- [ ] **Step 3: Relations + profilers suites, native lane (catches call-site regressions).**
```bash
cd packages/python/goldencheck && GOLDENCHECK_NATIVE=1 .venv/Scripts/python.exe -m pytest tests/relations tests/profilers tests/baseline -q
```
Expected: PASS (or the same skips as baseline needs `[baseline]` extra; ensure `.[dev,baseline]` installed).

- [ ] **Step 4: Fallback lane still green.**
```bash
cd packages/python/goldencheck && GOLDENCHECK_NATIVE=0 .venv/Scripts/python.exe -m pytest tests/relations tests/profilers tests/core -q
```
Expected: PASS — the pure-Python fallback is untouched.

- [ ] **Step 5: Commit (checkpoint marker, no code).**
```bash
git commit --allow-empty -m "test(goldencheck): Arrow-in-core refactor verified byte/set-identical on both lanes"
```

---

## Task 7: Reusable parity-oracle harness

Promote the hard-coded parity test into a reusable harness with an accepted-divergence registry (empty in Wave 0) and a component registry covering the 5 kernels + the 2 GoldenMatch bridges.

**Files:**
- Create: `packages/python/goldencheck/tests/core/parity_harness.py`
- Create: `packages/python/goldencheck/tests/core/test_parity_harness.py`

- [ ] **Step 1: Write the harness module.**

`parity_harness.py` — a registry + a runner. Sketch:
```python
"""Reusable parity-oracle harness. The Rust kernel is the source of truth; the
pure-Python/Polars fallback is asserted 'conforms or is documented-lossy'. Every
divergence must appear in ACCEPTED_DIVERGENCES (with a rationale + product-decision
ref) or the harness fails. Wave 0: the registry is EMPTY (all 5 kernels are
byte/set-exact), which validates the harness mechanics on known-exact code."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Callable, Any

@dataclass(frozen=True)
class Divergence:
    component: str
    rationale: str
    decision_ref: str

# Empty in Wave 0. A future wave adds entries when a kernel is deemed "more
# correct" than Polars and the product decision is signed off.
ACCEPTED_DIVERGENCES: tuple[Divergence, ...] = ()

@dataclass
class Component:
    name: str                       # loader component key
    run_native: Callable[[Any], Any]  # given a fixture, return native result (normalized)
    run_fallback: Callable[[Any], Any]
    fixtures: Callable[[int], list[Any]]  # seed -> list of inputs

def compare(component: Component, seed: int) -> list[str]:
    """Return a list of unexpected-divergence descriptions (empty = parity)."""
    problems: list[str] = []
    for fx in component.fixtures(seed):
        nat, fb = component.run_native(fx), component.run_fallback(fx)
        if nat != fb and not _accepted(component.name):
            problems.append(f"{component.name}: native={nat!r} fallback={fb!r} on {fx!r}")
    return problems

def _accepted(name: str) -> bool:
    return any(d.component == name for d in ACCEPTED_DIVERGENCES)
```
Then register the 7 components (benford, composite_keys, functional_dependencies, approximate_fd, fuzzy_values, plus the two bridges `cell_quality` and `functional_dependencies`-public-API) by reusing the fixture generators + `_python_*` fallbacks already present in `test_native_parity.py` (import and wrap them — DRY, do not duplicate the generators). Normalize results to order-independent forms (sets of frozensets for clusters/keys, tuples for histograms) inside each `Component`.

- [ ] **Step 2: Write the harness test (empty-registry must be green).**

`test_parity_harness.py`:
```python
import pytest
from goldencheck.core._native_loader import native_available
from tests.core import parity_harness as ph

native_only = pytest.mark.skipif(not native_available(), reason="native ext not built")

@native_only
@pytest.mark.parametrize("comp", ph.REGISTERED_COMPONENTS, ids=lambda c: c.name)
@pytest.mark.parametrize("seed", range(6))
def test_component_parity(comp, seed):
    problems = ph.compare(comp, seed)
    assert problems == [], "\n".join(problems)

def test_accepted_divergences_empty_in_wave0():
    # Wave 0 guarantee: nothing diverges yet. This test is the tripwire that a
    # later wave must consciously edit when it accepts its first divergence.
    assert ph.ACCEPTED_DIVERGENCES == ()
```

- [ ] **Step 3: Run the harness both lanes.**
```bash
cd packages/python/goldencheck && GOLDENCHECK_NATIVE=1 .venv/Scripts/python.exe -m pytest tests/core/test_parity_harness.py -v
```
Expected: all component×seed cases PASS; `test_accepted_divergences_empty_in_wave0` PASS. This proves the harness itself is correct on known-exact code.

- [ ] **Step 4: Commit.**
```bash
git add packages/python/goldencheck/tests/core/parity_harness.py packages/python/goldencheck/tests/core/test_parity_harness.py
git commit -m "test(goldencheck): reusable parity-oracle harness (empty divergence registry, Wave 0)"
```

---

## Task 8: Loader reference-mode flip

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/core/_native_loader.py`
- Modify: `packages/python/goldencheck/tests/core/` (a small loader test)

- [ ] **Step 1: Write a failing test for the new auto semantics + tuple probe.**

Create `tests/core/test_loader_reference_mode.py`:
```python
import pytest
from goldencheck.core import _native_loader as L

def test_auto_uses_native_wherever_symbol_exists(monkeypatch):
    """Under the reference-mode flip, `auto` no longer consults an allow-list."""
    monkeypatch.delenv("GOLDENCHECK_NATIVE", raising=False)
    assert not hasattr(L, "_GATED_ON")  # allow-list is gone

@pytest.mark.skipif(not L.native_available(), reason="native ext not built")
def test_approximate_fd_requires_both_symbols(monkeypatch):
    monkeypatch.setenv("GOLDENCHECK_NATIVE", "auto")
    # Both discover_approximate_fds AND fd_violation_rows must be present.
    assert L.native_enabled("approximate_fd") is True
```

- [ ] **Step 2: Run to verify failure.**

Run: `cd packages/python/goldencheck && .venv/Scripts/python.exe -m pytest tests/core/test_loader_reference_mode.py -v`
Expected: `test_auto_uses_native_wherever_symbol_exists` FAILS (`_GATED_ON` still present).

- [ ] **Step 3: Apply the flip.**

In `_native_loader.py`:
- Delete the `_GATED_ON` frozenset (lines 36-61) and its docstring block.
- Change `native_enabled` auto branch (line 91) from
  `return _native is not None and component in _GATED_ON and _has_symbol(component)`
  to `return _native is not None and _has_symbol(component)`.
- Change `_COMPONENT_SYMBOLS` (lines 96-102) values to tuples of ALL required symbols, and update `_has_symbol` to require every one:
```python
_COMPONENT_SYMBOLS: dict[str, tuple[str, ...]] = {
    "benford": ("benford_leading_digits",),
    "composite_keys": ("composite_key_search",),
    "functional_dependencies": ("discover_functional_dependencies",),
    "fuzzy_values": ("near_duplicate_value_clusters",),
    "approximate_fd": ("discover_approximate_fds", "fd_violation_rows"),
}

def _has_symbol(component: str) -> bool:
    if _native is None:
        return False
    symbols = _COMPONENT_SYMBOLS.get(component)
    if not symbols:
        return False
    return all(hasattr(_native, s) for s in symbols)
```
- Update the module docstring: `auto` now means "use native wherever a kernel symbol exists; pure-Python is a lossy fallback only when the wheel is absent" (per the roadmap). Keep `mode==0`/`mode==1` text.
- Also fix the stale `_GATED_ON` reference in `tests/core/test_native_parity.py:3` (docstring) — reword to "the gate that lets a component run under `GOLDENCHECK_NATIVE=auto`" without naming the deleted allow-list.

- [ ] **Step 4: Run loader tests + the full core suite.**
```bash
cd packages/python/goldencheck && GOLDENCHECK_NATIVE=1 .venv/Scripts/python.exe -m pytest tests/core -v
```
Expected: new loader tests PASS; `test_native_disabled_env_forces_python` still PASS (mode==0 unchanged); parity + harness still PASS.

- [ ] **Step 5: Commit.**
```bash
git add packages/python/goldencheck/goldencheck/core/_native_loader.py packages/python/goldencheck/tests/core/test_loader_reference_mode.py
git commit -m "feat(goldencheck): reference-mode flip — auto uses native wherever a symbol exists; two-symbol approximate_fd probe"
```

---

## Task 9: CI inversion (root ci.yml)

**Files:**
- Modify: `.github/workflows/ci.yml` (root — the `packages/python/goldencheck/.github/workflows/test.yml` is an orphan; do NOT touch it)

- [ ] **Step 0 (NEW — discovered during execution): CREATE the missing `scripts/build_goldencheck_native.py`.**

`ci.yml:932` runs `uv run python scripts/build_goldencheck_native.py` and `ci.yml:176` gates on it, but the script was never committed — the `goldencheck_native` CI job is currently broken. Create `packages/python/goldencheck/scripts/build_goldencheck_native.py`, modeled on `packages/python/goldenflow/scripts/build_native.py`: it should build the `goldencheck-native` crate (maturin/cargo) and drop `goldencheck/_native.abi3.so` (or `maturin develop` into the active env), matching what CLAUDE.md documents. Verify it runs green locally before wiring CI. This unbreaks the existing job that Step 2 builds on.

- [ ] **Step 1: Read the current goldencheck CI shape.**

Run: `grep -nE "goldencheck|GOLDENCHECK_NATIVE|build_goldencheck_native" .github/workflows/ci.yml`
Read the `goldencheck_native` job (~lines 903-950), the paths-filter block (~161-176), and the main Python test matrix (~272). Confirm: the `goldencheck_native` job already builds the ext + runs a `GOLDENCHECK_NATIVE=1` lane; the main matrix runs pure-Python.

**SCOPE REFINEMENT (discovered during execution):** the dedicated `goldencheck_native` job (ci.yml:903-950) ALREADY builds the ext + runs the parity suite + a required `GOLDENCHECK_NATIVE=1` lane — it IS the native-default lane once the build script exists. The shared 8-package `python` matrix (ci.yml:298) does NOT have a Rust toolchain, so making it build native would need a toolchain added to all 8 legs (high blast radius). So the low-risk correct approach is: (a) Step 0 creates the build script [the critical fix that unbreaks the job]; (b) add an explicit `GOLDENCHECK_NATIVE=0` fallback-lane step to the existing `goldencheck_native` job so BOTH lanes are provably exercised in one place; (c) widen the paths-filter to also trigger on the new/changed files (`cell_quality.py`, `tests/core/parity_harness.py`, `tests/core/test_parity_harness.py`, `tests/core/test_loader_reference_mode.py`). Do NOT touch the shared `python` matrix. Steps 2-original below are superseded by this.

- [ ] **Step 2 (SUPERSEDED — see refinement above): originally 'make the main matrix native-default'.**

Edit the main Python matrix so the goldencheck leg builds the native ext and runs with native present (default/auto), plus a `GOLDENCHECK_NATIVE=0` fallback lane. Keep the existing dedicated `goldencheck_native` required-mode job as-is. Because `ci.yml` self-triggers on its own change (root CLAUDE.md), the filter re-runs everything — no extra filter wiring needed unless a new job id is added.

**TWO REPO-SPECIFIC CONSTRAINTS you MUST honor (verify against the YAML you read in Step 1):**
1. **The `python` job runs under `uv`, not bare python** — it uses `uv sync --all-packages` (~ci.yml:308) and `uv run pytest` (~ci.yml:429/441). Bare `python`/`python -m pytest` will NOT hit the synced `.venv`. Use `uv run ...`.
2. **The `python` job is a matrix over ~8 packages sharing one pytest step (~ci.yml:272/382).** Any step you add MUST be guarded `if: matrix.pkg == 'goldencheck'` (match the actual matrix var name — it may be `matrix.package`), or every package leg builds the goldencheck ext and reddens 7 unrelated legs.

Concretely (adapt names to the actual matrix YAML):
```yaml
# added to the python matrix job, guarded to the goldencheck leg only:
- name: Build goldencheck native ext (Rust-as-oracle default lane)
  if: matrix.pkg == 'goldencheck'
  run: uv run python packages/python/goldencheck/scripts/build_goldencheck_native.py
- name: goldencheck tests (native default)
  if: matrix.pkg == 'goldencheck'
  working-directory: packages/python/goldencheck
  run: uv run pytest --timeout=120 --timeout-method=thread -q
- name: goldencheck tests (pure-Python fallback lane)
  if: matrix.pkg == 'goldencheck'
  working-directory: packages/python/goldencheck
  env: { GOLDENCHECK_NATIVE: "0" }
  run: uv run pytest tests/core tests/relations tests/profilers --timeout=120 --timeout-method=thread -q
```
If the shared pytest step already runs goldencheck pure-Python, either gate it off for `goldencheck` (so it isn't double-run) or treat it as the fallback lane and add only the native build + native lane. Decide based on the real YAML.

- [ ] **Step 3: Validate the workflow YAML.**

Run: `python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ci.yml OK')"`
Expected: `ci.yml OK` (a YAML startup failure = 0 jobs = required gate never reports; see `feedback_ci_yaml_startup_failure`).

- [ ] **Step 4: Commit.**
```bash
git add .github/workflows/ci.yml
git commit -m "ci(goldencheck): native is the default test lane; add pure-Python fallback lane"
```

---

## Task 10: Version bumps + docs relabel

**Files:**
- Modify: `packages/python/goldencheck/goldencheck/__init__.py` (`__version__`)
- Modify: `packages/python/goldencheck/pyproject.toml` (version, if it carries one)
- Modify: `packages/rust/extensions/goldencheck-core/Cargo.toml` + `goldencheck-native/Cargo.toml` + `goldencheck-native/pyproject.toml` (lockstep)
- Modify: `packages/python/goldencheck/CLAUDE.md` (native section), `_native_loader.py` docstring already done in Task 8
- Then: run `rollout-docs-sweep`

- [ ] **Step 1: Bump goldencheck (minor — no output change, internal refactor).**

Edit `goldencheck/__init__.py` `__version__` from `1.3.0` → `1.4.0`. Update any version test that reads `from goldencheck import __version__` (don't hardcode elsewhere — see CLAUDE.md gotcha).

- [ ] **Step 2: Bump the native crate + pyproject in lockstep.**

`goldencheck-core/Cargo.toml`, `goldencheck-native/Cargo.toml`, and `goldencheck-native/pyproject.toml` `[project].version` all `0.1.0` → `0.1.1` (maturin reads pyproject; a stale pyproject silently no-ops a republish — see CLAUDE.md).

- [ ] **Step 3: Relabel docs — Python path is a lossy fallback.**

Update `packages/python/goldencheck/CLAUDE.md` native section: replace "a kernel joins `_GATED_ON` only after parity" language with the reference-mode model (native is the default oracle wherever a symbol exists; `_GATED_ON` removed; pure-Python is the no-wheel lossy fallback). Note the Arrow-in-core boundary (kernels take Arrow arrays; native is marshalling-only). Note the two-symbol `approximate_fd` probe.

- [ ] **Step 4: Run the rollout-docs-sweep skill for the wider surface.**

Invoke the `rollout-docs-sweep` skill (per `feedback_rollout_docs_sweep`) to catch README / docs-site / wiki / CHANGELOG surfaces referencing `_GATED_ON`, "beat Polars gate", or the slice-based core. Apply its findings.

- [ ] **Step 5: Final full-suite sanity + commit.**
```bash
cd packages/python/goldencheck && GOLDENCHECK_NATIVE=1 .venv/Scripts/python.exe -m pytest tests/core tests/relations tests/profilers -q
```
Expected: green.
```bash
git add -A
git commit -m "chore(goldencheck): bump versions (py 1.4.0, native 0.1.1) + relabel Python path as lossy fallback"
```

---

## Done criteria (Wave 0 complete)

- [ ] `goldencheck-core` links arrow-rs, is pyo3-free, and every public kernel takes an Arrow array (`&dyn Array`/`&[ArrayRef]`); slice algorithms survive as private, unit-tested helpers.
- [ ] `goldencheck-native` is marshalling-only (no `intern_column`, no dtype/null logic).
- [ ] `test_native_parity.py` is green on the native lane — the 5 kernels are byte/set-identical to pre-refactor.
- [ ] `parity_harness.py` runs green with an empty `ACCEPTED_DIVERGENCES`, covering the 5 kernels + 2 bridges.
- [ ] Loader `auto` uses `_has_symbol` only; `_GATED_ON` is gone; `approximate_fd` requires both symbols.
- [ ] Root `ci.yml`: goldencheck main matrix is native-default + a `GOLDENCHECK_NATIVE=0` fallback lane; YAML validates.
- [ ] Versions bumped (py 1.4.0; native 0.1.1 in Cargo + pyproject); docs relabel done.
- [ ] No new *check* kernel was added (that is Waves 1-6).
