# GoldenCheck W2 — distributional / format checks — design

Date: 2026-07-11
Status: wave design (Arrow fused-scan program; /goal "all Ws implemented"). Self-reviewed via subagent.
Program: `...-arrow-fused-scan-engine-program-design.md` + `...-W-path-scoping.md`. **W2.**
Base: fresh `origin/main` (W0-land + CSV + W1 merged: `arrow_support`, `column_aggregate`/`dtype_category`, parity harness, list-based regex+date kernels).

## Goal

Fused Arrow-native Rust kernels for the three **full-scan-only** distributional checks — **range_distribution, sequence_detection, freshness** — which today are Polars-bound: they route through the Frame seam (`to_frame` + `col.min()/max()/mean()/std()/diff()/is_sorted()/count_gt()/count_eq()/filter_outside()`), but `PyColumn` (the polars-free backend) implements ONLY `drop_nulls/unique/cast` — the numeric/sequence/date ops live only on `PolarsColumn`. So these three profilers run only in the full scan on Polars. W2 supplies the fused Arrow kernels that reproduce exactly those ops (Rust = source of truth), shadow-wired: parity vs `PolarsColumn`, authoritative output stays Polars until the Flip. **Deferred (NOT W2):** Arrow-ifying the S2.2 regex / S2.3 date kernels — encoding/format/pattern/temporal are ALREADY polars-free and working; converting their input container from `&[Option<String>]` to Arrow is a uniformity nicety for the WASM/SQL surfaces, not eviction, and rides a later cleanup wave.

## Scope (three fused kernels, one per full-scan check)

