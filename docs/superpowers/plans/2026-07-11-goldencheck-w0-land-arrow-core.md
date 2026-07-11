# W0-land: Arrow-in-core kernel foundation — Implementation Plan

> Use superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Land the Arrow-in-core kernel foundation (from the stale `feat/goldencheck-arrow-native-core` branch) onto main-with-2.0.0: convert the 5 existing `goldencheck-core` kernels (benford, keys/composite/FD/approx-FD, fuzzy) from slice-in to **Arrow-in** (`&dyn Array`), thin the `-native` shims, and land the reusable **parity-oracle harness**. Reconcile to `arrow = 59` (main's native version) and integrate alongside the regex/date/dc kernels 2.0.0 added.

**Architecture:** The proven code exists on `feat/goldencheck-arrow-native-core` (built against arrow 55). This wave PORTS that code onto fresh main, fixing (a) arrow 55->59 API deltas, (b) coexistence with the regex/date/dc kernels on main. Rust kernel = source of truth; parity harness asserts native == Python fallback with an empty accepted-divergence registry.

**Program:** `docs/superpowers/specs/2026-07-11-goldencheck-arrow-fused-scan-engine-program-design.md` (this is W0-land, the foundation wave).

---

## Audit result (already done — the strategy)

- **NOT a rebase** — the branch's `_native_loader` reference-mode flip is ALREADY on main (2.0.0), and its pyproject/ci/version changes conflict. Only the KERNEL work + parity harness are new value.
- **Port these from the branch** (reference: `git show feat/goldencheck-arrow-native-core:<path>`): `goldencheck-core/src/{arrow_support.rs, benford.rs, keys.rs, fuzzy.rs, lib.rs}`, `goldencheck-core/Cargo.toml` (arrow dep), `goldencheck-native/src/{keys.rs, fuzzy.rs, profile.rs}` (thinned shims), `tests/core/{parity_harness.py, test_parity_harness.py, test_loader_reference_mode.py}`, and call-site adaptations in `goldencheck/profilers/fuzzy_values.py` + `goldencheck/cell_quality.py`.
- **Reconcile:** arrow **59** not 55 (match `goldencheck-native`); leave main's `regex.rs`/`date.rs`/`dc.rs` UNTOUCHED (they stay list/string-based this wave); do NOT touch `_native_loader.py` (already reference-mode), pyproject version (stays 2.0.0), ci.yml.

## Conventions (worktree `gc-w0`, branch `feat/goldencheck-w0-land-arrow-core`, off fresh main)

Rust toolchain: `export PATH="/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin:$PATH" CARGO_HOME=/d/.cargo RUSTUP_HOME=/d/.rustup`.
Python: `export PYTHONPATH="D:/show_case/gc-w0/packages/python/goldencheck" POLARS_SKIP_CPU_CHECK=1 GOLDENCHECK_NATIVE=auto; PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe`.
Native build: `$PY scripts/build_goldencheck_native.py` then Windows `.dll`->`.pyd` copy (see prior waves). Ruff 100-char.

**INVARIANTS:** native output byte/set-identical to before (the kernels were already exact — any diff is a mechanism bug); the S2.2/S2.3 regex/date native paths still work (regex+str_to_date symbols present + enabled); `import goldencheck` loads zero polars; existing tests + `tests/core/test_native_parity.py` pass. Commit per task; do NOT push (PR at end).

---

## Task 1: Arrow dep + `arrow_support.rs` + Benford Arrow-in-core (prove the pattern)

**Files:** `goldencheck-core/Cargo.toml`, `goldencheck-core/src/{arrow_support.rs (new), benford.rs, lib.rs}`, `goldencheck-native/src/profile.rs` (benford shim).

- [ ] **Step 1:** Add to `goldencheck-core/Cargo.toml` `[dependencies]`: `arrow = { version = "59", default-features = false }` (match native's version; native adds `features=["pyarrow"]` — core does NOT need pyarrow, just the array types). READ the branch's Cargo.toml (`git show feat/goldencheck-arrow-native-core:packages/rust/extensions/goldencheck-core/Cargo.toml`) for the feature set it used (it used arrow 55) and adapt to 59.
- [ ] **Step 2:** Create `goldencheck-core/src/arrow_support.rs` — port from the branch (`git show feat/goldencheck-arrow-native-core:packages/rust/extensions/goldencheck-core/src/arrow_support.rs`). Fix arrow 55->59 API (array downcast helpers, null-bitmap iteration). This is the shared Arrow-decode helper module.
- [ ] **Step 3:** Convert `benford.rs`: signature `benford_leading_digits(array: &dyn Array) -> Result<[u64;9], ArrowError>` (port from branch), downcast Float64/Int64/Decimal + honor null bitmap. Add `mod arrow_support;` + re-export in `lib.rs`.
- [ ] **Step 4:** Thin `goldencheck-native/src/profile.rs` (the benford shim): decode `PyArrowType<ArrayData>` -> `ArrayRef`, hand `array.as_ref()` to core (port from branch's version).
- [ ] **Step 5:** Build + test:
```bash
cd packages/rust/extensions/goldencheck-core && cargo test --release 2>&1 | grep -E "^error|test result:"
cargo build -p goldencheck-native --release 2>&1 | grep -E "^error" || echo "native builds"
```
Fix arrow-59 API errors until clean. Then `rustfmt` the touched files.
- [ ] **Step 6:** Build the ext + verify benford + the S2.2/S2.3 regex/date symbols still present:
```bash
cd /d/show_case/gc-w0 && $PY scripts/build_goldencheck_native.py   # + .dll->.pyd
$PY -c "import goldencheck._native as n; print('benford', hasattr(n,'benford_leading_digits'), 'regex', hasattr(n,'str_contains_count'), 'date', hasattr(n,'str_to_date'))"
$PY -m pytest packages/python/goldencheck/tests/core/test_native_parity.py -k benford -v
```
- [ ] **Step 7:** Commit.
```bash
git add packages/rust/extensions/goldencheck-core packages/rust/extensions/goldencheck-native/src/profile.rs
git commit -m "feat(goldencheck-core): W0-land Benford Arrow-in-core (arrow 59) + arrow_support module"
```

## Task 2: keys + fuzzy Arrow-in-core + thin their shims

**Files:** `goldencheck-core/src/{keys.rs, fuzzy.rs, lib.rs}`, `goldencheck-native/src/{keys.rs, fuzzy.rs}`.

- [ ] Port keys.rs (composite_key/FD/approx-FD kernels) + fuzzy.rs to Arrow-in from the branch (`git show feat/goldencheck-arrow-native-core:...`), reconciled to arrow 59. Thin the native keys.rs/fuzzy.rs shims (the branch removed the native `intern_column`; that logic moved to core). Build both crates clean (grep `^error`), rustfmt.
- [ ] Verify all 5 component symbols present + the parity for keys/fuzzy:
```bash
$PY scripts/build_goldencheck_native.py   # + .pyd
$PY -m pytest packages/python/goldencheck/tests/core/test_native_parity.py -v
```
- [ ] Commit: `feat(goldencheck-core): W0-land key/FD + fuzzy kernels Arrow-in-core (arrow 59)`.

## Task 3: parity-oracle harness + call-site adaptations

**Files:** `tests/core/{parity_harness.py (new), test_parity_harness.py (new), test_loader_reference_mode.py (new)}`, `goldencheck/profilers/fuzzy_values.py`, `goldencheck/cell_quality.py`.

- [ ] Port the parity-oracle harness + its tests from the branch (empty accepted-divergence registry). It runs each component twice (native + fallback) on random+adversarial fixtures, compares, looks up the registry. Adapt any import paths to current main.
- [ ] Port the call-site adaptations in `fuzzy_values.py` + `cell_quality.py` (they now pass Arrow to the kernel via `.to_arrow()` — reconcile with how main's versions call the kernel). Confirm the two GoldenMatch bridges (`cell_quality`, `functional_dependencies`) keep their pyarrow-facing signatures.
- [ ] Run the harness both lanes + the bridges:
```bash
$PY -m pytest packages/python/goldencheck/tests/core/ -v            # harness + parity, native present
GOLDENCHECK_NATIVE=0 $PY -m pytest packages/python/goldencheck/tests/core/ -v   # fallback lane
$PY -m pytest packages/python/goldencheck/tests -k "cell_quality or functional_dependencies or fuzzy" -v
```
Expected: harness green with EMPTY registry (kernels are exact); both lanes green.
- [ ] Ruff clean; commit: `test(goldencheck): W0-land parity-oracle harness + Arrow call-sites`.

## Task 4: final verification + PR

- [ ] Full targeted verification:
```bash
$PY -m pytest packages/python/goldencheck/tests/core packages/python/goldencheck/tests/test_import_no_polars.py -v
$PY -m pytest packages/python/goldencheck/tests -k "native_parity or relations or profilers" -q
cd packages/rust/extensions/goldencheck-core && cargo test --release 2>&1 | grep -E "^error|test result:"
$PY -m ruff check packages/python/goldencheck/goldencheck packages/python/goldencheck/tests
```
Confirm: 5 kernels Arrow-in-core; regex/date/dc untouched + still working; parity harness green empty-registry; both native + fallback lanes green; `import goldencheck` zero polars; version stays 2.0.0 (internal refactor, minor — actually no version change this wave).
- [ ] PR to main (additive/internal — the Arrow-in-core refactor changes no Python output; native stays byte/set-exact). Arm auto-merge.

## Done criteria
- 5 `goldencheck-core` kernels take `&dyn Array` (Arrow-in-core), arrow=59 lockstep with native; `-native` shims are thin pyarrow marshalling.
- Parity-oracle harness reusable, green, empty accepted-divergence registry.
- regex/date/dc kernels untouched; all native symbols present + enabled; zero polars on import; existing suite green.
- Foundation ready for W1 (fused aggregate checks) to build on.
