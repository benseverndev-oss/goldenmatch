# GoldenCheck W1 — fused aggregate — Implementation Plan

> Use superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Build the fused Arrow-native `column_aggregate` kernel (`{len, null_count, n_unique_nonnull, dtype}` in one pass) + the neutral dtype vocabulary/`dtype_category` shim. Shadow-wire the full-scan column loop; convert the dtype gates. `scan_columns` untouched. No user-visible change.

**Spec:** `docs/superpowers/specs/2026-07-11-goldencheck-w1-fused-aggregate-design.md`.
**Base:** fresh `origin/main` (W0-land arrow-in-core + parity harness + CSV kernel). Worktree `gc-w1`, branch `feat/goldencheck-w1-fused-aggregate`.

## Conventions
Rust: `export PATH="/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin:$PATH" CARGO_HOME=/d/.cargo RUSTUP_HOME=/d/.rustup`. Python: `export PYTHONPATH="D:/show_case/gc-w1/packages/python/goldencheck" POLARS_SKIP_CPU_CHECK=1 GOLDENCHECK_NATIVE=auto; PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe`. Native build per prior waves (.dll->.pyd). Ruff 100-char.

**Contract (from spec):** `ColumnAgg{len, null_count, n_unique_nonnull, dtype: DtypeCat}` — ONE pass (bitmap null_count + one non-null hash set + O(1) dtype). uniqueness = n_unique_nonnull; cardinality = n_unique_nonnull + (1 if null_count>0); nullability = len+null_count. `dtype_category(arrow)` = the 8 neutral cats == `_neutral_dtype(pl.dtype)`. Kernel replaces COUNTS+dtype-gate only (not value passes).

**INVARIANTS:** native `column_aggregate` == Polars reductions (parity, empty registry); `dtype_category==_neutral_dtype` (8 cats); `scan_columns` UNCHANGED; full-scan `ColumnProfile` authoritative values UNCHANGED (Polars) — fused runs SHADOW; `inferred_type` string unchanged (neutral divergence deferred to Flip); existing tests UNEDITED; `import goldencheck` zero polars; regex/date/csv_infer/benford symbols intact. Commit per task; don't push.

---

## Task 1: `column_aggregate` + `dtype_category` Rust kernel + parity

**Files:** `goldencheck-core/src/aggregate.rs` (new), `lib.rs`; `goldencheck-native/src/aggregate.rs` (new) + `lib.rs`; `_native_loader.py` (`column_aggregate` component); Test `tests/core/test_column_aggregate_parity.py` (new).

- [ ] **Step 1:** `goldencheck-core/src/aggregate.rs`: `pub enum DtypeCat { Str, Int, Uint, Float, Date, Datetime, Bool, Other }` + `pub fn dtype_category(array: &dyn Array) -> DtypeCat` (Arrow type -> cat: Utf8/LargeUtf8->Str; Int*->Int; UInt*->Uint; Float*->Float; Date32/Date64->Date; Timestamp->Datetime; Boolean->Bool; else Other). `pub struct ColumnAgg { pub len: usize, pub null_count: usize, pub n_unique_nonnull: usize, pub dtype: DtypeCat }` + `pub fn column_aggregate(array: &dyn Array) -> ColumnAgg` — ONE pass: `len = array.len()`, `null_count = array.null_count()` (bitmap), `n_unique_nonnull` = hash the non-null values (downcast per type; for float, hash the bit pattern -- but MATCH Polars n_unique on NaN: test what Polars does + match, register a divergence if unmatchable), `dtype = dtype_category(array)`. `#[cfg(test)]` tests. `mod aggregate;` + `pub use` in lib.rs.
- [ ] **Step 2:** `goldencheck-native/src/aggregate.rs`: `#[pyfunction] pub fn column_aggregate(array: PyArrowType<ArrayData>) -> (usize, usize, usize, String)` returning `(len, null_count, n_unique_nonnull, dtype_str)` where dtype_str is the neutral string ("str"/"int"/.../"other"). Register in native lib.rs. Add `"column_aggregate": ("column_aggregate",)` to `_COMPONENT_SYMBOLS`.
- [ ] **Step 3:** Build both crates (grep `^error`), rustfmt/clippy, wasm check. Build ext (.dll->.pyd). Verify `column_aggregate` + benford/regex/date/csv_infer symbols present.
- [ ] **Step 4:** PARITY test `tests/core/test_column_aggregate_parity.py`: for random + adversarial columns (ints, floats incl NaN, strings, bools, dates, all-null, single, with/without nulls, heterogeneous-via-arrow), build a `pl.Series`, call native `column_aggregate(series.to_arrow())`, assert `len == len(s)`, `null_count == s.null_count()`, `n_unique_nonnull == s.drop_nulls().n_unique()`, `dtype_str == _neutral_dtype(s.dtype)`. Register `column_aggregate` in the parity harness (empty divergence — if NaN forces a divergence, register it explicitly). Both lanes.
- [ ] **Step 5:** Commit: `feat(goldencheck-core): W1 column_aggregate + dtype_category fused kernel (parity w/ Polars)`.

