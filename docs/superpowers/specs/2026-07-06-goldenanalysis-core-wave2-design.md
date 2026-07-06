# GoldenAnalysis Rust cutover — Wave 2 (numeric reductions) — design

**Status:** approved (brainstorm 2026-07-06), pending spec review
**Context:** Wave 1 (#1471) cut over the frame-stat kernels (intern-based, Arrow-in-
native). Wave 2 cuts over the remaining genuine muscle — **numeric single-column
reductions** — which fit the ORIGINAL pure-slice `analysis-core` pattern
(`histogram`/`quantile`: `&[f64]` in, scalar out) exactly: no Arrow-in-core, no
intern. Stacked on Wave 1's branch (same files). Related:
`project_goldenanalysis_roadmap`.

## 1. Scope (muscle-focused, YAGNI)

Investigated all remaining analyzers: `cluster_dist` (small `sizes` list),
`quality_rollup` (small findings Counter), `_regressions` (7-element window) are
**trivial** — not worth a Rust cutover (Arrow overhead on a Python loop). The one
genuine muscle is `match_rates`' `mean_score = sum(scores)/len(scores)` over a
potentially-large score array. Wave 2 = the numeric-reduction cohort:
- **`mean(&[f64]) -> f64`** — WIRED into `match_rates.mean_pair_score`.
- **`min`/`max`** — the numeric-summary companions (exact parity; the foundation a
  future numeric-per-column `frame_summary` stat would use). Not yet wired.
- **`std` DEFERRED** — float summation-order + population/sample ambiguity, and
  unwired; add it when a numeric-stats analyzer needs it.

## 2. Every surface (Python + native + WASM) — this wave completes them

Unlike Wave 1's frame kernels (WASM deferred — arrow-in-wasm is heavy), Wave 2's
kernels are pure-slice `&[f64]`, so `analysis-wasm` exposes them the same trivial way
it already exposes `quantile_impl`. So Wave 2 delivers **all three surfaces** — the
thesis's "one Rust source, surfaces fall out," fully realized for these kernels.

## 3. Design (mirror histogram/quantile exactly)

### 3.1 `analysis-core` — 3 pure-slice kernels

