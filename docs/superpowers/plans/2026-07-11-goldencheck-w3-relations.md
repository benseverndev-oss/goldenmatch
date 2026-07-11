# GoldenCheck W3 — relations (approx_duplicate + age_validation) — Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Build two fused Arrow-native Rust kernels — `duplicate_signatures` (approx_duplicate) + `age_mismatch` (age_validation) — in `goldencheck-core`, with PyO3 shims + loader registration + parity vs the Polars profilers. Shadow-wire both profilers (compute kernel alongside, discard, keep Polars findings authoritative). No user-visible change.

**Spec:** `docs/superpowers/specs/2026-07-11-goldencheck-w3-relations-design.md` (READ IT, incl. "Review corrections" — the no-intern BLOCKER, `is_string` mask, multi-array FFI signature, bit-exact age are binding).
**Base:** fresh `origin/main` (W0-land + CSV + W1; W2 enqueuing). Worktree `gc-w3`, branch `feat/goldencheck-w3-relations`.

## Conventions
Rust: `export PATH="/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin:$PATH" CARGO_HOME=/d/.cargo RUSTUP_HOME=/d/.rustup`. goldencheck-core is a STANDALONE crate (excluded from the extensions workspace) — run cargo from INSIDE `packages/rust/extensions/goldencheck-core`. Native ext: from `packages/rust/extensions/goldencheck-native` with `PYO3_PYTHON=/d/show_case/goldenmatch/.venv/Scripts/python.exe`. **CLIPPY MUST be `-D warnings`** (CI does; bare clippy misses `type_complexity`/`doc_lazy_continuation`) — run `cargo clippy -- -D warnings` (core) AND `cargo clippy --release -- -D warnings` (native ext). For wide tuple returns use a `type` alias with a PLAIN `//` comment (not `///`). Python: `export PYTHONPATH="D:/show_case/gc-w3/packages/python/goldencheck" POLARS_SKIP_CPU_CHECK=1 GOLDENCHECK_NATIVE=auto; PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe`. Native build: build the native cdylib release + copy the dll to `goldencheck/_native.pyd` (find how W1's `aggregate` shim built it; `.pyd` is untracked — don't stage). Ruff 100-char.

**Precedent to MIRROR:** multi-column FFI = `goldencheck-core/src/keys.rs` (`composite_key`/FD take `&[ArrayRef]`) + `goldencheck-native/src/keys.rs` (`Vec<PyArrowType<ArrayData>>` → `to_arrays()`). Single kernel + shim + parity = W1 `aggregate.rs` + `tests/core/test_column_aggregate_parity.py`. regex kernel = `src/regex.rs` (`str_replace_all`); date kernel = `src/date.rs` (`str_to_date`).

**INVARIANTS (every task):** kernels pyo3-free in goldencheck-core; arrow=59; parity-locked vs the Polars profiler (register in `tests/core/parity_harness.py`); full-scan `Finding`s UNCHANGED (shadow); existing relation tests UNEDITED; `import goldencheck` zero polars; all prior native symbols intact; clippy `-D warnings` clean BOTH crates. Commit per task; don't push.

---

## Task 1: `duplicate_signatures` kernel (approx_duplicate)

**Files:** `goldencheck-core/src/duplicate.rs` (new) + `lib.rs`; `goldencheck-native/src/duplicate.rs` (new) + `lib.rs`; `_native_loader.py`; Test `tests/core/test_duplicate_signatures_parity.py` (new).

