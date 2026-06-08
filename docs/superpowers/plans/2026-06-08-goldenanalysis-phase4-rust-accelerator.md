# GoldenAnalysis Phase 4 — Rust Accelerator Plan

> Use superpowers:executing-plans (inline). Rust side is locally verifiable (`cargo test`/`cargo check`); the wheel build + native parity run in CI.

**Goal:** Ship the optional Rust accelerator scaffold for GoldenAnalysis — `analysis-core` (pyo3-free aggregation kernels) + `analysis-native` (abi3 maturin wheel exposing them via Arrow), with a parity test proving native == pure-Python, the loader `[native]` extra + uv source, a CI native-parity lane, a publish workflow, and a bench harness. **`_GATED_ON` stays empty** — no primitive is turned on until a parity-proven, wall-verified follow-up. Zero behavior/perf change to the pure path.

**Architecture:** Two crates under `packages/rust/extensions/`, modeled exactly on the `goldencheck-core` (pyo3-free) + `goldencheck-native` (abi3 maturin) pair. The kernels mirror `goldenanalysis/core/aggregate.py` value-for-value. The native targets are `histogram` + `quantile` — the two primitives that are **pure-Python loops** today (`null_ratio`/`duplicate_row` are already Polars-vectorized, so per the goldencheck "beat Polars, not 'it's Rust'" lesson they are NOT targets). Data crosses the boundary as a Float64 Arrow array (zero-copy, `PyArrowType<ArrayData>`).

**Why `_GATED_ON` empty:** the design ("start `_GATED_ON` empty; add primitives one parity-proven, wall-verified step at a time") + the #688 / perf-audit / goldencheck-composite-key lessons all forbid gating without a measured wall win on a real shape. The wall can only be measured by building the wheel at scale in CI; that measurement + the dispatch wiring + the `_GATED_ON` flip are a focused follow-up. P4 ships everything needed to make that follow-up one measured step.

**Reference:** `packages/rust/extensions/goldencheck-{core,native}/`, `scripts/build_goldencheck_native.py`, `packages/python/goldencheck/tests/core/test_native_parity.py`, the `goldencheck_native` job in `.github/workflows/ci.yml`, `.github/workflows/publish-goldencheck-native.yml`. Loader stub already shipped: `goldenanalysis/core/_native_loader.py` (`GOLDENANALYSIS_NATIVE`, `_GATED_ON` empty).

---

## Conventions
- Rust bash preamble (every cargo command): `export PATH="/c/Users/bsevern/.cargo/bin:$PATH" && export RUSTUP_HOME="C:/Users/bsevern/.rustup" && export CARGO_HOME="C:/Users/bsevern/.cargo"`.
- Branch `feat/goldenanalysis-native`, off `main`. Commit per task.
- Float parity is the whole game: the kernel must use the SAME IEEE754 op order as `aggregate.py` (`width=(hi-lo)/bins`; `idx=int((v-lo)/width)` right-edge-clamped; `pos=q*(n-1)`; `lo+(hi-lo)*frac`). Edges `lo + i*width`. Inputs assumed finite (cluster sizes / scores); NaN/inf are out of the parity contract (the Python reference is undefined on them too).

## Tasks

### P4.0 — `analysis-core` crate (pyo3-free kernels)
- [ ] Create `packages/rust/extensions/analysis-core/Cargo.toml` (`[package] name=analysis-core`, `[lib] name=analysis_core`, no deps; `[dev-dependencies]` none). Mirror goldencheck-core (no `[workspace]` line — excluded from the parent workspace in P4.6).
- [ ] Create `src/lib.rs`: `histogram(values: &[f64], bins: i64) -> Vec<(f64, i64)>` + `quantile(values: &[f64], q: f64) -> f64`, byte-mirroring `aggregate.py`. `#[cfg(test)] mod tests` covering: empty→[]/0.0; `bins<1`→[]; single value; all-equal collapses to one bin; right-edge inclusive (max in last bin); the cluster-sizes example `[1,1,3,2]` → p50/p95/max + the histogram shape; q=0/0.5/0.95/1.
- [ ] `cargo test` (in the crate dir) green.
- [ ] Commit `feat(analysis-core): pyo3-free histogram + quantile kernels (aggregate.py parity)`

### P4.1 — `analysis-native` crate (abi3 Arrow shims)
- [ ] Create `packages/rust/extensions/analysis-native/{Cargo.toml,pyproject.toml,README.md,rust-toolchain.toml}` + `python/goldenanalysis_native/__init__.py` + `src/lib.rs`. Mirror goldencheck-native exactly: empty `[workspace]`; `[lib] name="_native" crate-type=["cdylib"]`; pyo3 `extension-module`+`abi3-py311`; `arrow=55` (`pyarrow`, default-features off); path-dep `analysis-core`. `[tool.maturin] module-name="goldenanalysis_native._native"`, `python-source="python"`.
- [ ] `src/lib.rs`: `#[pymodule] _native` exposing `histogram(values: PyArrowType<ArrayData>, bins: i64) -> Vec<(f64,i64)>` and `quantile(values: PyArrowType<ArrayData>, q: f64) -> f64`. Each: require Float64 (else `TypeError`), drop null slots (honor the mask, like benford), delegate to `analysis_core`. Add `__version__`.
- [ ] `cargo check` (in the crate dir) clean.
- [ ] Commit `feat(analysis-native): abi3 _native ext — histogram/quantile Arrow shims`