```rust
/// Arithmetic mean. Empty => 0.0 (matches quantile's empty convention).
/// NAIVE left-to-right summation to byte-match Python `sum(v)/len(v)`.
pub fn mean(values: &[f64]) -> f64 {
    if values.is_empty() { return 0.0; }
    values.iter().sum::<f64>() / values.len() as f64
}
/// Min / max over finite values. Empty => 0.0.
pub fn min(values: &[f64]) -> f64 {
    values.iter().copied().fold(f64::INFINITY, f64::min).min(f64::INFINITY)  // 0.0 if empty (handled below)
}
pub fn max(values: &[f64]) -> f64 { /* symmetric with f64::NEG_INFINITY; empty => 0.0 */ }
```
(min/max: return `0.0` for empty explicitly — `if values.is_empty() { return 0.0; }` — don't leak `INFINITY`.) Unit tests: mean of `[1,2,3]`→2.0, empty→0.0; min/max basic + empty.

**Summation parity (load-bearing):** `mean` MUST use naive `iter().sum()`
(left-to-right IEEE754), matching Python `sum()` — NOT a pairwise/SIMD sum, which
would reorder additions and break byte-parity with `_mean_pure`.

### 3.2 `analysis-native` (pyo3) — 3 pyfunctions (existing single-array pattern)

Reuse `read_f64(PyArrowType<ArrayData>) -> Vec<f64>` (the histogram/quantile helper);
add `mean`/`min`/`max` `#[pyfunction]`s delegating to `analysis_core::*`; register in
the pymodule. Single `Float64Array` in — no intern, no RecordBatch.

### 3.3 `analysis-wasm` — 3 pure-slice impls (trivial, mirror `quantile_impl`)

`mean_impl(&[f64]) -> f64`, `min_impl`, `max_impl` delegating to `analysis_core::*`,
with the crate's existing wasm-test convention.

### 3.4 `aggregate.py` — dispatch (mirror histogram/quantile)

`mean`/`min`/`max` public dispatchers: `if native_enabled("X"): _X_native(values) else:
_X_pure(values)`, with **NO try/except** — mirror the `histogram`/`quantile` dispatchers
exactly (they have none; only the intern-based Wave-1 kernels wrap in try/except because
`intern_column` can reject dtypes — the pure-slice kernels can't).

`_X_native` MUST use the EXACT existing histogram/quantile Arrow idiom (verified in
`aggregate.py`) — **NOT** `pl.Series(values).to_arrow()`:
```python
arr = pa.array([float(v) for v in values if v is not None], type=pa.float64())
return native_module().X(arr)
```
Forcing `type=pa.float64()` + pre-`float()`-ing is load-bearing: `pl.Series([1,0,1]).to_arrow()`
would infer `Int64`, and `analysis-native`'s `read_f64` raises `TypeError` on non-Float64 —
which, with no try/except, would propagate uncaught instead of falling back.

`_*_pure` — filter `None` IDENTICALLY to the native path (which drops nulls via the
list-comp + `read_f64`), matching `_histogram_pure`/`_quantile_pure` which both filter
`if v is not None`:
- `_mean_pure(v)`: `xs = [float(x) for x in v if x is not None]; return sum(xs)/len(xs) if xs else 0.0`
- `_min_pure(v)`: `xs = [...]; return min(xs) if xs else 0.0`; `_max_pure` symmetric.
(No dtype-fallback try/except needed — these take numeric sequences, like histogram/quantile.)

### 3.5 Wire `match_rates` + gating

- `match_rates.py:79`: `mean_score = sum(scores) / len(scores)` → `agg.mean(scores)`
  (the `if scored_pairs:` guard already ensures non-empty). Byte-identical: `agg.mean`
  pure path IS `sum/len`.
- `_native_loader.py`: add `mean`/`min`/`max` to `_COMPONENT_SYMBOLS` (functional gate)
  + `_GATED_ON` (doc).

## 4. Parity gate

Extend `tests/core/test_native_parity.py` (skip-when-unbuilt guard): `_native == _pure`
for `mean`/`min`/`max` across fixtures:
- `[1.0, 2.0, 3.0]`, a large random-ish array, negatives, a single element, empty (→0.0).
- **mean summation-order:** an array where naive vs pairwise sum would differ (e.g. a
  large value + many tiny values: `[1e16, 1.0, 1.0, ..., -1e16]`) — asserts the Rust
  naive sum byte-matches Python `sum()`. This is the fixture that proves §3.1's
  naive-summation requirement.
- min/max: exact `==` (finite arrays). NaN in min/max is out of scope (finite-only;
  the wired `match_rates` scores are finite) — documented, not fixtured.
Also a box-safe test that `match_rates` still emits the same `match.mean_pair_score`
through the pure path.

## 5. Rollout / docs

- PR stacked on `feat/goldenanalysis-core-wave1` (Wave 1 #1471 still queued; same files).
  If Wave 1 squash-merges first, rebase Wave 2 onto main. Rust is CI-built.
- native_symbols: the 3 new exports + `aggregate.py` `native_module().X` refs
  self-reconcile (added together); goldenanalysis enters the gate via #1468.
- CLAUDE.md / goldenanalysis docs: Wave 2 numeric reductions (all 3 surfaces incl.
  WASM); std deferred; cluster_dist/quality_rollup/regressions assessed trivial-not-cut.
- ruff on touched Python.

## 6. Risks

- **Summation order (mean) — the one real parity risk.** Resolved by naive
  `iter().sum()` (folds left-to-right from `0.0`, matching Python `sum()`) + the
  summation-order fixture (§4, `[1e16, 1.0, …, -1e16]`) — that fixture IS the proof,
  since `read_f64` returns values in Arrow (= list) order and the native list-comp
  builds the array in list order, so no reorder is possible. (histogram/quantile prove
  same-op-order float *arithmetic* parity in this crate but do no summation, so they
  don't cover this specific risk — the §4 fixture does.)
- **min/max NaN — fails SILENT, not loud.** Out of scope (finite-only; match_rates
  scores are finite). Direction for a future caller: `read_f64` drops Arrow nulls but
  NaN is a valid f64 (passes through), and Rust `fold(INFINITY, f64::min)` is
  NaN-*ignoring* while Python builtin `min` is order-dependent/NaN-propagating — so a
  future NaN-bearing column would silently diverge, not error. A future wiring needs an
  explicit NaN guard, not just a fixture.
- **std deferred** — a clean follow-on; noted so a future numeric-stats analyzer knows
  the pattern is ready.
- **Stacked-PR churn** — if Wave 1 squash-merges, rebase (the documented stacked-PR
  recovery); Wave 2's kernels are additive/independent of Wave 1's, so conflicts are
  only the shared-file adjacency (analysis-core/aggregate/_native_loader), mechanically
  resolvable.
