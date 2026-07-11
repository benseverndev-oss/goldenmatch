# GoldenCheck W4 — baseline stats (pearson / chi2 / chi2-GOF) — design

Date: 2026-07-11
Status: wave design (Arrow fused-scan program; /goal "all Ws implemented"). Pending spec review.
Program: `...-arrow-fused-scan-engine-program-design.md` + `...-W-path-scoping.md`. **W4 — the statrs wave** (program: "stats REPRODUCED IN RUST (statrs), not scipy" + "scipy KS/chi2/pearsonr not bit-reproducible → owned epsilon contract").
Base: fresh `origin/main` (W0-land + CSV + W1 + W2 merged; W3 #1683 enqueuing — cite W1 `aggregate.rs`/W2 `stats.rs` as the pattern; W3 `duplicate.rs`/`age.rs` land before PR).

## Goal

Fused Arrow-native Rust kernels that reproduce the **deterministic** scipy statistics the baseline profilers use, + ONE distribution survival function via `statrs`. Rust = source of truth; scipy = the parity oracle. Shadow-wired (authoritative findings stay scipy/Polars until the Flip). The scope is set by what the profilers ACTUALLY consume (verified from source), which is narrower than "reproduce scipy":

- **`correlation.py` uses only the deterministic STATISTICS, no p-values:** `_pearson_entry` does `corr, _ = pearsonr(a, b)` — discards the p-value, uses only `r` (line 78). `_cramers_v` does `chi2, _, _, _ = chi2_contingency(matrix)` — uses only the chi2 statistic (line 127), then a pure-arithmetic Cramér's-V bias correction. **Both are pure arithmetic → fully reproducible, float-epsilon exact. No distribution needed.**
- **`statistical.py` `_compute_benford` uses the chi2 GOF p-VALUE:** `chi2, pvalue = _stats.chisquare(f_obs, f_exp)` then `result["chi2_pvalue"] = round(pvalue, 6)` (lines 326-327). chi2 statistic is deterministic; the p-value = `ChiSquared(df=8).sf(chi2)` — reproducible via `statrs` within epsilon (the ONE owned p-value divergence class).

**DECLINED (infeasible to byte-reproduce — documented, stays Python, like dates):** `statistical.py` `_fit_distribution` — scipy `dist.fit()` is MLE with NUMERICAL optimizers for `lognorm`/`expon` (not closed-form), plus `kstest` + `logpdf`. scipy's optimizer path is not byte-reproducible in Rust; the fitted params drive everything downstream, so the whole fit/KS/AIC selection stays scipy. This is the honest W4 boundary (the program's "KS not bit-reproducible" clause). Kerneling it is a possible far-future wave, NOT W4.

## Kernels (goldencheck-core)

### A. `pearson_r(x: &[f64], y: &[f64]) -> f64`
Pearson correlation coefficient: `cov(x,y) / (std_x * std_y)` — the exact quantity `scipy.stats.pearsonr(x,y)[0]` returns. Match scipy's formulation (mean-centered dot product / sqrt(ss_x * ss_y)); the profiler pre-guards zero-variance + `n>=30` + finite (stays Python). Float-epsilon vs scipy (register the epsilon class for `r`). Reused wherever Pearson is needed (drift W5 also calls pearsonr).

### B. `chi2_contingency_stat(matrix: &[&[f64]]) -> f64` (or a flat `values: &[f64], nrows, ncols`)
The Pearson chi-squared STATISTIC from a contingency table: `expected[i][j] = row_sum[i]*col_sum[j]/total`; `chi2 = Σ (obs-exp)^2 / exp`. This is what `scipy.stats.chi2_contingency(matrix)[0]` returns **with the default `correction=True`** — VERIFY: scipy applies Yates' continuity correction ONLY for 2×2 tables (`correction=True` default); the kernel must replicate that 2×2-only Yates adjustment (`Σ (|obs-exp|-0.5)^2/exp`) to match. Deterministic → float-epsilon exact.