### P4.2 — in-tree build script
- [ ] Create `scripts/build_analysis_native.py` mirroring `build_goldencheck_native.py` (CRATE=`analysis-native`, DEST=`packages/python/goldenanalysis/goldenanalysis/_native.abi3.so`).
- [ ] Add `goldenanalysis/_native.abi3.so` to the goldenanalysis `.gitignore` (or the repo root one) — the in-tree artifact must not be committed.
- [ ] Commit `feat(goldenanalysis): in-tree build script for the native ext`

### P4.3 — Python parity + gate tests
- [ ] Create `packages/python/goldenanalysis/tests/core/test_native_parity.py`: `native_only = skipif(not native_available())`. Parity (vs `goldenanalysis.core.aggregate`): random + adversarial + null-bearing + empty + all-equal Float64 arrays for `histogram(arr, bins)` and `quantile(arr, q)` — assert EXACT equality to the pure reference. Plus ALWAYS-run gate tests (no wheel needed): `GOLDENANALYSIS_NATIVE=0` → `native_enabled("histogram") is False`; `auto` + empty `_GATED_ON` → False even if a wheel were present; `=1` with no wheel raises `RuntimeError`.
- [ ] Verify locally (targeted, `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8`): the gate tests pass and the native parity tests SKIP cleanly (no wheel) — or defer to CI if the venv isn't readily usable. ruff + `py_compile` clean.
- [ ] Commit `test(goldenanalysis): native parity + loader-gate tests`

### P4.4 — wire the `[native]` extra + uv source
- [ ] `packages/python/goldenanalysis/pyproject.toml`: replace the commented stub with `native = ["goldenanalysis-native>=0.1.0"]`.
- [ ] root `pyproject.toml` `[tool.uv.sources]`: add `goldenanalysis-native = { path = "packages/rust/extensions/analysis-native" }` (workspace-local path so `uv sync` resolves it without PyPI — the goldenmatch[native] uv-sync bite; the extra + the path source land in LOCKSTEP).
- [ ] Commit `feat(goldenanalysis): wire the [native] extra + uv path source`

### P4.5 — CI native-parity lane + path filter
- [ ] `.github/workflows/ci.yml`: add the `analysis_native` filter (`packages/rust/extensions/analysis-{core,native}/**`, `packages/python/goldenanalysis/tests/core/test_native_parity.py`, `scripts/build_analysis_native.py`) + emit it; add a `goldenanalysis_native` job mirroring `goldencheck_native` (rust-cache on the ext crate, clippy, `uv run python scripts/build_analysis_native.py`, run the parity test, then a `GOLDENANALYSIS_NATIVE=1` required-mode step). Gate on `needs.changes.outputs.analysis_native || ci_workflow`.
- [ ] Commit `ci(goldenanalysis): native parity lane + analysis_native path filter`

### P4.6 — exclude the crates from the bridge workspace + publish workflow
- [ ] `packages/rust/extensions/Cargo.toml`: add `"analysis-core"`, `"analysis-native"` to `exclude` (standalone, like goldencheck-*).
- [ ] Create `.github/workflows/publish-goldenanalysis-native.yml` mirroring `publish-goldencheck-native.yml` (maturin wheels on `goldenanalysis-native-v*`; both macOS arches on `macos-14`; `workflow_dispatch` publish toggle).
- [ ] Commit `ci(goldenanalysis): exclude native crates from bridge workspace + publish-goldenanalysis-native`

### P4.7 — bench harness (the gate-flip tool)
- [ ] Create `packages/python/goldenanalysis/benchmarks/aggregate_benchmark.py`: 5-run median wall of `histogram`/`quantile`, pure vs `GOLDENANALYSIS_NATIVE` forced, over large Float64 arrays (e.g. 1M / 10M). Prints the pure-vs-native ratio per primitive — the measurement that decides the follow-up gate-flip (don't gate on "it's Rust"; gate on the measured wall vs the pure-Python loop). Document that the WALL must move on a real shape before any primitive joins `_GATED_ON`.
- [ ] Commit `feat(goldenanalysis): aggregate native A/B bench harness`

### P4.8 — README + verify + push
- [ ] `packages/python/goldenanalysis/README.md` (+ a short `packages/rust/extensions/analysis-native/README.md`): a "native accelerator" note — `pip install goldenanalysis[native]`, the loader gate, `_GATED_ON` empty until a wall-verified primitive lands, the build script. CHANGELOG note if present.
- [ ] Verify: `cargo test` (analysis-core) + `cargo check` (analysis-native) green; ruff clean; the existing goldenanalysis Python tests untouched (aggregate.py unchanged). Push (auth dance); PR vs main; babysit (ci-required + CodeQL + the new native lane); merge.

## Acceptance
- [ ] `analysis-core` `cargo test` green; `analysis-native` `cargo check` clean; both excluded from the bridge workspace.
- [ ] Native parity test proves `histogram`/`quantile` byte-identical to `aggregate.py` (CI native lane, `GOLDENANALYSIS_NATIVE=1`); skips cleanly with no wheel.
- [ ] `_GATED_ON` EMPTY → `auto` still uses pure Python (zero behavior/perf change); `aggregate.py` untouched. `[native]` extra + uv path source don't break `uv sync`.
- [ ] CI native lane + path filter + publish workflow wired; bench harness present.

### Deferred (the measured follow-up)
Measure the wall on a real shape (CI bench) → if `histogram`/`quantile` actually beat the pure loop, wire the dispatch in `aggregate.py` (guarded by `native_enabled(...)`) and add the primitive to `_GATED_ON`. P5: GoldenPipe stage + MCP + publish.
