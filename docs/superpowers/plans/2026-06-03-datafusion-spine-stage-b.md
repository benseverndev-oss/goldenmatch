# DataFusion Spine — Stage B: scorer as FFI ScalarUDF Implementation Plan

> **For agentic workers:** REQUIRED: superpowers:subagent-driven-development.
> Checkbox (`- [ ]`) steps. **CI-validated posture:** dev box HANGS on Python
> `import` of goldenmatch/polars/datafusion. Subagents validate Python via `ruff
> check` + `python -m py_compile` ONLY, Rust via `cargo check` (long timeout; the
> local rustc sysroot may be corrupted — if `cargo check` ICEs on deps, defer to
> CI and say so). NEVER import/pytest/uv/pyright. Real tests in CI. Branch off
> the Stage-A branch `feat/datafusion-ffi-udf` (the proven FFI scaffold);
> benzsevern auth, NEVER benzsevern-mjh. `docs/superpowers/` gitignored.

**Goal:** Replace the Stage-A `add_one` FFI UDF with the real native string
scorers (jaro_winkler / token_sort / levenshtein) as `FFI_ScalarUDF`s over
`(Utf8, Utf8) -> Float64`, with the scorer algorithms in a SHARED crate so the
FFI UDF score equals the in-process native score BY CONSTRUCTION.

**Architecture:** Extract `score.rs`'s pure scorer fns into a new
`goldenmatch-score-core` lib crate; `_native` and `datafusion-udf` both depend on
it (single source of truth). The FFI UDFs downcast two `StringArray` args, call
score-core per row, return a `Float64Array`.

**Tech Stack:** Rust (rapidfuzz 0.5.0, datafusion-ffi 53.x, arrow 58, pyo3 0.28),
Python datafusion 53, PyArrow.

