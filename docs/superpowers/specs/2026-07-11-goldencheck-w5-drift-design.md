# GoldenCheck W5 — drift detection (reuse wave) — design

Date: 2026-07-11
Status: wave design (Arrow fused-scan program; /goal "all Ws implemented"). Pending spec review.
Program: `...-arrow-fused-scan-engine-program-design.md` + `...-W-path-scoping.md`. **W5 — drift.**
Base: fresh `origin/main` (W0-land + CSV + W1 + W2 + W3 merged; W4 #1685 enqueuing — the reused kernels `chi2_gof`/`pearson_r`/`chi2_contingency_stat` land with W4 before this PRs; rebase onto them).

## Goal

Bring `drift/detector.py`'s statistics onto the Rust-source-of-truth kernels. **W5 is a REUSE wave — it adds NO new kernels.** Recon (source-verified) shows drift's scipy surface is a subset of what W1–W4 already built:
- `_compute_benford_pvalue` (detector.py:346) does `_stats.chisquare(f_obs=observed, f_exp=expected)` — **identical** to baseline `_compute_benford`; reuses W4 **`chi2_gof`**.
- `_compute_correlation` pearson branch (detector.py:699) does `_stats.pearsonr(a_vals, b_vals)` (uses only `corr`) — reuses W4 **`pearson_r`**.
- `_compute_correlation` cramers_v branch (detector.py:706) calls `_cramers_v` from `baseline/correlation.py` — **already shadow-wired in W4** (`chi2_contingency_stat`); nothing to do here.
- Benford leading-digit COUNTS (`_leading_digit_counts`, detector.py:340) already use the native `benford` kernel (W0-land). Nothing to do.

**DECLINED (documented, stays Python — same boundary as W4):** `_check_distribution_drift` (detector.py:109-146) fits candidate distributions via scipy `dist.fit()` (lognorm numerical MLE) + `_stats.kstest` (Kolmogorov p-value). Not byte-reproducible; identical decline to W4's `_fit_distribution`.

So W5 = **shadow-wire `chi2_gof` + `pearson_r` into `drift/detector.py`** (mirroring exactly how W4 wired them into `baseline/`), + a shadow test, + record the distribution/kstest decline. The 13 drift check-TYPES are unchanged; their non-stat parts already route through existing kernels (fd/keys/regex/benford) or are pure arithmetic (entropy/bounds) — out of scope (no scipy, no new Rust).

## Wiring (shadow — mirrors W4)
- `_compute_benford_pvalue`: after `_chi2, pvalue = _stats.chisquare(...)`, when `native_enabled("chi2_gof")`, ALSO compute `native_module().chi2_gof(observed, expected)` in shadow (try/except **BaseException** — pyo3 PanicException is a BaseException, the W4 lesson); discard. Finding unchanged.
- `_compute_correlation` pearson: after `corr, _ = _stats.pearsonr(a_vals, b_vals)`, when `native_enabled("pearson_r")`, ALSO compute `pearson_r(pa.array(a_vals, float64), pa.array(b_vals, float64))` in shadow; discard. (Cast to float64 first — pearson_r panics on int arrays, the W4 lesson.) Value unchanged.
- No wiring for cramers_v (baseline `_cramers_v` already carries the W4 shadow).

## Parity / contract
- No NEW parity contract — `chi2_gof` + `pearson_r` are already registered + parity-locked in the W4 harness. W5 adds only a shadow test asserting the kernels match scipy on drift's inputs (the same epsilon as W4).
- Authoritative drift Findings UNCHANGED (shadow); `import goldencheck` zero polars (drift lazy-imports scipy); existing drift tests UNEDITED.

## Testing
- Python: existing `drift`/`detector` tests UNEDITED green (shadow). A shadow test `tests/engine/test_w5_drift_shadow.py`: for a benford-eligible drift fixture + a correlated-numeric drift fixture, assert `chi2_gof`/`pearson_r` (on the inputs drift feeds scipy) match scipy within epsilon. `skipif` on `native_enabled(...)`.
- `import goldencheck` zero polars; ruff clean. NO Rust changes → no cargo/clippy/wasm needed (but confirm the reused symbols are present).

## Risks
- **Thin wave — the value is correctness-of-reuse, not new kernels.** The risk is mis-wiring: passing the wrong array/list shape to a kernel, or letting a pyo3 panic escape. Mitigated by mirroring W4's exact wiring (float64 cast + `except BaseException`) and the shadow test.
- **drift lazy-imports scipy (`_SCIPY_AVAILABLE`)** — the shadow calls must sit on the path where scipy already succeeded (inside the existing try), so a scipy-absent env is unaffected.
- **rebase onto W4** — the reused kernels only exist once W4 merges; branch/rebase accordingly.

## Non-goals
- No new kernels. No kstest/distribution-fit reproduction (declined, same as W4). No entropy/bounds kernel (pure arithmetic, no scipy, low value). No re-wiring drift's fd/keys/regex checks (already native via their own kernels). No changing user-visible output (shadow). No polars-free wiring (Flip).
