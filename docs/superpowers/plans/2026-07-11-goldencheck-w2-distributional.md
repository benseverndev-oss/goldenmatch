# GoldenCheck W2 — distributional / format checks — Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Build three fused Arrow-native Rust kernels — `column_numeric_stats`+`count_outside` (range_distribution), `sequence_analysis` (sequence_detection), `date_freshness` (freshness) — in `goldencheck-core`, with PyO3 shims + loader registration + parity vs `PolarsColumn`. Shadow-wire the three full-scan profilers (compute kernel alongside, discard, keep Polars findings authoritative). No user-visible change.

**Spec:** `docs/superpowers/specs/2026-07-11-goldencheck-w2-distributional-design.md` (READ IT, incl. the "Review corrections" section — the B1/B2 blockers + should-fixes are binding).
**Base:** fresh `origin/main` (W0-land + CSV + W1 merged). Worktree `gc-w2`, branch `feat/goldencheck-w2-distributional`.

## Conventions
Rust: `export PATH="/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin:$PATH" CARGO_HOME=/d/.cargo RUSTUP_HOME=/d/.rustup`. Python: `export PYTHONPATH="D:/show_case/gc-w2/packages/python/goldencheck" POLARS_SKIP_CPU_CHECK=1 GOLDENCHECK_NATIVE=auto; PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe`. Native build: build the `goldencheck-native` crate (cdylib .dll -> copy to `_native.pyd` next to the loader, per prior waves). Ruff 100-char. **rustfmt only the files you touch by name** (not whole-crate `cargo fmt`). Verify each crate builds by grepping `^error` in the build output; `cargo clippy` + wasm check clean.

**INVARIANTS (every task):** kernels pyo3-free in `goldencheck-core`; shim in `goldencheck-native` (arrow=59); each kernel parity-locked vs `PolarsColumn` (register in `tests/core/parity_harness.py`); full-scan `Finding`s authoritative values UNCHANGED (Polars) — kernels run SHADOW; existing tests UNEDITED; `import goldencheck` zero polars; all prior native symbols (benford/keys/composite/fd/approx_fd/fuzzy/regex/str_to_date/csv_infer/column_aggregate) intact. Commit per task; don't push.

---

## Task 1: `column_numeric_stats` + `count_outside` kernel (range_distribution)

**Files:** `packages/rust/extensions/goldencheck-core/src/stats.rs` (new) + `lib.rs`; `packages/rust/extensions/goldencheck-native/src/stats.rs` (new) + `lib.rs`; `goldencheck/core/_native_loader.py` (`_COMPONENT_SYMBOLS`); Test `packages/python/goldencheck/tests/core/test_numeric_stats_parity.py` (new).

- [ ] **Step 1:** `goldencheck-core/src/stats.rs`: `pub struct NumStats { pub count_nonnull: usize, pub min: f64, pub max: f64, pub mean: f64, pub std: f64, pub sum: f64 }` + `pub fn column_numeric_stats(array: &dyn Array) -> NumStats` — ONE pass over Int*/UInt*/Float* (downcast per Arrow type; reuse `arrow_support` for null handling). `std` = SAMPLE std, **ddof=1** (`sqrt(sum((x-mean)^2)/(n-1))`), matching Polars `.std()`; guard `count_nonnull < 2 -> std=0/NaN` as Polars does (VERIFY what Polars returns for n<2 and match). **NaN/inf:** determine Polars' propagation for `min`/`max`/`mean`/`std` on float cols containing NaN/inf (test empirically) and match — min/max NaN handling differs from mean/std. Also `pub fn count_outside(array: &dyn Array, lower: f64, upper: f64) -> (usize, Vec<String>)` returning (count of values `< lower || > upper`, first-5 such values as strings **in array order, formatted per the array's dtype** — Int64 -> `"1"` not `"1.0"`). `#[cfg(test)]` tests: null/empty/single/all-same/NaN/inf/negative. `mod stats; pub use` in lib.rs.
- [ ] **Step 2:** `goldencheck-native/src/stats.rs`: `#[pyfunction] pub fn column_numeric_stats(array: PyArrowType<ArrayData>) -> (usize,f64,f64,f64,f64,f64)` + `#[pyfunction] pub fn count_outside(array: PyArrowType<ArrayData>, lower: f64, upper: f64) -> (usize, Vec<String>)`. Register both in native lib.rs. Add `"numeric_stats": ("column_numeric_stats", "count_outside")` to `_COMPONENT_SYMBOLS`.
- [ ] **Step 3:** Build both crates (grep `^error`), rustfmt touched files, clippy, wasm check. Build ext -> `_native.pyd`. Verify `column_numeric_stats`+`count_outside` + all prior symbols present.
- [ ] **Step 4:** Parity `tests/core/test_numeric_stats_parity.py`: for random+adversarial numeric `pl.Series` (int/uint/float, with/without nulls, NaN/inf, single, all-same), call native on `s.to_arrow()`, assert `count_nonnull==s.drop_nulls().len()`, `min/max==s.min()/s.max()` (exact for int; NaN-canonicalized for float), `mean/std` within epsilon of `s.mean()/s.std()` (ddof=1). For `count_outside`, compute Polars `lower=mean-3std, upper=mean+3std`, assert kernel `(count,sample)` == `s.filter( (s<lower)|(s>upper) )` count + first-5 `[str(v) for v in ...to_list()[:5]]`. Register `numeric_stats` in the parity harness with an explicit **float-epsilon divergence class for mean/std** (and NaN-canonicalizing compare — do NOT use bare `!=`). Both lanes.
- [ ] **Step 5:** Commit: `feat(goldencheck-core): W2 column_numeric_stats + count_outside kernel (parity w/ Polars, ddof=1)`.

