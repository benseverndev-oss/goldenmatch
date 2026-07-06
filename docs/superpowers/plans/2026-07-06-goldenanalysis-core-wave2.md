# GoldenAnalysis Rust cutover — Wave 2 (numeric reductions) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Cut over GoldenAnalysis's numeric single-column reductions (`mean`/`min`/`max` over f64 arrays) to the Rust `analysis-core`/`-native`/`-wasm` split via the pure-slice pattern, dispatched from `aggregate.py`, wired into `match_rates.mean_pair_score`, parity-gated against the Python `_*_pure` reference.

**Architecture:** Pure-slice kernels (`&[f64]` in, scalar out) added to `analysis-core` alongside the existing `histogram`/`quantile` — no Arrow-in-core, no intern (contrast Wave 1's frame kernels). `analysis-native` reuses the existing `read_f64(PyArrowType<ArrayData>)` helper; `analysis-wasm` adds trivial `*_impl` exports like `quantile_impl`. `aggregate.py` gains `mean`/`min`/`max` dispatchers mirroring the `histogram`/`quantile` idiom EXACTLY (NO try/except; `pa.array([float(v) for v in values if v is not None], type=pa.float64())`).

**Tech Stack:** Rust (pyo3/abi3 + wasm-bindgen, CI-built — the box can't `cargo build`), Python 3 + polars/pyarrow, `goldenanalysis` package.

**Branch:** `feat/goldenanalysis-core-wave2`, stacked on `feat/goldenanalysis-core-wave1` (Wave 1 #1471 still queued; same files). If Wave 1 squash-merges first, rebase onto `origin/main`.

**Spec:** `docs/superpowers/specs/2026-07-06-goldenanalysis-core-wave2-design.md`

**Environment notes for the implementer:**
- Rust does NOT build on this box and TS/wasm OOMs — do NOT attempt `cargo build`/`wasm-pack`. Rust/wasm correctness is proven in CI. Write the code + `#[cfg(test)]` unit tests; do not run them locally.
- Python pure-path IS box-runnable. Interpreter: `D:/show_case/goldenmatch/.venv/Scripts/python.exe`. Always prefix: `PYTHONPATH=packages/python/goldenanalysis POLARS_SKIP_CPU_CHECK=1 GOLDENANALYSIS_NATIVE=0`. (`GOLDENANALYSIS_NATIVE=0` forces the pure path since no native wheel is built locally.)
- Run `ruff check` on every touched Python file before committing.
- gh: `unset GH_TOKEN; gh auth switch --user benzsevern`. Merge-queue repo — no `--delete-branch`.

---

### Task 1: `analysis-core` — mean/min/max pure-slice kernels

**Files:**
- Modify: `packages/rust/extensions/analysis-core/src/lib.rs` (add 3 `pub fn` after `quantile`; add tests to the existing `mod tests`)

- [ ] **Step 1: Write the failing tests** (add to the existing `#[cfg(test)] mod tests`)

```rust
    #[test]
    fn mean_basic() {
        assert_eq!(mean(&[1.0, 2.0, 3.0]), 2.0);
        assert_eq!(mean(&[5.0]), 5.0);
    }
    #[test]
    fn mean_empty_is_zero() {
        assert_eq!(mean(&[]), 0.0);
    }
    #[test]
    fn mean_naive_left_to_right_sum() {
        // Naive sum: (((1e16 + 1) + 1) + ... ) - 1e16. With f64, 1e16 + 1 == 1e16,
        // so the small values are absorbed and the result is 0.0 / n. This pins the
        // NAIVE summation order (a pairwise/SIMD sum would recover the small values).
        let mut v = vec![1e16];
        v.extend(std::iter::repeat(1.0).take(100));
        v.push(-1e16);
        // Mirror of Python `sum(v)/len(v)` on the same list order.
        let expected = v.iter().sum::<f64>() / v.len() as f64;
        assert_eq!(mean(&v), expected);
    }
    #[test]
    fn min_max_basic() {
        assert_eq!(min(&[3.0, 1.0, 2.0]), 1.0);
        assert_eq!(max(&[3.0, 1.0, 2.0]), 3.0);
        assert_eq!(min(&[-1.5]), -1.5);
    }
    #[test]
    fn min_max_empty_is_zero() {
        assert_eq!(min(&[]), 0.0);
        assert_eq!(max(&[]), 0.0);
    }
```

- [ ] **Step 2: Add the kernels** (after `pub fn quantile`, before `#[cfg(test)]`)

```rust
/// Arithmetic mean. Empty => 0.0 (matches `quantile`'s empty convention).
///
/// NAIVE left-to-right summation (`iter().sum()` folds from 0.0) to byte-match the
/// Python reference `sum(values)/len(values)`. Do NOT swap to a pairwise/SIMD sum:
/// it would reorder the additions and break byte-parity with `_mean_pure`.
pub fn mean(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.iter().sum::<f64>() / values.len() as f64
}

/// Minimum over finite values. Empty => 0.0. (NaN-ignoring via `f64::min`; the wired
/// callers pass finite values — see the spec's min/max-NaN note.)
pub fn min(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.iter().copied().fold(f64::INFINITY, f64::min)
}

/// Maximum over finite values. Empty => 0.0.
pub fn max(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values.iter().copied().fold(f64::NEG_INFINITY, f64::max)
}
```

- [ ] **Step 3: Verify it compiles + tests pass** — CI only (do NOT run locally). Confirm by reading that the signatures mirror `histogram`/`quantile` (both `pub fn ...(values: &[f64], ...)`), the tests reference the new fns, and the file still has a single `mod tests`.

- [ ] **Step 4: Commit**

```bash
git add packages/rust/extensions/analysis-core/src/lib.rs
git commit -m "feat(analysis-core): mean/min/max pure-slice kernels (Wave 2)"
```

---

### Task 2: `analysis-native` — 3 pyfunctions reusing `read_f64`

**Files:**
- Modify: `packages/rust/extensions/analysis-native/src/lib.rs` (add 3 `#[pyfunction]`s + register in the `#[pymodule]`)

- [ ] **Step 1: Read the existing `quantile` pyfunction + `read_f64` helper + the pymodule registration block.** Mirror them exactly. `read_f64` takes `PyArrowType<ArrayData>` and returns `Vec<f64>` (order-preserving, nulls dropped).

- [ ] **Step 2: Add the pyfunctions** (next to the existing `histogram`/`quantile` pyfunctions)

```rust
#[pyfunction]
fn mean(values: PyArrowType<ArrayData>) -> PyResult<f64> {
    Ok(analysis_core::mean(&read_f64(values)?))
}

#[pyfunction]
fn min(values: PyArrowType<ArrayData>) -> PyResult<f64> {
    Ok(analysis_core::min(&read_f64(values)?))
}

#[pyfunction]
fn max(values: PyArrowType<ArrayData>) -> PyResult<f64> {
    Ok(analysis_core::max(&read_f64(values)?))
}
```
(Match the EXACT signature/error-propagation of the existing `quantile` pyfunction — if it takes `py: Python` or returns differently, mirror that. `read_f64`'s `?` handles the non-Float64 TypeError.)

- [ ] **Step 3: Register in the `#[pymodule]`** — add alongside the existing `m.add_function(wrap_pyfunction!(histogram, m)?)?;` lines:

```rust
    m.add_function(wrap_pyfunction!(mean, m)?)?;
    m.add_function(wrap_pyfunction!(min, m)?)?;
    m.add_function(wrap_pyfunction!(max, m)?)?;
```
(Use the exact `wrap_pyfunction!` form already in the file — with or without `&`/`py` per the pyo3 version in use.)

- [ ] **Step 4: Verify (read-only)** — the 3 fns exist, delegate to `analysis_core::{mean,min,max}`, and are registered. No local build.

- [ ] **Step 5: Commit**

```bash
git add packages/rust/extensions/analysis-native/src/lib.rs
git commit -m "feat(analysis-native): mean/min/max pyfunctions over Float64 (Wave 2)"
```

---

### Task 3: `analysis-wasm` — 3 pure-slice impls

**Files:**
- Modify: `packages/rust/extensions/analysis-wasm/src/lib.rs` (add 3 `*_impl` fns + their wasm-test coverage in the existing `#[cfg(test)]`)

- [ ] **Step 1: Read `quantile_impl` + its test.** Mirror the export style exactly (whether it's a bare `pub fn ..._impl(&[f64]) -> f64` delegating to `analysis_core`, with a separate `#[wasm_bindgen]` wrapper, or a direct wasm export — match what `quantile_impl` does).

- [ ] **Step 2: Add the impls**

```rust
pub fn mean_impl(values: &[f64]) -> f64 {
    analysis_core::mean(values)
}
pub fn min_impl(values: &[f64]) -> f64 {
    analysis_core::min(values)
}
pub fn max_impl(values: &[f64]) -> f64 {
    analysis_core::max(values)
}
```
(If `quantile_impl` also has a `#[wasm_bindgen]` public wrapper taking a JS-friendly type, add the same wrappers for mean/min/max. Follow the file — do not invent a new pattern.)

- [ ] **Step 3: Add tests** (mirror the `quantile_impl` test in the same `mod tests`)

```rust
    #[test]
    fn mean_matches_core() {
        assert_eq!(mean_impl(&[1.0, 2.0, 3.0]), 2.0);
        assert_eq!(mean_impl(&[]), 0.0);
    }
    #[test]
    fn min_max_impl_basic() {
        assert_eq!(min_impl(&[3.0, 1.0, 2.0]), 1.0);
        assert_eq!(max_impl(&[3.0, 1.0, 2.0]), 3.0);
        assert_eq!(min_impl(&[]), 0.0);
        assert_eq!(max_impl(&[]), 0.0);
    }
```
(Update the `use super::{...}` import line in the test module to include the 3 new names.)

- [ ] **Step 4: Verify (read-only)** — no local wasm build.

- [ ] **Step 5: Commit**

```bash
git add packages/rust/extensions/analysis-wasm/src/lib.rs
git commit -m "feat(analysis-wasm): mean/min/max pure-slice impls (Wave 2)"
```

---

### Task 4: `aggregate.py` dispatch + `match_rates` wire + gating

**Files:**
- Modify: `packages/python/goldenanalysis/goldenanalysis/core/aggregate.py` (add `mean`/`min`/`max` public dispatchers + `_*_native` + `_*_pure`)
- Modify: `packages/python/goldenanalysis/goldenanalysis/analyzers/match_rates.py:79` (wire `agg.mean`)
- Modify: `packages/python/goldenanalysis/goldenanalysis/core/_native_loader.py` (add 3 to `_COMPONENT_SYMBOLS` + `_GATED_ON`)
- Test: `packages/python/goldenanalysis/tests/core/test_aggregate_wave2.py` (new — pure-path unit tests)

- [ ] **Step 1: Write the failing pure-path tests** (`tests/core/test_aggregate_wave2.py`)

```python
"""Wave 2 numeric-reduction pure-path unit tests (box-safe; GOLDENANALYSIS_NATIVE=0)."""
from goldenanalysis.core import aggregate as agg


def test_mean_basic():
    assert agg.mean([1.0, 2.0, 3.0]) == 2.0
    assert agg.mean([5.0]) == 5.0


def test_mean_empty_is_zero():
    assert agg.mean([]) == 0.0


def test_mean_filters_none():
    # None dropped, matching the native read_f64 null-drop + _histogram/_quantile_pure.
    assert agg.mean([1.0, None, 3.0]) == 2.0


def test_mean_matches_python_sum_over_same_order():
    v = [1e16] + [1.0] * 100 + [-1e16]
    assert agg.mean(v) == sum(v) / len(v)


def test_min_max_basic():
    assert agg.min([3.0, 1.0, 2.0]) == 1.0
    assert agg.max([3.0, 1.0, 2.0]) == 3.0


def test_min_max_empty_is_zero():
    assert agg.min([]) == 0.0
    assert agg.max([]) == 0.0


def test_min_max_filter_none():
    assert agg.min([3.0, None, 1.0]) == 1.0
    assert agg.max([3.0, None, 1.0]) == 3.0
```

- [ ] **Step 2: Run — expect failure** (`agg.mean` not defined)

```
PYTHONPATH=packages/python/goldenanalysis POLARS_SKIP_CPU_CHECK=1 GOLDENANALYSIS_NATIVE=0 \
  D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenanalysis/tests/core/test_aggregate_wave2.py -q
```
Expected: FAIL / AttributeError: module 'goldenanalysis.core.aggregate' has no attribute 'mean'.

- [ ] **Step 3: Add the dispatchers to `aggregate.py`** (after `quantile`/`_quantile_native`, mirroring them EXACTLY — note NO try/except, and the exact `pa.array([float(v) for v in values if v is not None], type=pa.float64())` idiom)

```python
def mean(values: Sequence[float]) -> float:
    """Arithmetic mean. Empty input => 0.0.

    Dispatches to the native kernel when gated (byte-identical to ``_mean_pure``).
    """
    if native_enabled("mean"):
        return _mean_native(values)
    return _mean_pure(values)


def _mean_pure(values: Sequence[float]) -> float:
    vals = [float(v) for v in values if v is not None]
    return sum(vals) / len(vals) if vals else 0.0


def _mean_native(values: Sequence[float]) -> float:
    import pyarrow as pa

    vals = [float(v) for v in values if v is not None]
    arr = pa.array(vals, type=pa.float64())
    return native_module().mean(arr)


def min(values: Sequence[float]) -> float:
    """Minimum over finite values. Empty input => 0.0.

    Dispatches to the native kernel when gated (byte-identical to ``_min_pure``).
    """
    if native_enabled("min"):
        return _min_native(values)
    return _min_pure(values)


def _min_pure(values: Sequence[float]) -> float:
    vals = [float(v) for v in values if v is not None]
    return __builtins__["min"](vals) if vals else 0.0  # see note below


def _min_native(values: Sequence[float]) -> float:
    import pyarrow as pa

    vals = [float(v) for v in values if v is not None]
    arr = pa.array(vals, type=pa.float64())
    return native_module().min(arr)


def max(values: Sequence[float]) -> float:
    """Maximum over finite values. Empty input => 0.0.

    Dispatches to the native kernel when gated (byte-identical to ``_max_pure``).
    """
    if native_enabled("max"):
        return _max_native(values)
    return _max_pure(values)


def _max_pure(values: Sequence[float]) -> float:
    vals = [float(v) for v in values if v is not None]
    return __builtins__["max"](vals) if vals else 0.0


def _max_native(values: Sequence[float]) -> float:
    import pyarrow as pa

    vals = [float(v) for v in values if v is not None]
    arr = pa.array(vals, type=pa.float64())
    return native_module().max(arr)
```

**IMPORTANT — the `min`/`max` name-shadowing trap:** defining module-level `min`/`max`
shadows the Python builtins *within this module*, so `_min_pure`/`_max_pure` and
`_histogram_pure` (which calls `min(vals), max(vals)` at lines ~113) would recurse or
break. Resolve cleanly: at the TOP of `aggregate.py` add `import builtins` and use
`builtins.min`/`builtins.max` everywhere inside the module that needs the reduction
(i.e. `_min_pure`/`_max_pure` return `builtins.min(vals)`/`builtins.max(vals)`, and
FIX `_histogram_pure`'s `lo, hi = min(vals), max(vals)` → `builtins.min(vals),
builtins.max(vals)`). Drop the `__builtins__["min"]` placeholder above — use
`builtins.min`. Verify via grep that no other bare `min(`/`max(` call inside
`aggregate.py` is left pointing at the shadowed names.

- [ ] **Step 4: Run the Wave 2 tests + the FULL existing aggregate/analyzer test modules** (the shadow fix must not regress histogram/quantile/match_rates)

```
PYTHONPATH=packages/python/goldenanalysis POLARS_SKIP_CPU_CHECK=1 GOLDENANALYSIS_NATIVE=0 \
  D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenanalysis/tests/core/ \
  packages/python/goldenanalysis/tests/analyzers/test_match_rates.py -q
```
Expected: PASS (new + all existing).

- [ ] **Step 5: Wire `match_rates.py`** — change line 79 from
  `mean_score = sum(scores) / len(scores)` to `mean_score = agg.mean(scores)`
  (the `if scored_pairs:` guard at line 77 keeps `scores` non-empty; `agg` is already
  imported at line 12). Byte-identical on the pure path.

- [ ] **Step 6: Add gating** in `_native_loader.py` — add three entries to BOTH
  `_GATED_ON` (frozenset) and `_COMPONENT_SYMBOLS` (dict, `"mean": "mean"` etc.):

```python
# in _GATED_ON frozenset({...}): add "mean", "min", "max"
# in _COMPONENT_SYMBOLS dict: "mean": "mean", "min": "min", "max": "max",
```
Check whether `tests/core/test_native_loader.py` asserts the exact contents of
`_GATED_ON`/`_COMPONENT_SYMBOLS` (Wave 1 / the P4 gate-flip updated such an assertion) —
if so, update it to include the 3 new names.

- [ ] **Step 7: Re-run the core + loader tests + `ruff check` the 3 touched Python files**

```
PYTHONPATH=packages/python/goldenanalysis POLARS_SKIP_CPU_CHECK=1 GOLDENANALYSIS_NATIVE=0 \
  D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenanalysis/tests/core/ \
  packages/python/goldenanalysis/tests/analyzers/test_match_rates.py -q
ruff check packages/python/goldenanalysis/goldenanalysis/core/aggregate.py \
  packages/python/goldenanalysis/goldenanalysis/analyzers/match_rates.py \
  packages/python/goldenanalysis/goldenanalysis/core/_native_loader.py \
  packages/python/goldenanalysis/tests/core/test_aggregate_wave2.py
```
Expected: tests PASS, ruff clean.

- [ ] **Step 8: Commit**

```bash
git add packages/python/goldenanalysis/goldenanalysis/core/aggregate.py \
  packages/python/goldenanalysis/goldenanalysis/analyzers/match_rates.py \
  packages/python/goldenanalysis/goldenanalysis/core/_native_loader.py \
  packages/python/goldenanalysis/tests/core/test_aggregate_wave2.py
git commit -m "feat(goldenanalysis): dispatch mean/min/max to native + wire match_rates (Wave 2)"
```

---

### Task 5: Native parity fixtures + CLAUDE.md note

**Files:**
- Modify: `packages/python/goldenanalysis/tests/core/test_native_parity.py` (add mean/min/max parity cases in the existing skip-when-unbuilt harness)
- Modify: `CLAUDE.md` (Wave 2 note)

- [ ] **Step 1: Read the existing parity test** — note the skip guard (skips unless the native wheel is built, i.e. it runs only in the CI `goldenanalysis_native` lane under `GOLDENANALYSIS_NATIVE=1`), the fixture idiom, and how it references `_*_pure` vs the native dispatch. Mirror it.

- [ ] **Step 2: Add parity cases** — for each fixture assert the public dispatch under native == the `_*_pure` reference:

```python
# fixtures reused from the module's style; add:
_NUMERIC_FIXTURES = [
    [1.0, 2.0, 3.0],
    [5.0],
    [-3.5, -1.0, 2.25, 100.0],
    [1e16] + [1.0] * 100 + [-1e16],   # summation-order: naive == naive
    [0.0, 0.0, 0.0],
]

@native_only  # or whatever skip-marker the module already uses
@pytest.mark.parametrize("xs", _NUMERIC_FIXTURES)
def test_mean_native_matches_pure(xs):
    assert aggregate.mean(xs) == aggregate._mean_pure(xs)  # native path (env=1) == pure ref

@native_only
@pytest.mark.parametrize("xs", _NUMERIC_FIXTURES)
def test_min_max_native_match_pure(xs):
    assert aggregate.min(xs) == aggregate._min_pure(xs)
    assert aggregate.max(xs) == aggregate._max_pure(xs)
```
(Use the module's ACTUAL skip decorator/guard name and import alias — read it in Step 1; don't assume `@native_only`. Empty-list case is exercised by the Task-4 pure tests; the parity lane needs ≥1 element to be meaningful but include a small one.)

- [ ] **Step 3: Verify the parity test still collects + skips cleanly on the box** (no wheel → skipped, not errored)

```
PYTHONPATH=packages/python/goldenanalysis POLARS_SKIP_CPU_CHECK=1 \
  D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest \
  packages/python/goldenanalysis/tests/core/test_native_parity.py -q
```
Expected: all new tests SKIPPED (native wheel absent), 0 errors. `ruff check` the file.

- [ ] **Step 4: Add the CLAUDE.md note** — append to the GoldenAnalysis section a Wave 2 bullet: numeric reductions (mean/min/max) cut to the pure-slice `analysis-core` pattern (contrast Wave 1's intern-based frame kernels), all three surfaces (Python + native + WASM), wired into `match_rates.mean_pair_score`; the `min`/`max` builtin-shadow fix in `aggregate.py` (module defines `min`/`max` → uses `builtins.min/max` internally); `std` deferred; cluster_dist/quality_rollup/regressions assessed trivial-not-cut.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenanalysis/tests/core/test_native_parity.py CLAUDE.md
git commit -m "test(goldenanalysis): mean/min/max native parity fixtures + Wave 2 CLAUDE note"
```

---

### Finalize

- [ ] Push `feat/goldenanalysis-core-wave2`; open PR with base `main` (it will show Wave 1's commits until #1471 merges — standard stacked-PR view). PR body: what Wave 2 does (numeric reductions, all 3 surfaces, pure-slice pattern), the spec link, the summation-order parity note, the `min`/`max` builtin-shadow gotcha, and the stacked-on-#1471 + rebase-if-Wave-1-merges-first note.
- [ ] `gh pr merge --auto --squash` (NO `--delete-branch`) and STOP. Do not poll CI.
