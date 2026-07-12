# GoldenCheck fused per-column kernel — design

Date: 2026-07-12
Status: wave design of the Arrow fused-scan program — realizing the "FUSED" the program was named for. Grounds: measured pass inventory (~8 passes/column) + the per-profiler reduction recon (2026-07-12).
Base: perf work merged/queued (3.0.1 vectorized cast, 3.0.2 vectorized string ops + column caching, 3.0.3 parallel scan). Branch `perf/goldencheck-fused-column-kernel`.

## Problem

After the 3.0.x perf passes the scan makes each vectorized op fast and runs columns in parallel, but it still does **N separate passes per column** over the same bytes (measured: ~8/column; e.g. a string column does 2 casts + up to 7 regex match-counts + n_unique + drop_nulls, each a distinct scan). Polars' 0.33s (vs our 1.36s) comes from its query optimizer fusing those into far fewer materializations. The program design's original headline — "one column's ~13 checks in 1-2 shared passes" — was never built; the W-waves shipped individual kernels and the Flip wired them through the seam method-by-method.

## Architecture — fused digest, memoized on `ArrowColumn` (profilers UNCHANGED)

Do NOT rewire the profilers. Instead: the **first** seam reduction on a column triggers ONE fused Rust kernel pass that computes everything that column's dtype needs; the result is cached on the `ArrowColumn`, and every subsequent seam method reads from the cache. This extends the existing `_num_stats`/`_n_unique`/`_dropna` memoization to a full digest. The `Column` seam is unchanged, so the profilers, the differential harness, and the Jaccard-1.0 gate are all unchanged — the kernel computes the SAME quantities, in one pass.

Per-dtype fused kernels in `goldencheck-core` (arrow-in, matching the existing kernels), PyO3-shimmed in `goldencheck-native`, gated by `native_enabled(...)` with the current pyarrow.compute path as the fallback (accelerator-not-requirement).

### The digest structs (from the recon reduction table)
- **Universal core** (every dtype): `count`, `null_count`, `n_unique` (+ capped `distinct_values`) — satisfies nullability + uniqueness + cardinality in one pass.
- **Numeric** (`column_numeric_stats` already fuses the 6 stats): fold `n_unique` into that SAME streaming pass (build the distinct hashset while accumulating min/max/sum/sumsq). Non-fusable tail stays separate: outlier sample (2-pass, needs mean/std), sequence gaps (needs materialized distinct-set).
- **String** (the BIG win — ~10 passes today): ONE pass computing `null_count`, `n_unique` (hashset), `float_castable_count`, `int_castable_count`, and `match_count[pattern]` for the 7 fixed patterns (email/phone/url + 4 encoding patterns). `str::to_lowercase`-free; regex crate (matches pyarrow RE2 on these fixed patterns — differential-gated). Skeleton value-counts (pattern_consistency) stays a 2nd derived pass; fuzzy clustering stays external.
- **Date/datetime**: `count`, `null_count`, `n_unique`, `max`, `count_gt(now)` — one pass (`now` passed in at the column's granularity).
- **Bool / other**: universal core only.

### Materialized-value tails (unchanged, kept separate)
Outlier samples, format/encoding sample rows, pattern skeleton groups, cardinality distinct samples, sequence gaps, drift per-half sets, fuzzy clusters — these need rows, not scalars. They remain their own (fast, vectorized) passes; the fused kernel does NOT try to produce them. It only fuses the SCALAR reductions that dominate the pass count.

### Two cross-profiler dependencies to preserve
1. `type_inference` writes `context[column]["mostly_numeric"]` (gates range_distribution on string cols). The fused string digest emits `float_castable_count`, so the promotion decision reads the digest — no re-scan.
2. `drift_detection` needs per-HALF aggregates (split at `total//2`) — a whole-column pass can't produce these. Drift stays on its own slice-then-reduce path (it also has a `total < 1000` gate, so it rarely fires on the small end).

## Phasing (measure + differential-1.000 gate each)
- **Phase 1 — STRING digest** (biggest ROI, self-contained): the fused string kernel + wire `ArrowColumn` to compute+cache it and have `str_match_count`/`cast('float'/'int')`/`n_unique`/`null_count` read from it for string columns. Collapses ~10 string passes -> 1. Ship + measure.
- **Phase 2 — NUMERIC digest**: fold `n_unique` (and optionally `is_sorted`/diff-counts) into `column_numeric_stats`'s pass. Smaller (numeric already fuses 6). Ship + measure only if Phase 1 ROI justifies.
- **Phase 3 — date/bool/other**: universal-core digest. Marginal; do only if measured.

## Correctness / contract
- The fused kernel MUST produce byte/epsilon-identical scalars to the current per-method path. Gate: the finding-set differential (strict Jaccard 1.000) + the ArrowColumn parity tests, run with the fused path ON and OFF. Any divergence = revert/fix, not ship.
- RE2-vs-regex-crate on the 7 fixed patterns: the differential is the arbiter; the encoding `\uXXXX` patterns already route to the kernel today, so parity there is expected.
- Native-absent: pyarrow.compute fallback path unchanged (accelerator-not-requirement).
- Determinism preserved (pure per-column function; composes with the 3.0.3 thread pool).

## Risks
- **ROI**: caching already deduped n_unique/stats; the win is concentrated in string columns' cast+regex passes. Phase 1 measures whether it's worth Phases 2-3. Be honest if it plateaus.
- **Kernel scope creep**: keep the kernel to SCALAR reductions; never try to fuse the materialized-value tails.
- **New kernel symbol wheel skew**: a depended-on new symbol must ship in the published wheel (the #688 lesson) — but in-tree build covers dev; the fallback keeps it correct if absent.

## Non-goals
No profiler rewrite. No fusing materialized-value passes. No new numerics. No change to the owned contract / finding set.
