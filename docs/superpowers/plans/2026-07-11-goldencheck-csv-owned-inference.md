# GoldenCheck CSV owned-inference — Implementation Plan

> Use superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Read CSV into typed columns WITHOUT Polars via goldencheck's owned inference contract. Rust kernel = source of truth (arrow-native), Python reference = parity fallback. Additive: polars-present unchanged; polars-absent gains CSV (was ImportError).

**Spec:** `docs/superpowers/specs/2026-07-11-goldencheck-csv-owned-inference-design.md` (the inference contract + shadow discipline are there).

**Base:** fresh `origin/main` (has W0-land arrow-in-core + parity harness). Worktree `gc-csv`, branch `feat/goldencheck-csv-owned-inference`.

## Conventions
Rust: `export PATH="/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin:$PATH" CARGO_HOME=/d/.cargo RUSTUP_HOME=/d/.rustup`.
Python: `export PYTHONPATH="D:/show_case/gc-csv/packages/python/goldencheck" POLARS_SKIP_CPU_CHECK=1 GOLDENCHECK_NATIVE=auto; PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe`. Native build per prior waves (.dll->.pyd). Ruff 100-char.

**The contract (from spec):** per column over non-empty cells; all-null col -> str; precedence int->float->bool->str; int = `^-?[0-9]+$` fit i64 & NOT leading-zero-multidigit; float = decimal/sci & not-leading-zero & not inf/nan, COERCE all to Python float; bool = {true,false} case-insensitive; else str. empty ""->null (nulls coexist with any type). No date inference.

**INVARIANTS:** polars-PRESENT reader/scanner tests pass UNEDITED (one polars-absent CSV test flips to expect the owned dict — intended); `import goldencheck` zero polars; Rust infer == Python reference on identical cell matrices (empty divergence registry). Commit per task; don't push.

---

## Task 1: Python reference owned-inference (the contract + fallback)

**Files:** `goldencheck/engine/csv_infer.py` (new); Test `tests/engine/test_csv_infer.py` (new).

- [ ] **Step 1:** Write failing tests for the contract: a function `infer_and_type(cells: list[list[str]], header: list[str]) -> dict[str, list]` (takes pre-tokenized cells + header, returns owned-typed dict). Cases: int col; leading-zero `"01234"`->str col; single `"0"`->int; float col (coerced all-float incl `"5"`->`5.0`); `inf`/`nan`->str; `"5."`/`"+5"`->str; bool `true/False`->bool; `0/1`->int (not bool); mixed->str; empty ""->null; all-empty col->str-all-None; nulls coexist (`["1","","3"]`->[1,None,3] int). Assert exact typed lists.
- [ ] **Step 2:** Run -> FAIL. Implement `csv_infer.py`: `_infer_col_type(values) -> type-tag` applying the contract (return "int"/"float"/"bool"/"str"); `_coerce(values, tag) -> list` (empty->None; int->int; float->float(v) for ALL; bool->parse; str->keep). `infer_and_type(cells, header)` builds the dict. Also `read_csv_owned(path) -> dict[str,list]`: tokenize via stdlib `csv` (utf-8 then latin-1 fallback, mirroring `_read_csv_columns`), header=first row, then `infer_and_type`.
- [ ] **Step 3:** Run -> PASS. Ruff clean. Commit: `feat(goldencheck): CSV owned-inference Python reference (engine/csv_infer.py)`.

## Task 2: Rust CSV inference kernel (source of truth, arrow-native)

**Files:** `goldencheck-core/Cargo.toml` (+`csv`), `goldencheck-core/src/csv_infer.rs` (new), `lib.rs`; `goldencheck-native/src/csv_infer.rs` (new) + register in `lib.rs`; loader `_COMPONENT_SYMBOLS["csv_infer"]`.

