# GoldenCheck W4 — baseline stats — Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Steps use `- [ ]`.

**Goal:** Build three fused Arrow-native Rust kernels — `pearson_r`, `chi2_contingency_stat`, `chi2_gof` (statrs) — in `goldencheck-core`, reproducing the DETERMINISTIC scipy statistics the baseline profilers consume + the one chi2 GOF p-value. Shadow-wire `correlation.py` + `statistical.py`. No user-visible change. The scipy `.fit()`/kstest surface is DECLINED (stays Python).

**Spec:** `docs/superpowers/specs/2026-07-11-goldencheck-w4-baseline-stats-design.md` (READ IT + the "Review corrections" — the Yates BLOCKER, `gamma_ur` p-value, getrandom/wasm, and the harness-canonicalize-floats mechanism are binding).
**Base:** fresh `origin/main` (W2 merged; W3 enqueuing). Worktree `gc-w4`, branch `feat/goldencheck-w4-baseline-stats`.

## Conventions
Rust: `export PATH="/d/.rustup/toolchains/1.94.0-x86_64-pc-windows-msvc/bin:$PATH" CARGO_HOME=/d/.cargo RUSTUP_HOME=/d/.rustup`. **goldencheck-core is STANDALONE** — cd INTO `packages/rust/extensions/goldencheck-core` for cargo (NOT `-p`). Native ext: `packages/rust/extensions/goldencheck-native` with `PYO3_PYTHON=/d/show_case/goldenmatch/.venv/Scripts/python.exe`. **CLIPPY `-D warnings` BOTH crates** (CI does). Wide tuple returns → `type` alias + PLAIN `//` comment. Build `.pyd`: `python scripts/build_goldencheck_native.py` builds it (its `.so` copy step errors on Windows — ignore; then `cp packages/rust/extensions/goldencheck-native/target/release/_native.dll packages/python/goldencheck/goldencheck/_native.pyd`). Python: `export PYTHONPATH="D:/show_case/gc-w4/packages/python/goldencheck" POLARS_SKIP_CPU_CHECK=1 GOLDENCHECK_NATIVE=auto; PY=/d/show_case/goldenmatch/.venv/Scripts/python.exe`. Ruff 100-char. `.pyd` untracked — don't stage.

**Pattern to MIRROR:** W2 `goldencheck-core/src/stats.rs` + `goldencheck-native/src/stats.rs` + `tests/core/test_numeric_stats_parity.py` (float-epsilon Component via `_canon_float`/`_STATS_SIG` — REUSE that mechanism). Arrow-in decode: `arrow_support.rs`.

**INVARIANTS:** kernels pyo3-free in goldencheck-core; arrow=59; each kernel's Component `run_fallback` calls SCIPY directly (no pure-Python fallback) + canonicalizes floats to ~9 sig-figs in BOTH run_native/run_fallback (ACCEPTED_DIVERGENCES stays EMPTY); full-scan `Finding`s UNCHANGED (shadow); existing baseline tests UNEDITED; `import goldencheck` zero polars; clippy `-D warnings` clean BOTH crates; wasm build green; all prior symbols intact. Commit per task; don't push.

---

## Task 1: `pearson_r` + `chi2_contingency_stat` kernels (correlation.py — no new dep)

**Files:** `goldencheck-core/src/correlation.rs` (new) + `lib.rs`; `goldencheck-native/src/correlation.rs` (new) + `lib.rs`; `_native_loader.py`; Test `tests/core/test_correlation_stats_parity.py` (new).