### C. `chi2_gof_pvalue(observed: &[f64], expected: &[f64]) -> (f64, f64)` (statrs)
Chi-squared goodness-of-fit: `chi2 = Σ (obs-exp)^2 / exp` (statistic, deterministic exact); `pvalue = 1 - ChiSquared(df).cdf(chi2)` = `sf` where `df = len(observed) - 1 - ddof` and scipy `chisquare` default `ddof=0` → `df = k-1` (benford: 9 digits → df=8). Uses `statrs::distribution::ChiSquared`. **p-value is the owned epsilon divergence class** (statrs vs scipy's `chdtrc`/`gammaincc` differ in the last digits; register it — after the profiler's `round(pvalue, 6)` most cases match, but a value near a rounding boundary can flip → register the class, and note the profiler rounds to 6dp which SHRINKS the divergence surface).

## Dependency: `statrs`
Add `statrs` to `goldencheck-core/Cargo.toml` (kernel C only). Confirm it is **wasm-compatible** (the core crate compiles to `wasm32-unknown-unknown` for the goldencheck-wasm surface) — statrs is pure Rust (no C deps), should be fine, but VERIFY the wasm build stays green. If statrs bloats wasm unacceptably, feature-gate kernel C behind a non-wasm feature (kernels A/B need no dep).

## Wiring (shadow — mirrors W1/W2/W3)
- `correlation.py` `_pearson_entry`: when `native_enabled("pearson_r")`, ALSO compute `pearson_r(a_vals, b_vals)` in shadow (the arrays are already numpy → pass via pyarrow); discard. `_cramers_v`: when `native_enabled("chi2_contingency")`, ALSO compute `chi2_contingency_stat(matrix)` in shadow; discard. Findings STAY scipy.
- `statistical.py` `_compute_benford`: when `native_enabled("chi2_gof")`, ALSO compute `chi2_gof_pvalue(observed_props, expected_vals)` in shadow; discard. The finding STAYS scipy.
- Shadow test per kernel asserts kernel == scipy within epsilon on a corpus.

## Parity / contract
- `pearson_r`: float-epsilon vs `scipy.stats.pearsonr(x,y)[0]` (register the epsilon class; the profiler rounds to 6dp).
- `chi2_contingency_stat`: float-epsilon vs `scipy.stats.chi2_contingency(m)[0]` incl. the 2×2 Yates correction.
- `chi2_gof`: statistic exact (deterministic); p-value epsilon vs `scipy.stats.chisquare` — register the p-value divergence class (statrs vs scipy survival fn).
- Authoritative findings UNCHANGED (shadow); `import goldencheck` zero polars (baseline is lazy-imported anyway); existing baseline tests UNEDITED.

## Testing
- Rust: `pearson_r` (perfect +1/-1, zero-corr, known fixtures); `chi2_contingency_stat` (2×2 with Yates, 3×3 without, known scipy values); `chi2_gof` (uniform obs=exp → chi2=0 p=1, skewed, benford-shaped).
- Parity harness: each kernel vs scipy on random + adversarial fixtures (register pearson/contingency epsilon; chi2_gof p-value epsilon class).
- Python: existing `correlation`/`statistical` baseline tests UNEDITED green (shadow); shadow test asserts kernel==scipy within epsilon.
- `import goldencheck` zero polars; cargo/clippy `-D warnings` (BOTH crates) / wasm clean; all prior native symbols intact.

## Risks
- **statrs vs scipy survival-fn epsilon** — the chi2 p-value's last digits differ; the profiler's `round(_, 6)` shrinks but doesn't eliminate boundary flips. Register the p-value divergence class explicitly; the STATISTIC stays exact.
- **scipy `chi2_contingency` Yates correction** — 2×2-only continuity correction (`correction=True` default) is easy to miss; replicate it + test a 2×2 fixture against scipy.
- **statrs wasm** — verify the core crate still builds to wasm with statrs; feature-gate kernel C if it breaks wasm.
- **pearsonr formula match** — scipy centers then normalizes; use the same ss-based formula (not a naive cov that accumulates differently) to keep epsilon tight.
- **the DECLINE is load-bearing** — do NOT attempt to kernel `_fit_distribution`/kstest/logpdf; that's the infeasible scipy-MLE surface. W4 is the 3 deterministic-ish kernels only.

## Non-goals
- No distribution fitting / kstest / logpdf / AIC (scipy MLE, declined). No entropy/percentile/bounds kernels (pure arithmetic, no scipy problem, low value). No drift (W5, though pearson_r/chi2 reuse there). No changing user-visible output (shadow). No polars-free wiring (Flip).
