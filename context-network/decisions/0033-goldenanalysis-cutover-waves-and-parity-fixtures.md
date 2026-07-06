# 0033 — GoldenAnalysis: Rust-cutover waves + the cut-vs-fixture rule + Wave 1b deferral

**Status:** Accepted • **Shipped:** `#1472` (numeric reductions), `#1481` (frame-kernel fix + parity), `#1482` (quality/regressions parity) merged; `#1478` (cluster histogram) queued. Native `analysis-core` kernels: 9 gated primitives; four cross-surface fixtures.

## Context

The "Rust is the reference" program (ADR
[0031](0031-goldenflow-reference-mode-identifiers-wasm.md),
[0032](0032-goldenflow-duckdb-compiled-extension.md)) makes an owned pyo3-free
`-core` crate the single source of truth, with Python (native wheel + pure fallback)
and TypeScript/WASM as conforming surfaces proven byte-identical.

GoldenAnalysis had a toehold: `analysis-core` with `histogram` + `quantile` (ADR
[0014](0014-opt-in-wasm-acceleration.md) posture; measured 5.8-9.9x on Linux), plus a
Wave-1 frame-kernel cut (`null_ratio_per_column`/`duplicate_row_ratio`/`distinct_count`,
native-only). The question was how far to push the cutover across the remaining
aggregation surface and the cross-run/quality analyzers — and where a full three-surface
cutover is *not* the right tool.

## Decision

**Cut an analyzer/primitive into `analysis-core` only when it is MUSCLE with a CLEAN
primitive boundary; otherwise enforce cross-surface parity with a data-driven fixture.**

- **Cutovers (muscle + `Float64Array`-shaped):**
  - **Numeric reductions** `mean`/`min`/`max` — pure-slice `&[f64]` kernels; all three
    surfaces (Python native + WASM); `mean` uses naive `iter().sum()` to byte-match
    Python `sum()`.
  - **`cluster_size_histogram`** (counts of sizes ==1/==2/==3/>=4) — pure-slice, all
    three surfaces. Motivated by **anti-drift** (the bucket spec was hand-rolled
    identically in Python + TS), not speed; perf-neutral.
  - `_GATED_ON` / `_COMPONENT_SYMBOLS` now hold 9 primitives. Only histogram/quantile
    are claimed as perf wins; the frame/numeric/cluster kernels are single-sourcing.

- **Parity fixtures (trivial-compute and/or no clean boundary):**
  - **Frame-kernel equality semantics** — the kernels take *interned u64* (Arrow-specific
    canon) on the Rust side but *JSON.stringify* keys on the TS side; a `-0.0`/`NaN`/null
    /int-vs-float fixture locks them. This **found and fixed a real bug**: TS
    `duplicateRowRatio` conflated `NaN` and null (both serialized to JSON `null`),
    over-counting duplicates; its own `nUnique` already distinguished them.
  - **`quality.rollup`** — operates on heterogeneous finding *objects* and calls back
    into a GoldenCheck `health_score` *method*: no clean Rust boundary. Fixture locks the
    `Counter.most_common` tie ordering, unknown-check fallback, null-column filter, and
    metric order.
  - **`regressions`** — trivial rule logic (median of ≤7 floats + a compare); a 3-crate +
    WASM boundary would be disproportionate and there is no DuckDB/PG surface (host
    history math). Fixture locks baseline-strategy × direction × threshold edges.
  - Each fixture is a byte-identical copy in both packages' `tests/fixtures/`, locked by a
    Python and a TS test; inputs are JSON-safe (or code-mirrored where they hold
    `NaN`/`-0.0`).

- **Wave 1b (a WASM surface for the frame kernels) is consciously deferred**
  (`docs/superpowers/specs/2026-07-06-goldenanalysis-wave1b-deferred.md`). No clean WASM
  boundary — the interning + `canon_f64_bits` is Arrow-specific; bridging needs
  arrow-in-wasm (bloat + a JS conversion costlier than the dedup) or a second intern impl
  (new drift surface). In-browser frame-dedup is a speculative workload. Revisit only on a
  measured real one; arrow-in-wasm is then the thesis-pure path.

## Consequence

- A written rule for *when* to spend a cutover vs a fixture, so future analyzers aren't
  reflexively wrapped in three crates for no speed.
- The discipline that paid off: establish Python ground truth + a `node` mirror of the TS
  impl on adversarial inputs **before** writing a fixture. It caught the `duplicateRowRatio`
  NaN/null bug the benign `report_frame_summary` fixture never would have; the
  quality/regressions mirrors showed no divergence (lock, don't fix).
- GoldenAnalysis's analyzer surface is now covered end-to-end (native/WASM cutovers for
  the numeric+cluster muscle; cross-surface fixtures for frame-kernel semantics, quality,
  and regressions), with only the deferred Wave-1b WASM surface outstanding.
- Docs: `docs-site/goldenanalysis/native.mdx` + `overview.mdx` + the goldenanalysis
  `llms.txt` now distinguish the measured perf wins (histogram/quantile) from the
  single-sourced kernels, and record that the frame kernels have no WASM surface.