**Spec:** `docs/superpowers/specs/2026-06-03-datafusion-spine-design.md` (Stage B).
**Stage A (DONE, GREEN #698):** the FFI boundary works on v53.

---

## Reference (the native scorers being shared — `packages/rust/extensions/native/src/score.rs`)

- `jaro_winkler_similarity(a:&str,b:&str)->f64` (:81), `levenshtein_similarity` (:87),
  `token_sort_ratio` (:94), `token_sort_string(s)` (:71), `score_one(scorer_id:u8,
  a,b)->f64` (:104) — all pure, backed by `rapidfuzz = "0.5.0"`. These MOVE to the
  shared crate; `_native` re-imports them (its public behavior + parity tests
  unchanged).

## File structure

- Create `packages/rust/extensions/score-core/` — lib crate, `crate-type=["lib"]`,
  NO pyo3. `Cargo.toml` (rapidfuzz 0.5.0, own empty `[workspace]` for isolation),
  `src/lib.rs` = the pure scorer fns moved verbatim from `native/src/score.rs`.
- Modify `packages/rust/extensions/native/Cargo.toml` — add
  `goldenmatch-score-core = { path = "../score-core" }`.
- Modify `packages/rust/extensions/native/src/score.rs` — `score_one` becomes
  `use goldenmatch_score_core::score_one;` (pure re-import); the three pyo3
  scorers keep `#[pyfunction]` SHIMS delegating to score-core (so
  `lib.rs:32-34`'s `wrap_pyfunction!` still resolves); the arrow/batch wrappers
  (`score_block_pairs`, `score_block_pairs_arrow`, `score_field_matrix`) stay and
  call the shared `score_one`. `token_sort_string` moves entirely (private in
  score-core); zero hits remain in `native/src/`.
- Modify `packages/rust/extensions/datafusion-udf/Cargo.toml` — add the path dep.
- Modify `packages/rust/extensions/datafusion-udf/src/scalar_udf.rs` — replace
  `AddOneUDF` with the scorer UDFs.
- Modify `packages/python/goldenmatch/tests/test_datafusion_ffi_udf.py` — the
  scorer parity test (FFI UDF == native scorer).

---

### Task B1: Extract `goldenmatch-score-core` (single source of truth)

**Files:**
- Create: `packages/rust/extensions/score-core/Cargo.toml`, `src/lib.rs`
- Modify: `packages/rust/extensions/native/Cargo.toml`, `src/score.rs`

- [ ] **Step 1: Create the lib crate.** `Cargo.toml`: `[package] name=
  "goldenmatch-score-core"`, `edition="2021"`, `[lib]`, `[dependencies] rapidfuzz
  = "0.5.0"` (same pin as `native/Cargo.toml:31` — no skew), empty `[workspace]`.
  **NO `rust-toolchain.toml`** (path deps compile under each PARENT's toolchain;
  score-core must inherit, not pin). `src/lib.rs` MOVES from `native/src/score.rs`:
  - the three scorers `jaro_winkler_similarity`/`levenshtein_similarity`/
    `token_sort_ratio` — **as plain `pub fn`, STRIPPING the `#[pyfunction]`
    attribute + any `use pyo3::...`** (score-core has no pyo3 → `#[pyfunction]`
    won't compile).
  - `score_one` — `pub fn`, **VERBATIM including the id=2 UNSCALED `fuzz::ratio`**
    ([0,1], NOT `*100`). Do NOT reconcile with `token_sort_ratio`'s `*100` —
    `score_field_matrix` depends on the unscaled form (see score.rs:491-493
    comment). This is the silent-drift trap (CLAUDE.md "2 bugs survived review").
  - `token_sort_string` — keep PRIVATE in score-core (both its callers,
    `token_sort_ratio` + `score_one`, move with it). Do NOT export/re-import it.
  - the `use rapidfuzz::...` the moved fns need.

- [ ] **Step 2: Point `_native` at the shared crate.** `native/Cargo.toml`:
  `goldenmatch-score-core = { path = "../score-core" }`. In `native/src/score.rs`:
  - `score_one` → `use goldenmatch_score_core::score_one;` (pure re-import; it's
    called by `score_block_pairs`:188, `score_block_pairs_arrow`:335,
    `score_field_matrix`:494 which STAY in `_native`).
  - the three pyo3 scorers → KEEP a `#[pyfunction]` SHIM in `score.rs` delegating
    to score-core, e.g. `#[pyfunction] pub fn jaro_winkler_similarity(a:&str,
    b:&str)->f64 { goldenmatch_score_core::jaro_winkler_similarity(a,b) }`. A bare
    `use` re-export does NOT satisfy `lib.rs:32-34`'s `wrap_pyfunction!(
    score::jaro_winkler_similarity)` — it needs a `#[pyfunction]` in the `score`
    module. Leave `lib.rs` UNCHANGED.
  - `grep token_sort_string native/src/` MUST return zero hits post-move.

- [ ] **Step 3: Validate.** `cargo check` in `score-core` and in `native` (long
  timeout; if the local rustc ICEs on deps, note it and defer to CI). Confirm no
  other `native/src/*.rs` referenced the moved fns by their old path (grep).

- [ ] **Step 4: Commit.** `refactor(score): extract scorers to goldenmatch-score-core (shared by _native + datafusion-udf)`.
  NOTE the GATE: `_native`'s existing scorer/bucket parity tests in CI MUST stay
  GREEN (they prove the extraction didn't change native behavior — `build_native.py`
  builds `_native` which now pulls score-core).

### Task B2: Scorer FFI UDFs in `datafusion-udf`

**Files:**
- Modify: `packages/rust/extensions/datafusion-udf/Cargo.toml`
- Modify: `packages/rust/extensions/datafusion-udf/src/scalar_udf.rs` (+ `lib.rs`)

- [ ] **Step 1: Add the dep.** `datafusion-udf/Cargo.toml`:
  `goldenmatch-score-core = { path = "../score-core" }`.

- [ ] **Step 2: Implement the scorer UDFs.** Replace `AddOneUDF` with one
  `#[pyclass]` `ScalarUDFImpl` per scorer: `JaroWinklerUDF` (name `"jaro_winkler"`),
  `TokenSortUDF` (`"token_sort"`), `LevenshteinUDF` (`"levenshtein"`). Each:
  `signature() = Signature::exact(vec![DataType::Utf8, DataType::Utf8],
  Volatility::Immutable)`, `return_type() -> Float64`,
  `invoke_with_args(args)`: use `ColumnarValue::values_to_arrays(&args.args)?`
  (v53 helper — collapses Scalar→Array of len `args.number_rows`, removing the
  hand-rolled Array/Scalar broadcast + its null edges), downcast both to
  `StringArray`, build via `Float64Array::from_iter(a.iter().zip(b.iter()).map(
  |(oa,ob)| ...))`. **Null convention (pin it + test in B3): match the in-memory
  path — `None`→`""` then score** (the in-memory `native.py:47` maps `None→""`),
  so FFI ≡ in-process; do NOT null-propagate unless you assert that divergence in
  B3. `goldenmatch_score_core::jaro_winkler_similarity(a,b)` etc.; return
  `ColumnarValue::Array(Arc::new(out))`. Mirror Stage A's
  `__datafusion_scalar_udf__` PyCapsule export verbatim per UDF. Register all
  three in the `#[pymodule]`. Named UDFs (not a generic scorer-name arg) — cleaner
  for the spine's SQL.

- [ ] **Step 3: Validate** `cargo check` (defer to CI if rustc ICEs). Commit.
  `feat(df-udf): jaro_winkler/token_sort/levenshtein scorers as FFI ScalarUDFs`

### Task B3: Parity test (FFI UDF == native scorer) + CI

**Files:**
- Modify: `packages/python/goldenmatch/tests/test_datafusion_ffi_udf.py`

- [ ] **Step 1: Write the parity test.** Add a scorer-parity test: a fixture of
  string pairs (identical, typo'd, reordered tokens, empty, **a null/None**,
  unicode, disjoint). At the TOP assert
  `goldenmatch.core._native_loader.native_available() is True` (NOT importorskip —
  if `_native` isn't built in this job the test must FAIL, not silently fall back).
  Register the scorer UDFs into a `SessionContext`, run `SELECT jaro_winkler(a,b),
  token_sort(a,b), levenshtein(a,b) FROM pairs`, assert each == the per-pair native
  scorer `goldenmatch._native.jaro_winkler_similarity(a,b)` /
  `.levenshtein_similarity(a,b)` / `.token_sort_ratio(a,b)` (these ARE exported,
  per `lib.rs:32-34`, already tested vs rapidfuzz in `test_native_parity.py`).
  **CRITICAL: native `token_sort_ratio` returns 0-100; the FFI `token_sort` UDF
  returns 0-1** → assert `ffi_token_sort == native.token_sort_ratio(a,b)/100.0`
  (a 100× silent failure if missed). jaro_winkler/levenshtein are both 0-1 on both
  sides. Null row: assert FFI matches the `None→""` convention (B2). Use
  `pytest.approx(abs=1e-6)` (f32-origin; NOT 1e-12). Both paths call
  `goldenmatch-score-core` → parity is by construction; this is the drift guard.

- [ ] **Step 2: CI.** The `datafusion-udf` wheel step (Stage A) builds score-core
  transitively. The parity test now ALSO needs `goldenmatch._native` importable in
  the same job — confirm the goldenmatch lane builds `_native` (`build_native.py`)
  before pytest (it does for the in-tree native path); if not, add it. Add
  `packages/rust/extensions/score-core/**` to BOTH paths-filters: the
  `datafusion-udf`/`python_goldenmatch` filter AND whatever filter gates the
  `_native` build + `test_native_parity.py` (else a score-core-only PR skips the
  native regression gate that is the whole point of B1).

- [ ] **Step 3: Validate** `ruff` + `py_compile` the test. Commit.
  `test(df-udf): scorer FFI UDF parity vs native (eps 1e-6, by-construction guard)`

---

## Execution order & gates

1. B1 → B2 → B3 on a branch off `feat/datafusion-ffi-udf`. Push (benzsevern), PR.
2. CI gates (both must be GREEN):
   - `_native`'s scorer/bucket parity tests (B1 didn't change native behavior).
   - the new FFI scorer-parity test (FFI UDF == native, ε 1e-6) — actually RUNS
     (hard import guard from Stage A; datafusion installed).
3. If GREEN: Stage B done → re-plan Stage C (spine orchestration). The shared
   `score-core` + the scorer UDFs are the substrate Stage C's score stage uses.

No default flips (nothing wires into the pipeline until Stage C, behind
`mode="scale"`).

## Final review

After B1-B3: a code-reviewer over the diff (the score.rs extraction correctness —
did any native caller break? — the FFI UDF arrow array handling + null/scalar
cases, the parity fixture coverage), then declare Stage B done.