## Task 2: Python `dtype_category` mirror + convert the dtype gates (output-identical)

**Files:** `goldencheck/core/frame.py` (or a small `dtype.py`) — a `dtype_category()` helper; `engine/scanner.py` + `profilers/type_inference.py` — route their dtype gates through it.

- [ ] **Step 1:** Add a Python `dtype_category(pl_dtype_or_arrow) -> str` returning the 8 neutral cats. It must equal `_neutral_dtype` for pl dtypes (reuse/wrap `_neutral_dtype`) and match the Rust kernel. (This consolidates the shim; `_neutral_dtype` already exists — `dtype_category` is the single public entry.)
- [ ] **Step 2:** Convert the full-scan column-loop dtype gates in `scanner.py` (`is_string = col.dtype in (pl.Utf8, pl.String)` etc.) + `type_inference.py`'s dtype branch to call `dtype_category(...)` and compare to `"str"`/`"int"`/etc. This must be OUTPUT-IDENTICAL (type_inference already branches on `_neutral_dtype` values; the column-loop tuple checks map 1:1). Do the type_inference + column-loop sites ONLY (leave the other ~13 dtype sites with a tracking comment).
- [ ] **Step 3:** Run existing profiler/scanner tests -> UNEDITED green (output-identical):
```bash
$PY -m pytest packages/python/goldencheck/tests -k "type_inference or scanner or profilers" -q
```
Ruff clean. Commit: `refactor(goldencheck): W1 dtype_category shim + convert type_inference/column-loop gates (output-identical)`.

## Task 3: shadow-wire the full-scan column loop + shadow test

**Files:** `engine/scanner.py` (the `_scan_dataframe_impl` column loop); Test `tests/engine/test_column_aggregate_shadow.py` (new).

- [ ] **Step 1:** In the `_scan_dataframe_impl` column loop, when `native_enabled("column_aggregate")`, ALSO compute the fused kernel in SHADOW: `agg = native_module().column_aggregate(col.to_arrow())`. The AUTHORITATIVE `ColumnProfile` values STAY the Polars-computed ones (`null_count`, `n_unique`, `str(col.dtype)`). The shadow agg is computed + (optionally) logged/asserted in tests — do NOT change the emitted ColumnProfile. (Keep it cheap + guarded; if native absent, skip the shadow compute entirely.)
- [ ] **Step 2:** Shadow test `tests/engine/test_column_aggregate_shadow.py`: for a fixture `pl.DataFrame`, assert that for each column `column_aggregate(col.to_arrow())` MATCHES the Polars `ColumnProfile` values the loop computes (`null_count`, `n_unique_nonnull == non_null.n_unique()`, `dtype_category == _neutral_dtype`). This proves the fused values are ready to become authoritative at the Flip.
- [ ] **Step 3:** Run + verify the full scan's OUTPUT is unchanged (ColumnProfile authoritative values identical):
```bash
$PY -m pytest packages/python/goldencheck/tests/engine/test_column_aggregate_shadow.py -v
$PY -m pytest packages/python/goldencheck/tests -k "scan_dataframe or scan_file or scanner" -q
```
Existing scan tests UNEDITED green. Ruff clean. Commit: `feat(goldencheck): W1 shadow-compute column_aggregate in the full-scan loop (authoritative unchanged)`.

## Task 4: final verification + PR

- [ ] Full targeted verification: parity (both lanes); scan_columns UNCHANGED (its tests unedited); full-scan ColumnProfile unchanged; dtype-gate sites output-identical; cargo test; wasm check; ruff; import gate; all native symbols intact. Confirm NO user-visible change (shadow). PR to main (additive, no version bump), arm auto-merge.

## Done criteria
- `column_aggregate` + `dtype_category` fused kernel (Rust source of truth), parity-green vs Polars (empty registry, NaN handled/registered).
- Neutral dtype vocabulary + `dtype_category` shim landed; type_inference + column-loop gates converted (output-identical).
- Full-scan loop shadow-computes the fused agg; authoritative ColumnProfile UNCHANGED; shadow test proves the match.
- scan_columns untouched; existing suite green; zero polars; no version bump. Foundation + pattern ready for W2.