## Task 2: `sequence_analysis` kernel (sequence_detection)

**Files:** `goldencheck-core/src/sequence.rs` (new) + `lib.rs`; `goldencheck-native/src/sequence.rs` (new) + `lib.rs`; `_native_loader.py`; Test `tests/core/test_sequence_analysis_parity.py` (new).

- [ ] **Step 1:** `goldencheck-core/src/sequence.rs`: `pub struct SeqStats { pub n_diffs: usize, pub unit_diff_count: usize, pub positive_diff_count: usize, pub is_sorted: bool, pub min: i64, pub max: i64, pub present_size: usize, pub gap_count: usize, pub gap_sample: Vec<i64> }` + `pub fn sequence_analysis(array: &dyn Array) -> Option<SeqStats>` over Int*/UInt* (return `None` if not int/uint or `count_nonnull < 2`). Reproduce sequence_detection.py: drop nulls -> `diffs[i] = v[i].wrapping_sub(v[i-1])` (**`wrapping_sub` — Polars diff wraps; plain `-` panics on overflow**); `n_diffs = diffs.len()`; `unit_diff_count = diffs==1`; `positive_diff_count = diffs>0`; `is_sorted` = Polars NON-STRICT ascending (v[i] >= v[i-1] for all i); `min/max`; `present` = HashSet of the values; `present_size = present.len()`; if `(max-min+1) > count_nonnull`: `gap_count`/`gap_sample` = first-10 of `(min..=max).filter(|v| !present.contains(v))` (in ascending order, matching `range(min,max+1)`); else gap_count=0, gap_sample empty. `#[cfg(test)]`: monotonic-no-gap / gapped / unsorted / duplicates / Int64 min&max (overflow) / single. `mod`+`pub use`.
- [ ] **Step 2:** `goldencheck-native/src/sequence.rs`: `#[pyfunction] pub fn sequence_analysis(array: PyArrowType<ArrayData>) -> Option<(usize,usize,usize,bool,i64,i64,usize,usize,Vec<i64>)>`. Register. Add `"sequence_analysis": ("sequence_analysis",)` to `_COMPONENT_SYMBOLS`.
- [ ] **Step 3:** Build (grep `^error`), rustfmt touched, clippy, wasm. Ext -> `.pyd`. Symbols present.
- [ ] **Step 4:** Parity `tests/core/test_sequence_analysis_parity.py`: build int `pl.Series` (tight-sequential, gapped, unsorted, dup, min/max i64), call native on `s.to_arrow()`, assert each SeqStats field matches the Polars computation the profiler does (`diff().drop_nulls()` + `count_eq(1)`/`count_gt(0)`/`is_sorted()`/`min`/`max`/`set(unique().to_list())` + `range` gaps `[:10]`). Register `sequence_analysis` in the harness (empty divergence — integer/bool exact). Both lanes.
- [ ] **Step 5:** Commit: `feat(goldencheck-core): W2 sequence_analysis kernel (wrapping diff, order-preserved, parity)`.

## Task 3: `date_freshness` kernel (freshness)

**Files:** `goldencheck-core/src/freshness.rs` (new) + `lib.rs`; `goldencheck-native/src/freshness.rs` (new) + `lib.rs`; `_native_loader.py`; Test `tests/core/test_date_freshness_parity.py` (new).

