# GoldenCheck W2 — distributional / format checks — design

Date: 2026-07-11
Status: wave design (Arrow fused-scan program; /goal "all Ws implemented"). Self-reviewed via subagent.
Program: `...-arrow-fused-scan-engine-program-design.md` + `...-W-path-scoping.md`. **W2.**
Base: fresh `origin/main` (W0-land + CSV + W1 merged: `arrow_support`, `column_aggregate`/`dtype_category`, parity harness, list-based regex+date kernels).

## Goal

Fused Arrow-native Rust kernels for the distributional/format checks — **range_distribution, sequence_detection, freshness** (full-scan-only today, on Polars) + **encoding_detection, format_detection, pattern_consistency** (already polars-free via the S2.2 regex kernel; W2 Arrow-ifies that kernel for uniformity). Rust = source of truth; Polars/list-path = parity oracle + fallback. **Shadow**: no user-visible change; the full-scan authoritative output stays Polars until the Flip.

## Scope (three coherent pieces)

### A. Fused numeric-stats kernel (for range_distribution; reused by W4)
`column_numeric_stats(&dyn Array) -> NumStats { count_nonnull, null_count, min, max, mean, std, sum }` in `goldencheck-core` — ONE pass over a numeric Arrow array (Int*/UInt*/Float*), null-aware. Feeds `range_distribution` (min/max/mean/std bounds + outlier detection). Parity vs the Polars `col.min()/max()/mean()/std()`. **std**: match Polars' default (sample std, ddof=1) EXACTLY — verify + register any float-epsilon divergence. This kernel is reused by W4's distribution-fit/z-score work.

### B. Arrow-ify the string + date kernels (encoding/format/pattern/freshness/temporal uniformity)
The S2.2 `regex` kernel (`str_contains_count`/`str_filter_mask`/`str_replace_all`, currently `&[Option<String>]`) and the S2.3 `date` kernel (`str_to_date`, `&[Option<String>]`) get **Arrow-in** entry points taking `&dyn Array` (StringArray/LargeStringArray), reusing `arrow_support`. Keep the existing list-based fns as thin wrappers (the PyColumn / native-shim callers are UNCHANGED — same behaviour, added Arrow path). This makes the string checks Arrow-in-core uniform (needed for the fused full-scan + WASM/SQL). Behaviour byte-identical (the regex/parse logic is unchanged; only the input container differs). Parity: Arrow path == list path on identical values.

### C. Sequence + freshness kernels
- **sequence_detection**: gap/monotonicity over an ORDER-PRESERVED numeric (or date) column — detects missing values in a sequence / non-monotonic ordering. A kernel `sequence_gaps(&dyn Array) -> {is_monotonic, gap_count, ...}` matching the profiler's current Polars logic (READ `sequence_detection.py` for the exact signal — likely `diff()` + gap detection). Order matters (no sort).
- **freshness**: future-dated timestamps (always-on) + name-gated staleness. Reuses the (now Arrow) `str_to_date` + a numeric date-compare (max date vs "now"/reference). READ `freshness.py` for the exact checks; the date arithmetic is the kernel.

## Wiring (shadow)
- **encoding/format/pattern (scan_columns, already polars-free):** route the regex calls through the Arrow entry point when the input is Arrow; the list path stays for PyColumn. Byte-identical (parity-locked) — scan_columns output UNCHANGED.
- **range/sequence/freshness (full-scan-only, `_scan_dataframe_impl` -> the profilers on `pl.Series`):** compute the new kernels in SHADOW (`col.to_arrow()` -> kernel), CI-compare to the profiler's Polars-computed findings, but the authoritative findings STAY Polars until the Flip. A shadow test per check asserts the kernel's stats/signals match the Polars ones.
- Register each new kernel in the W0-land parity harness (empty divergence, or register float-epsilon std divergence explicitly).

## Contract / parity
- Numeric stats: `min`/`max`/`sum` exact; `mean`/`std` epsilon (float, order-free reduction — match Polars ddof=1; register the epsilon divergence class for `std`/`mean`).
- String kernels: Arrow path == list path == Polars regex (byte-identical; the regex crate is unchanged).
- Sequence/freshness: the integer signals (gap_count, future-dated count) exact; any float compare epsilon.
- `scan_columns` output UNCHANGED (encoding/format/pattern parity-locked); full-scan authoritative UNCHANGED (shadow); `import goldencheck` zero polars; existing tests UNEDITED.

## Testing
- Rust: each kernel over Arrow arrays (null/empty/single/edge; numeric NaN/inf for stats; string unicode for regex; date valid/invalid for freshness; monotonic/gapped for sequence).
- Parity harness: each kernel == the Polars/list reference on random+adversarial fixtures (register; std epsilon if needed).
- Python: encoding/format/pattern `scan_columns` tests UNEDITED (Arrow regex path parity-locked); full-scan range/sequence/freshness findings UNCHANGED (shadow); shadow tests assert kernel==Polars per check.
- `import goldencheck` zero polars; wasm/clippy clean; all prior native symbols intact.

## Risks
- **std/mean float parity** — Polars' ddof + reduction order; match ddof=1, epsilon-tolerance, register the divergence class (this is the first float-stat divergence, a preview of W4).
- **Arrow-ifying regex/date without breaking the list callers** — keep the list fns as wrappers; parity Arrow==list.
- **sequence order-preservation** — must NOT sort (the profiler detects order-based gaps); the kernel iterates in array order.
- **W2 is large (6 checks, 3 pieces)** — if a single PR is unwieldy, split into W2a (numeric-stats + Arrow-ify string/date kernels) and W2b (sequence + freshness); each shadow/additive.

## Non-goals
- No histogram/percentile beyond min/max/mean/std (full distribution-fit is W4). No changing user-visible output (shadow). No `scan_columns` behavior change. No new checks.