- [ ] **Step 1:** Rust kernel `csv_infer_columns(csv_bytes: &[u8], delimiter: u8) -> Result<(Vec<String>, Vec<ColumnData>)>` (or return a form the shim turns into a Python dict). Tokenize with the `csv` crate; apply the SAME contract as the Python reference (int/float/bool/str, leading-zero guard, inf/nan, coerce). Emit typed columns (as Arrow arrays OR as a tagged enum the shim converts). Add `csv = "1"` to core Cargo.toml (pure Rust, WASM-safe). `#[cfg(test)]` tests for the contract cases.
- [ ] **Step 2:** Native shim `goldencheck-native/src/csv_infer.rs`: `#[pyfunction] csv_infer_columns(csv_bytes: Vec<u8>, delimiter) -> PyObject` returning a Python dict[str, list] (or a structure the Python side turns into one). Register in native `lib.rs`. Add `"csv_infer": ("csv_infer_columns",)` to `_native_loader._COMPONENT_SYMBOLS`.
- [ ] **Step 3:** Build both crates (grep `^error`), rustfmt/clippy. Build ext (.dll->.pyd). Verify symbol present + regex/date/benford still present.
- [ ] **Step 4:** PARITY on inference (harness): assert the Rust kernel's typed output == the Python reference's `infer_and_type` given identical pre-tokenized cells, on random + adversarial fixtures (empty divergence registry). Register `csv_infer` in the parity harness. Also an end-to-end parse+infer parity test on WELL-FORMED CSV only.
- [ ] **Step 5:** Commit: `feat(goldencheck-core): CSV owned-inference Rust kernel (arrow-native, parity w/ Python reference)`.

## Task 3: shadow integration + differential snapshot + zero-polars proof

**Files:** `goldencheck/engine/reader.py` (the `read_columns` CSV branch + a `_read_csv_columns_owned`); Test `tests/engine/test_read_columns.py` (flip the one test), `tests/engine/test_csv_owned_differential.py` (new).

- [ ] **Step 1:** `_read_csv_columns_owned(path) -> dict`: use the native `csv_infer` kernel when `native_enabled("csv_infer")`, else the Python `read_csv_owned` reference. In `read_columns`, CSV branch: `if polars importable -> _read_csv_columns (unchanged); else -> _read_csv_columns_owned`. (Keep `_read_csv_columns` for the polars-present path byte-identical.)
- [ ] **Step 2:** FLIP the one test: `test_read_columns_parquet_excel_are_polars_free` currently asserts CSV raises ImportError under a polars block -> change to assert `read_columns(csv)` returns the owned-typed dict (e.g. an int column typed, a leading-zero column str) + `"polars" not in sys.modules`.
- [ ] **Step 3:** Differential-vs-Polars snapshot `tests/engine/test_csv_owned_differential.py` (polars present): a corpus CSV; assert owned inference vs `pl.read_csv().to_dict()` and DOCUMENT each delta (leading-zero str vs int; inf/nan str vs float; `"5."`/`"+5"` str vs float; else equal). This test makes the divergence explicit.
- [ ] **Step 4:** `"other"`-dtype smoke: `scan_columns(read_columns(all_empty_col_csv))` returns cleanly (no crash). And `scan_columns(read_columns(owned_csv))` runs the covered checks.
- [ ] **Step 5:** Run: the flipped test + differential + smoke + import gate + existing reader tests (polars-present UNEDITED):
```bash
$PY -m pytest packages/python/goldencheck/tests/engine/test_read_columns.py packages/python/goldencheck/tests/engine/test_csv_owned_differential.py packages/python/goldencheck/tests/test_import_no_polars.py -v
$PY -m pytest packages/python/goldencheck/tests -k "reader or csv" -v
```
Ruff clean. Commit: `feat(goldencheck): CSV owned path when polars absent (shadow, additive) + differential snapshot`.

## Task 4: final verification + PR

- [ ] Full targeted verification (both native + fallback lanes for the parity; polars-present suite unedited; cargo test; ruff; import gate; version unchanged — additive minor). PR to main, arm auto-merge.

## Done criteria
- Owned CSV inference (Rust kernel + Python reference, parity green empty-registry) reads CSV polars-free per the contract.
- `read_columns(csv)` works polars-absent (owned), unchanged polars-present; one test flips (intended); differential-vs-Polars snapshotted.
- `import goldencheck` zero polars; polars-present tests unedited; existing suite green. Additive (no version bump; the default-flip is the future Flip wave).