### A. `column_numeric_stats` (for range_distribution; reused by W4)
`column_numeric_stats(&dyn Array) -> NumStats { count_nonnull, min, max, mean, std, sum }` in `goldencheck-core` — ONE pass over a numeric Arrow array (Int*/UInt*/Float*), null-aware. Feeds `range_distribution`'s `min/max/mean/std` + the ±3σ outlier bounds. Parity vs `PolarsColumn.min()/max()/mean()/std()`. **std**: match Polars' default (sample std, ddof=1) EXACTLY — verify + register any float-epsilon divergence class. The outlier COUNT + first-5 sample (`filter_outside(lower, upper)`) is a thin follow-on: `count_outside(&dyn Array, lower, upper) -> (count, sample)` (or fold into the stats kernel's caller). This kernel is reused by W4's distribution-fit/z-score work.

### B. `sequence_analysis` (for sequence_detection)
`sequence_analysis(&dyn Array) -> SeqStats { n_diffs, unit_diff_count, positive_diff_count, is_sorted, min, max, present_set_size, gap_count, gap_sample }` over an ORDER-PRESERVED integer array (Int*/UInt* only, matching the profiler's `dtype in ("int","uint")` gate). Reproduces `diff().drop_nulls()` + `count_eq(1)` + `count_gt(0)` + `is_sorted()` + the `range(min,max+1) not in present` gap scan. **Order matters — the kernel iterates in array order, NEVER sorts.** Gap sample = first 10 missing values. Parity vs the profiler's PolarsColumn computation.

### C. `date_freshness` (for freshness)
`date_freshness(&dyn Array, now_epoch) -> FreshStats { future_count, max_epoch }` over a Date32/Date64/Timestamp array — reproduces `count_gt(now)` + `max()`. The caller keeps the name-gated staleness age-in-days arithmetic + tz-aware `count_gt` fallback (the `except Exception: return []` path stays Python). Parity vs `PolarsColumn.count_gt(now)/max()`.

## Wiring (shadow — mirrors W1)
- Each of the three profilers, when `native_enabled(...)`, ALSO computes its fused kernel in SHADOW (`col.to_arrow()` -> kernel) alongside the authoritative PolarsColumn compute. The emitted `Finding`s STAY the Polars-computed ones. A shadow test per check asserts kernel == PolarsColumn on a corpus.
- Register each new kernel in the W0-land parity harness (empty divergence for the integer/enum signals; register the float-epsilon `std`/`mean` divergence class explicitly if it appears).

## Contract / parity
- Numeric stats: `min`/`max`/`sum` exact; `mean`/`std` epsilon (float, order-free reduction — match Polars ddof=1; register the epsilon divergence class for `std`/`mean`). Outlier count/sample exact (integer count; sample = first-5 in array order).
- Sequence: all signals (n_diffs, unit/positive diff counts, is_sorted, gap_count, gap_sample) exact integer/bool — NaN-free (int/uint only).
- Freshness: `future_count` exact integer; `max_epoch` exact.
- Full-scan authoritative findings UNCHANGED (shadow); `scan_columns` untouched (these three profilers aren't in it); `import goldencheck` zero polars; existing tests UNEDITED.

## Testing
- Rust: each kernel over Arrow arrays — numeric (null/empty/single/all-same/NaN/inf for stats + outlier bounds); integer monotonic/gapped/unsorted/duplicate for sequence; Date32/Date64/Timestamp valid/future/all-past for freshness.
- Parity harness: each kernel == the `PolarsColumn` reference on random + adversarial fixtures (register each; `std`/`mean` epsilon divergence class if it appears).
- Python: full-scan range/sequence/freshness findings UNCHANGED (shadow); a shadow test per check asserts kernel == PolarsColumn on a corpus. Existing profiler/scanner tests UNEDITED green.
- `import goldencheck` zero polars; wasm/clippy clean; all prior native symbols (benford/keys/composite/FD/approx-FD/fuzzy/regex/date/csv_infer/column_aggregate) intact.

## Risks
- **std/mean float parity** — Polars' ddof + reduction order; match ddof=1, epsilon-tolerance, register the divergence class (first float-stat divergence — a preview of W4's statrs work).
- **sequence order-preservation** — must NOT sort (the profiler detects order-based gaps + `is_sorted`); the kernel iterates in array order. `present_set` for the gap scan is a distinct concern from `is_sorted`.
- **freshness tz/dtype** — the kernel takes an already-normalized `now_epoch` + operates on the Arrow temporal array's native unit; the tz-aware `count_gt` failure path (`except Exception`) stays Python (kernel only runs when the Polars path would succeed). Match the array's time unit (Date32=days, Timestamp=us/ns) to `now_epoch`.
- **arrow-rs lockstep** (standing) — same `arrow=59`.

## Review corrections (folded — spec review 2026-07-11)
- **[B1] `count_outside` MUST receive the Polars-computed `lower`/`upper`**, not derive its own ±3σ bounds from the kernel's (epsilon-divergent) mean/std — else a value near the boundary flips vs the Polars `filter_outside`. Shadow wiring passes the authoritative `lower=mean-3std`, `upper=mean+3std` (Polars-computed) into the kernel. Then outlier count/sample ARE exact.
- **[B2] `now_epoch` naive-encoding conversion is offset-free, per Arrow dtype.** Polars tz-naive `Datetime` exports `Timestamp(us, None)` = wall-clock microseconds, no tz shift. Convert `now` offset-free: Timestamp(us) = `(datetime.now() - datetime(1970,1,1)) // timedelta(microseconds=1)`; Date32 = `(date.today() - date(1970,1,1)).days`; Date64 = ms; match the array's actual `TimeUnit` (s/ms/us/ns) — the shim reads it and the caller passes `now_epoch` in that unit. (NEVER `.timestamp()` — it applies the local UTC offset.)
- **[S] tz-aware freshness guard:** the shadow kernel call sits INSIDE `freshness.py`'s `try:` block, AFTER the successful `count_gt(now)`/`max()` — so a tz-aware column that makes the Polars path bail (`except: return []`) never reaches the kernel (no divergence, kernel never runs on refused data).
- **[S] `diff()` overflow:** use `wrapping_sub` (Polars `Series.diff()` keeps Int64 and wraps; Rust plain `i64 - i64` PANICS in debug/`cargo test` on overflow — the Int64 min/max adversarial fixture hits it). Derive `unit_diff_count`/`positive_diff_count` from the wrapped diffs so both sides agree.
- **[S] NaN/inf stats parity:** the parity harness compares with `!=`, so `NaN != NaN` false-positives. Canonicalize NaN (compare via `math.isnan` / NaN→sentinel) + use tolerance for mean/std. Pin Polars' NaN/inf propagation for `min`/`max`/`mean`/`std` and match it in the kernel (min/max NaN handling differs from mean/std).
- **[S] outlier sample dtype:** key the sample's `str(v)` form off the ACTUAL array dtype (Int64 sample → `"1"` not `"1.0"`); the `mostly_numeric` cast branch legitimately yields a float array, so read the array type, don't assume float.
- **[S] loader/shim registration (mandatory):** add `range_distribution`/`sequence_analysis`/`date_freshness` (kernel symbol names) to `_COMPONENT_SYMBOLS` in `_native_loader.py` + register the modules in `goldencheck-native/src/lib.rs`, or `native_enabled(...)` is always False and the shadow never runs.
- **[note] parity-only fields:** `max_epoch` is parity-only (profiler keeps Polars `max()` for the date-typed staleness arithmetic); the parity check converts epoch↔date. `sequence` gap fields are conditional on `expected_count > total` (mirror the `range(min,max+1)` guard). `is_sorted` = Polars non-strict ascending. After the range cast branch, kernel input is the cast+`drop_nulls`'d float array (`non_null = col` at that point).

## Non-goals
- No histogram/percentile beyond min/max/mean/std (full distribution-fit is W4). No Arrow-ifying the regex/date string kernels (deferred cleanup). No changing user-visible output (shadow). No `scan_columns` change. No new checks. No polars-free wiring of these profilers (that flips at the Flip, like W1).