- [ ] **Step 1:** `goldencheck-core/src/freshness.rs`: `pub struct FreshStats { pub future_count: usize, pub max_epoch: i64 }` + `pub fn date_freshness(array: &dyn Array, now_epoch: i64) -> Option<FreshStats>` over Date32/Date64/Timestamp(any unit) (return `None` if empty/not temporal). `future_count` = count of non-null values `> now_epoch`; `max_epoch` = max non-null raw value. The kernel reads the RAW integer values in the array's native unit (Date32=i32 days -> widen to i64; Date64=i64 ms; Timestamp=i64 in its unit). `#[cfg(test)]`: all-past / some-future / all-null / empty / each temporal type. `mod`+`pub use`.
- [ ] **Step 2:** `goldencheck-native/src/freshness.rs`: `#[pyfunction] pub fn date_freshness(array: PyArrowType<ArrayData>, now_epoch: i64) -> Option<(usize,i64)>`. The shim must read the array's TimeUnit so the caller knows which unit to pass (document: caller matches unit). Register. Add `"date_freshness": ("date_freshness",)` to `_COMPONENT_SYMBOLS`.
- [ ] **Step 3:** Build (grep `^error`), rustfmt touched, clippy, wasm. Ext -> `.pyd`. Symbols present.
- [ ] **Step 4:** Parity `tests/core/test_date_freshness_parity.py`: build Date + Datetime `pl.Series` (all-past, some-future vs a fixed reference `now`), compute `now_epoch` **offset-free per B2** (Date32: `(ref_date - date(1970,1,1)).days`; Timestamp us: `(ref_dt - datetime(1970,1,1))//timedelta(microseconds=1)` — match `s.to_arrow()`'s actual unit, read it via pyarrow), call native on `s.to_arrow()`, assert `future_count == s.filter(s > ref).len()` (the profiler's `count_gt`) and `max_epoch` == the epoch of `s.max()`. Register `date_freshness` in the harness (empty divergence). Both lanes. **Do NOT use `datetime.timestamp()` (applies local offset).**
- [ ] **Step 5:** Commit: `feat(goldencheck-core): W2 date_freshness kernel (offset-free epoch, unit-matched, parity)`.

## Task 4: shadow-wire the three profilers + shadow tests

**Files:** `profilers/range_distribution.py`, `profilers/sequence_detection.py`, `profilers/freshness.py`; Tests `tests/engine/test_w2_shadow.py` (new).

- [ ] **Step 1 (range):** In `range_distribution.py`, when `native_enabled("numeric_stats")` and the profiler is about to emit findings, ALSO compute `column_numeric_stats(non_null.to_arrow())` + (for the outlier branch) `count_outside(non_null.to_arrow(), lower, upper)` where `lower/upper` are the **Polars-computed** `mean-3*std`/`mean+3*std` (B1). Discard the results (or assert-in-debug); the emitted `Finding`s STAY the Polars-computed ones. Guard: skip the shadow entirely if native absent. `non_null` here is already the cast+drop_null'd column.
- [ ] **Step 2 (sequence):** In `sequence_detection.py`, when `native_enabled("sequence_analysis")`, ALSO compute `sequence_analysis(non_null.to_arrow())` in shadow; discard. Authoritative findings unchanged.
- [ ] **Step 3 (freshness):** In `freshness.py`, INSIDE the existing `try:` block (S — after the successful `count_gt(now)`/`max()`), when `native_enabled("date_freshness")`, compute `now_epoch` offset-free (per B2, matching the column's Arrow unit) + `date_freshness(non_null.to_arrow(), now_epoch)` in shadow; discard. The tz-aware `except` path never reaches the kernel.
- [ ] **Step 4:** Shadow test `tests/engine/test_w2_shadow.py`: for fixture `pl.DataFrame`s (a numeric col with outliers, a gapped sequence int col, a date col with a future value), assert each kernel (on `col.to_arrow()`) MATCHES the Polars values the profiler computes (stats within epsilon; sequence fields exact; freshness future_count/max exact). This proves the shadow values are ready to be authoritative at the Flip.
- [ ] **Step 5:** Run the three profilers' existing tests UNEDITED green + the shadow test:
```bash
$PY -m pytest packages/python/goldencheck/tests -k "range_distribution or sequence or freshness or w2_shadow" -q
```
Ruff clean. Commit: `feat(goldencheck): W2 shadow-compute the 3 distributional kernels (authoritative findings unchanged)`.

## Task 5: final verification + PR

- [ ] Full targeted verification: all three parity tests (both lanes, native + fallback); the three profilers' findings UNCHANGED (existing tests unedited); shadow test green; `cargo test` (grep `^error`, no overflow panic); clippy + wasm clean; ruff; `import goldencheck` zero polars; ALL native symbols intact (benford/keys/composite/fd/approx_fd/fuzzy/regex/str_to_date/csv_infer/column_aggregate/numeric_stats/count_outside/sequence_analysis/date_freshness). Confirm NO user-visible change (shadow). PR to main (additive, no version bump), report the PR number back.

## Done criteria
- Three fused Arrow kernels (numeric_stats+count_outside, sequence_analysis, date_freshness) — Rust source of truth, parity-green vs PolarsColumn (mean/std epsilon class registered; NaN-canonicalized; sequence/freshness exact).
- All three registered in `_COMPONENT_SYMBOLS` + native lib.rs; shadow-wired into the three full-scan profilers with authoritative findings UNCHANGED; shadow test proves the match.
- Existing suite green; zero polars; no version bump; all prior symbols intact. W2 done — pattern extends to W3.
