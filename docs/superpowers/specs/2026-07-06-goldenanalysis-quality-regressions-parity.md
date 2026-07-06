# GoldenAnalysis: quality_rollup + regressions — parity-fixture, NOT Rust cutover

**Decision (2026-07-06):** lock the `quality.rollup` and `regressions` cross-surface
parity with data-driven fixtures. Do **not** cut them to `analysis-core`.

## Why fixtures, not a Rust cutover

The earlier "waves" moved analysis math into a shared Rust core so every surface is a
thin wrapper. These two do not qualify:

- **Neither has muscle.** `regressions` is `median` of ≤7 floats + a subtract/divide + a
  few comparisons; `quality_rollup` counts findings and sums `affected_rows`. A Rust
  cutover buys **zero speed**.
- **`quality_rollup` has no clean Rust boundary.** It operates on heterogeneous finding
  *objects* and calls back into GoldenCheck's `health_score` *method* — not a primitive
  boundary like the numeric kernels' `Float64Array`.
- **`regressions` would be disproportionate plumbing.** Wrapping
  `(current-baseline)/baseline*100` in 3 crates + Python dispatch + wasm bindings is
  absurd for three-line rule functions, and there is no DuckDB/PG surface for it (it's
  host-orchestrated history math).
- **The value here is *enforcement*, not single-sourcing by construction.** Both
  surfaces are already faithful mirrors; a cross-surface fixture enforces that parity
  cheaply — the same protection a cutover would give — and (per the frame-kernel work)
  fixtures actually catch latent drift.

## What shipped

Two fully data-driven fixtures (inputs are JSON-safe: finite floats, strings, plain
dicts), byte-identical copies in both packages' `tests/fixtures/`, locked by a Python
test and a TS test each:

- **`regressions_cases.json`** + `test_regressions_parity.py` /
  `regressions.parity.test.ts` — `baseline_value` / `delta_pct` / `is_regression` across
  even/odd median, `baseline==0`, negative baseline, threshold boundary (inclusive), all
  three directions, `window > history`, and empty history.
- **`quality_rollup_result.json`** + `test_quality_rollup_parity.py` /
  `qualityRollup.parity.test.ts` — the analyzer `{metrics, tables}` across the
  `Counter.most_common` tie ordering (count desc, ties in first-appearance order),
  unknown-check fallback, null-column filtering, findings + manifest rollup, and the
  metric array order.

## Verification / findings

Established Python ground truth + a `node` mirror of both TS implementations on the
adversarial scenarios **before** writing (the frame-kernel discipline). Unlike the frame
kernels (where `duplicateRowRatio` had a real NaN/null bug), **both were already faithful
mirrors — no divergence found.** So these fixtures *lock* the parity rather than fix a
live bug; they guard against future drift.

## Out of scope (documented)

The `quality.score` **health-score path** is not in the cross-surface fixture: it calls a
GoldenCheck `profile` method (Python keyword-arg call vs TS positional duck-typed call),
awkward to mock identically cross-surface. It stays covered by the per-surface unit
tests. All profile-free rollup logic is locked.

## Roadmap status

With this, GoldenAnalysis's analyzer set is covered: frame muscle (W1) + numeric
reductions (W2) + cluster-size histogram (W3, Rust cutover) + frame-kernel equality
semantics (parity fixture) + quality_rollup/regressions (parity fixtures). Wave 1b (WASM
for the frame kernels) remains consciously deferred
(`2026-07-06-goldenanalysis-wave1b-deferred.md`).