- [ ] **Step 1:** `goldencheck-core/src/duplicate.rs`: `pub struct DupStats { pub exact_dup_rows: usize, pub exact_dup_groups: usize, pub near_dup_rows: usize, pub near_dup_groups: usize }` + `pub fn duplicate_signatures(columns: &[ArrayRef], is_string: &[bool]) -> DupStats`. Per row `i` (0..n_rows): build `exact_sig` = join with `\x1f` (0x1F) of, per column `c`, `cast_utf8(columns[c], i)` where null → `""` (fill_null FIRST). Build `norm_sig` = same, but if `is_string[c]`: normalize the cell = `s.to_lowercase()` (Rust std, Unicode) → `regex::str_replace_all`-equivalent `[^0-9a-z]+`→`" "` → `trim()`. **Build REAL strings — do NOT use `intern_column`** (null must collide with `""`; see spec BLOCKER). `cast_utf8`: Utf8/LargeUtf8 → the str; Int*/UInt* → decimal; Float* → Rust `{}` Display (NaN→"NaN"); Bool → "true"/"false"; Date32 → the value's ISO or just a deterministic form (injective, form doesn't matter for counts); Dictionary → resolve to its value string. Two `HashMap<String, usize>` passes (exact_counts, norm_counts) + a per-row `Vec<exact_sig>`/`Vec<norm_sig>` (or per-row exact-count lookup). Then: `exact_dup_rows` = Σ counts where ec≥2; `exact_dup_groups` = #distinct exact sigs with ec≥2; `near_dup_rows` = #rows where `nc≥2 AND ec<2` (ec = that row's exact count); `near_dup_groups` = #distinct norm sigs among those near rows. `#[cfg(test)]`: exact dups, near dups (case/space/punct), both, none, single-col, mixed-dtype, all-null, null-vs-empty-string-collide, "!!!"-normalizes-to-empty. `mod`+`pub use`.
- [ ] **Step 2:** `goldencheck-native/src/duplicate.rs`: `#[pyfunction] pub fn duplicate_signatures(field_arrays: Vec<PyArrowType<ArrayData>>, is_string: Vec<bool>) -> (usize,usize,usize,usize)` — mirror keys.rs `to_arrays()`. Register in native lib.rs. Add `"duplicate_signatures": ("duplicate_signatures",)` to `_COMPONENT_SYMBOLS`.
- [ ] **Step 3:** Build core (`cargo test` inside goldencheck-core, grep `^error`), clippy `-D warnings` (core). Build native ext + clippy `--release -D warnings`. Copy dll → `_native.pyd`. Verify `duplicate_signatures` + all prior symbols present.
- [ ] **Step 4:** Parity `tests/core/test_duplicate_signatures_parity.py`: for random+adversarial `pl.DataFrame`s (pure-string exact+near dups, int dups, mixed dtype, all-unique, single-col, null-vs-"" , Unicode-lowercase "İ"/"Σ", a bool col), compute `is_string = [dt == pl.Utf8 for dt in df.dtypes]`, call native `duplicate_signatures([df[c].to_arrow() for c in df.columns], is_string)`, assert the four counts == the profiler's Polars computation (replicate `_normalized_signature`/`_exact_signature` + group_by/join, or call the profiler's helpers). Register `duplicate_signatures` in the harness (**one exact component, empty divergence** — per spec, none expected; if a float fixture ever diverges, split it to `duplicate_signatures_floatedge`). Both lanes.
- [ ] **Step 5:** Commit: `feat(goldencheck-core): W3 duplicate_signatures kernel (owned signature-equality, parity w/ Polars)`.

## Task 2: `age_mismatch` kernel (age_validation)

**Files:** `goldencheck-core/src/age.rs` (new) + `lib.rs`; `goldencheck-native/src/age.rs` (new) + `lib.rs`; `_native_loader.py`; Test `tests/core/test_age_mismatch_parity.py` (new).

