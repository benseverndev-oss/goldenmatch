# 0036 — GoldenMatch: polars becomes optional; the engine is Arrow-native

**Status:** Accepted • **Shipped:** 2026-07-13 (goldenmatch 3.1.0, PRs #1720–#1736)

## Context

The ZERO-Polars program (spec 2026-07-09, ADR-era waves W0–W5) shipped 3.0.0
with Arrow-native results and an arrow-default Frame lane, but the pipeline
SPINE still materialized polars on both lanes and ~11 of 13 feature classes
declined the Frame lane. Every declined feature fell back to polars — a
fallback that cannot exist once polars leaves the dependency set.

## Decision

1. Descend the spine (D2s series): the collect boundary hands the engine a
   seam Frame; every post-collect consumer is dual-rep.
2. Widen the lane (W-1..W-7): every feature class runs ON the Frame lane —
   the eligibility predicate ends with zero feature declines.
3. Port feature compute to Arrow (A1–A9): integrations via their own arrow
   surfaces (goldencheck 3.x scan, goldenflow adapter), everything else via
   the seam; the golden survivorship ORACLE is seam-native.
4. **Polars-present paths are WALL OPTIMIZATIONS, not requirements**: the
   golden fast-columnar builder, vectorized survivorship, and the
   large-scale pair-score join light up when polars is installed
   (`goldenmatch[polars]`) and stay byte-identical to 3.0.x. Without polars,
   seam-native routes carry the run — the spec's correct-but-slower
   Arrow+Python fallback lane.
5. PolarsFrame and the classic lane are deliberately NOT deleted (deviation
   from the original "delete" phrasing): they are the optimization substrate.
6. Kernel candidates (K1 precompute chain, K2 fused prep) resolved as a
   MEASURED NO-GO: 1M frame-lane profile put the precompute at 1.1s / 13.9%
   of wall — the wall lives in scoring/clustering, already kernel-owned.

## Consequence

- `pip install goldenmatch` carries no polars; a zero-polars CI gate
  (`tests/test_zero_polars_gate.py`) runs a full dedupe with polars imports
  BLOCKED and is the invariant's arbiter (it caught three web-surface
  regressions the static inventory missed).
- A bridge-count tripwire (`tests/test_bridge_ledger.py`) pins the exact
  polars re-entry sites (21 → 6 over the A-series) so no bridge lands
  silently.
- The profiler-driven hotspot loop that followed took the 1M frame-lane wall
  7.6s → 5.5s in pure Python — validating the K-series no-go.

- **Post-release correction (same day):** the "polars as wall optimization"
  framing was falsified by a head-to-head — the polars-free lane measured
  FASTER (7.11s vs 7.55s at 500K) once a module-level polars literal in
  golden_fused.py stopped silently disabling the Rust golden kernel on
  polars-free installs. The [polars] extra is a COMPATIBILITY surface
  (classic lane, kernel-absent golden replay, cell-quality weighting), not
  an accelerator. The zero-polars gate runs native-ON too now.