- [ ] **Step 1:** `goldencheck-core/src/correlation.rs`: `pub fn pearson_r(x: &dyn Array, y: &dyn Array) -> f64` — downcast to Float64Array (already-numeric, Python casts), compute `r = Σ(xi-x̄)(yi-ȳ) / sqrt(Σ(xi-x̄)² · Σ(yi-ȳ)²)`, then **clamp `r = r.max(-1.0).min(1.0)`** (scipy clamps). `pub fn chi2_contingency_stat(values: &[f64], nrows: usize, ncols: usize) -> f64` — row/col sums, `exp[i][j]=row_i*col_j/total`; if `nrows==2 && ncols==2` apply Yates: `chi2 = Σ (max(0.0, (obs-exp).abs() - 0.5)).powi(2) / exp` (per the BLOCKER — clip at 0, NOT square-a-negative); else `chi2 = Σ (obs-exp)²/exp`. `#[cfg(test)]`: perfect +1/-1 corr (assert exactly 1.0/-1.0 via the clamp), zero corr, known 3×3 chi2, 2×2 with Yates incl. a cell with |obs-exp|<0.5 (contribution 0). `mod`+`pub use`.
- [ ] **Step 2:** `goldencheck-native/src/correlation.rs`: `#[pyfunction] pearson_r(x: PyArrowType<ArrayData>, y: PyArrowType<ArrayData>) -> f64` + `#[pyfunction] chi2_contingency_stat(values: Vec<f64>, nrows: usize, ncols: usize) -> f64`. Register both. Add `"pearson_r": ("pearson_r",)` + `"chi2_contingency": ("chi2_contingency_stat",)` to `_COMPONENT_SYMBOLS`.
- [ ] **Step 3:** Build core (`cargo test`, grep `^error`) + clippy `-D warnings`. Build native ext + clippy `--release -D warnings`. **wasm: `cargo check -p goldencheck-wasm --target wasm32-unknown-unknown` (or however it's built) — must stay GREEN** (no new dep yet, sanity check). Copy dll → `_native.pyd`. Symbols present.
- [ ] **Step 4:** Parity `tests/core/test_correlation_stats_parity.py`: random+adversarial numeric pairs → `pearson_r(a.to_arrow(), b.to_arrow())` vs `scipy.stats.pearsonr(a,b)[0]` (epsilon via canon-float); contingency matrices (2×2 + larger) → `chi2_contingency_stat(flat, r, c)` vs `scipy.stats.chi2_contingency(matrix)[0]`. Register `pearson_r` + `chi2_contingency` Components in the harness with `run_fallback`=scipy + float canonicalization (~9 sig-figs), ACCEPTED_DIVERGENCES EMPTY. Both lanes.
- [ ] **Step 5:** Commit: `feat(goldencheck-core): W4 pearson_r + chi2_contingency_stat kernels (parity w/ scipy, Yates 2x2)`.

## Task 2: `chi2_gof` kernel (statistical.py benford — adds statrs)

**Files:** `goldencheck-core/Cargo.toml` (add statrs); `goldencheck-core/src/gof.rs` (new) + `lib.rs`; `goldencheck-native/src/gof.rs` (new) + `lib.rs`; `_native_loader.py`; Test `tests/core/test_chi2_gof_parity.py` (new).

- [ ] **Step 1:** Add `statrs` to `goldencheck-core/Cargo.toml`. **Try `statrs = { version = "0.17", default-features = false }`** first (to drop the `rand`→`getrandom` transitive dep that breaks wasm). `goldencheck-core/src/gof.rs`: `pub fn chi2_gof(observed: &[f64], expected: &[f64]) -> (f64, f64)` — `chi2 = Σ (obs-exp)²/exp` (do NOT renormalize expected to the observed sum — scipy doesn't); `df = observed.len() - 1` (scipy `chisquare` default ddof=0); `pvalue` = the UPPER tail = `statrs::function::gamma::gamma_ur(df/2.0, chi2/2.0)` (matches scipy `chdtrc`; do NOT use `1.0 - cdf` — cancellation). Handle `chi2==0.0 → p=1.0`, `df` as f64. `#[cfg(test)]`: obs==exp → chi2=0/p=1; a benford-shaped fixture matching a known scipy chisquare p; skewed. `mod`+`pub use`.
- [ ] **Step 2:** `goldencheck-native/src/gof.rs`: `#[pyfunction] chi2_gof(observed: Vec<f64>, expected: Vec<f64>) -> (f64, f64)`. Register. Add `"chi2_gof": ("chi2_gof",)` to `_COMPONENT_SYMBOLS`.
- [ ] **Step 3:** Build core + clippy `-D warnings`. **CRITICAL: `cargo check` the wasm target for goldencheck-wasm (or goldencheck-core's wasm build) — statrs MUST NOT break it.** If it breaks on `getrandom`, either keep `default-features=false` (preferred) OR feature-gate `gof`/statrs behind a `stats` feature that's off for wasm (document in the crate). Build native ext + clippy `--release -D warnings`. Copy dll → `_native.pyd`. Symbols present.
- [ ] **Step 4:** Parity `tests/core/test_chi2_gof_parity.py`: for benford-shaped + random (obs, exp) pairs, `chi2_gof(obs, exp)` vs `scipy.stats.chisquare(obs, exp)` (both stat + pvalue). The STATISTIC exact (canon); the P-VALUE epsilon (canon-float ~9 sig-figs; after the profiler's `round(_,6)` most match — assert within canon). Register `chi2_gof` Component (run_fallback=scipy.chisquare, canon-float, ACCEPTED_DIVERGENCES EMPTY). Both lanes.
- [ ] **Step 5:** Commit: `feat(goldencheck-core): W4 chi2_gof kernel via statrs gamma_ur (parity w/ scipy chisquare)`.

## Task 3: shadow-wire correlation.py + statistical.py + shadow tests

**Files:** `baseline/correlation.py`, `baseline/statistical.py`; Test `tests/engine/test_w4_shadow.py` (new).

- [ ] **Step 1 (pearson):** in `correlation.py` `_pearson_entry`, after `corr, _ = pearsonr(...)`, when `native_enabled("pearson_r")`, ALSO compute `pearson_r(pa.array(a_vals), pa.array(b_vals))` in shadow (try/except-swallowed); discard.
- [ ] **Step 2 (cramers):** in `_cramers_v`, after `chi2, _, _, _ = chi2_contingency(matrix)`, when `native_enabled("chi2_contingency")`, ALSO compute `chi2_contingency_stat(matrix.flatten().tolist(), r, k)` in shadow; discard. Skip if the scipy path raised/returned None.
- [ ] **Step 3 (benford):** in `statistical.py` `_compute_benford`, after `chi2, pvalue = _stats.chisquare(...)`, when `native_enabled("chi2_gof")`, ALSO compute `chi2_gof(observed_props, expected_vals)` in shadow; discard.
- [ ] **Step 4:** Shadow test `tests/engine/test_w4_shadow.py`: for fixtures (a numeric-pair df, a categorical-pair df, a benford-eligible amount column), assert each kernel (on the same inputs the profiler feeds scipy) MATCHES scipy within epsilon. `skipif` on the relevant `native_enabled(...)`.
- [ ] **Step 5:** Run existing baseline tests UNEDITED green + shadow:
```bash
$PY -m pytest packages/python/goldencheck/tests -k "correlation or statistical or baseline or w4_shadow" -q
```
Ruff clean. Commit: `feat(goldencheck): W4 shadow-compute the 3 baseline-stat kernels (authoritative findings unchanged)`.

## Task 4: final verification + PR

- [ ] Rebase onto fresh `origin/main` (W3 will have merged — resolve any additive lib.rs/loader/harness conflicts by unioning; verify diff is goldencheck-only). Full verification: 3 parity tests (both lanes); baseline profilers' findings UNCHANGED (existing tests unedited); shadow test green; `cargo test` (grep `^error`); clippy `-D warnings` BOTH crates; **wasm build GREEN (statrs didn't break it)**; ruff; `import goldencheck` zero polars; ALL native symbols intact (prior + pearson_r + chi2_contingency_stat + chi2_gof). Confirm NO user-visible change (shadow). Push as `benzsevern`, PR to main (additive, no version bump), report PR number.

## Done criteria
- Three kernels (pearson_r, chi2_contingency_stat, chi2_gof) — Rust source of truth, parity-green vs scipy (deterministic stats float-epsilon exact; chi2 p-value epsilon via statrs gamma_ur; ACCEPTED_DIVERGENCES empty via canon-float).
- statrs added without breaking wasm; all three registered + shadow-wired with authoritative findings UNCHANGED; shadow test proves the match.
- Existing suite green; zero polars; no version bump; clippy `-D warnings` clean; wasm green; all prior symbols intact. scipy `.fit()`/kstest DECLINED (documented). W4 done.