- [ ] **Step 1:** `goldencheck-core/src/age.rs`: `pub struct AgeStats { pub mismatch_count: usize, pub sample_indices: Vec<usize> }` + `pub fn age_mismatch(actual: &dyn Array, dob_epoch_days: &dyn Array, ref_epoch_days: i64) -> AgeStats`. `actual` = Float64Array (already cast Python-side); `dob_epoch_days` = Date32Array (days since epoch; already parsed Python-side). Per row `i`: `both_present = actual[i] not null AND dob[i] not null`; `expected = (ref_epoch_days - dob_days[i]) as f64 / 365.25`; `mismatch = both_present AND (actual[i] - expected).abs() > 2.0`. `mismatch_count` = count; `sample_indices` = first-5 mismatch row indices (ascending row order). `#[cfg(test)]`: matching ages, off-by>2, nulls, boundary exactly 2.0 (not mismatch), NaN age (not mismatch), empty. `mod`+`pub use`.
- [ ] **Step 2:** `goldencheck-native/src/age.rs`: `#[pyfunction] pub fn age_mismatch(actual: PyArrowType<ArrayData>, dob_epoch_days: PyArrowType<ArrayData>, ref_epoch_days: i64) -> (usize, Vec<usize>)`. Register. Add `"age_mismatch": ("age_mismatch",)` to `_COMPONENT_SYMBOLS`.
- [ ] **Step 3:** Build + clippy `-D warnings` both crates. Copy dll → `_native.pyd`. Symbols present.
- [ ] **Step 4:** Parity `tests/core/test_age_mismatch_parity.py`: build an age Float64 series + a DOB Date series + a fixed `reference_date`; `ref_epoch_days = (reference_date - date(1970,1,1)).days`; `dob_epoch = df["dob"].cast(pl.Date)` → `.to_arrow()` (Date32); call native `age_mismatch(actual.to_arrow(), dob_epoch.to_arrow(), ref_epoch_days)`; assert `mismatch_count` == the profiler's `int(((actual-expected).abs() > 2.0 & non_null).sum())` and `sample_indices` gather the same 5 values as `col_series.filter(mask).head(5)`. Cover matching, mismatched, nulls, boundary, NaN age, empty. Register `age_mismatch` in the harness (empty divergence — bit-exact). Both lanes.
- [ ] **Step 5:** Commit: `feat(goldencheck-core): W3 age_mismatch kernel (bit-exact age arithmetic, parity)`.

## Task 3: shadow-wire both profilers + shadow tests

**Files:** `relations/approx_duplicate.py`, `relations/age_validation.py`; Test `tests/engine/test_w3_shadow.py` (new).

- [ ] **Step 1 (approx_duplicate):** after the Polars group_by/join computes the four numbers, when `native_enabled("duplicate_signatures")`, ALSO compute `is_string = [dt == pl.Utf8 for dt in df.dtypes]` + `duplicate_signatures([df[c].to_arrow() for c in df.columns], is_string)` in SHADOW; discard. Emitted findings STAY Polars. Guard: skip if native absent.
- [ ] **Step 2 (age_validation):** inside the per-(age_col,dob_col) loop, after the Polars `df.select(...)` mismatch compute, when `native_enabled("age_mismatch")`, compute `ref_epoch_days` offset-free (`(reference_date - date(1970,1,1)).days`) + the DOB epoch array (parse via the SAME `dob_expr` the profiler uses, then `.to_arrow()`) + `actual.to_arrow()`, call `age_mismatch(...)` in SHADOW; discard. Findings STAY Polars.
- [ ] **Step 3:** Shadow test `tests/engine/test_w3_shadow.py`: for a fixture with (a) exact+near dup rows, (b) an age col mismatching a DOB col, assert the kernel counts/indices MATCH the Polars values the profilers compute. Skip gracefully if the relevant `native_enabled(...)` is False.
- [ ] **Step 4:** Run existing relation tests UNEDITED green + shadow:
```bash
$PY -m pytest packages/python/goldencheck/tests -k "approx_duplicate or age_validation or duplicate or w3_shadow" -q
```
Ruff clean. Commit: `feat(goldencheck): W3 shadow-compute the 2 relation kernels (authoritative findings unchanged)`.

## Task 4: final verification + PR

- [ ] Rebase onto fresh `origin/main` (W2 will have merged — verify diff is goldencheck-only, no unrelated goldenmatch files). Full targeted verification: both parity tests (both lanes); the two profilers' findings UNCHANGED (existing tests unedited); shadow test green; `cargo test` (grep `^error`); clippy `-D warnings` BOTH crates; wasm check; ruff; `import goldencheck` zero polars; ALL native symbols intact (prior + duplicate_signatures + age_mismatch). Confirm NO user-visible change (shadow). Push as `benzsevern` (unset GH_TOKEN; `gh auth switch`), PR to main (additive, no version bump), report the PR number.

## Done criteria
- Two fused Arrow kernels (duplicate_signatures, age_mismatch) — Rust source of truth, parity-green vs the Polars profilers (owned signature-equality contract; age bit-exact; empty divergence unless a float fixture forces a split component).
- Both registered in `_COMPONENT_SYMBOLS` + native lib.rs; shadow-wired into the two relation profilers with authoritative findings UNCHANGED; shadow test proves the match.
- Existing suite green; zero polars; no version bump; clippy `-D warnings` clean; all prior symbols intact. W3 done — the two HARD R4-declined relations now have owned kernels.
