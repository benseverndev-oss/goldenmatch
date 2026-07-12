# GoldenCheck Flip (3.0.0) — owned-contract cutover — design

Date: 2026-07-11
Status: wave design — the Flip wave of the Arrow-native fused-scan program. HUMAN-GATED (§8b sign-off).
Program: `2026-07-11-goldencheck-arrow-fused-scan-engine-program-design.md` (§8b Flip gate) + `...-W-path-scoping.md`.
Base: fresh `origin/main` (W0-land…W6 all merged; 17 kernels + parity harness live). Worktree `D:\show_case\gc-flip`, branch `feat/goldencheck-flip-3.0.0`.

## What the Flip actually is (recon-corrected)

The Flip is the irreversible 3.0.0 cutover to the **owned contract**: the Rust/Arrow fused kernels become authoritative, the owned deterministic sample + owned dtype vocabulary become the emitted values, and Polars stops being required. Per §3 the finding set is NOT unconditionally bit-identical to the 2.x Polars path, so the cutover is gated on a **measured** differential (§8b), not an assertion of zero delta.

**The lever is the Frame seam, not a 30-module rewrite.** The column profilers already route through `core/frame.py` (`to_frame(frame)` → `col.mean()/std()/min()/max()/filter_outside()/count_gt()/diff()/is_sorted()`). Those methods exist on `PolarsColumn` (Polars-backed) but NOT on `PyColumn` (polars-free: only drop_nulls/unique/cast). That gap is exactly why W2–W5 kernels run as *shadow* — the authoritative value still comes from `PolarsColumn`. Give the seam an **Arrow-backed column** whose distributional/relational methods resolve to the W1–W5 kernels, and every profiler runs unchanged, polars-free, kernel-authoritative. The shadow blocks then become dead code.

### Two kernel populations (recon §1 — the critical distinction)
- **Population A / C — already native-authoritative-when-present** (W0-land relational kernels: composite_keys, functional_dependencies, approximate_fd, fuzzy_values, benford counts, csv_infer; and core/frame regex/str_to_date which already `raise NativeRequiredError`). For these the Flip is "remove the Polars fallback branch," NOT "flip authority." They must be held **constant** in the differential — any delta there is a kernel bug.
- **Population B — genuinely shadow** (W2–W5, 13 sites: column_aggregate, numeric_stats/count_outside, date_freshness, sequence_analysis, chi2_gof, pearson_r, chi2_contingency, duplicate_signatures, age_mismatch, regex-format-count). These are the true "flip authority" sites and the ONLY expected source of a contract delta (beyond owned sample + owned dtype).

## Staged execution (measurement FIRST, cutover gated on it)

### Stage 0 — Arrow-backed Column via kernels (the lever; also enables the measurement)
Implement on the polars-free Arrow column (extend `PyColumn`, or a new `ArrowColumn` backed by a `pyarrow.ChunkedArray`/`Array`) every method the distributional/sequence/freshness profilers call, delegating to the native kernels:
- `mean/std/min/max/sum/count` → `column_numeric_stats`
- `filter_outside(lo,hi)` / outlier sample → `count_outside`
- `diff/is_sorted/gap analysis` → `sequence_analysis`
- `count_gt(now)/max` for dates → `date_freshness`
- `dtype` → neutral vocab (already present)
Kernel results are the SAME ones validated byte/epsilon-exact in the W2–W5 parity harness, so Stage 0 is wiring, not new numerics. Gate on `native_enabled(...)`; if a kernel symbol is absent the Arrow column raises `NativeRequiredError` (matches the existing regex/date policy — the native wheel is mandatory in the polars-free world).

### Stage 1 — the §8b differential measurement harness (THE GATE)
New harness `tests/flip/differential.py` (+ a runnable `scripts/flip_differential.py`):
- **Corpus**: generated synthetic datasets (a `scripts/flip_corpus.py` generator) exercising every check family — numeric distributions (outliers), sequences/gaps, dates/freshness, duplicates, age-vs-dob, correlations/contingency, Benford, plus mixed dtypes and at least one dataset > `sample_size` (100k) so the sample path fires. Plus the 2 existing CSV fixtures.
- **Two runs per dataset**: (P) authoritative 2.x path = `scan_file`/`scan_dataframe` on a `pl.DataFrame` (PolarsColumn); (F) fused path = the same scan on an Arrow-backed Frame (Stage-0 column) with owned sample + owned dtype. Both return `list[Finding]`.
- **Finding key**: `(check, column, severity)` PLUS `affected_rows` and a normalized `message`/`sample_values` — because Population B divergences surface in counts/samples, a bare `(check,column,severity)` key would false-pass (recon §8).
- **Metrics (§8b)**: (1) finding-set Jaccard + count delta per (check, severity); (2) **stat-threshold-flip** bucket (finding appears/disappears because statrs p-value crossed a cutoff scipy didn't); (3) **owned-sample-flip** bucket (finding differs only because the sampled rows differ); (4) `inferred_type` string diffs; (5) max stat-float delta per stat family; (6) `DatasetProfile.health_score` grade delta (secondary end-to-end invariant).
- **Acceptance (§8b)**: non-stat, non-sample findings MUST be identical (**Jaccard 1.0**) — any delta there is a kernel bug that BLOCKS the flip. Stat-threshold + owned-sample buckets are QUANTIFIED and must sit within an expected band (e.g. stat flips only in p∈[0.04,0.06]); dtype-string diffs are expected (owned vocabulary). Emit a report artifact (`docs/superpowers/specs/flip-differential-report.md`).

**Stage 1 output is presented for human sign-off before any Stage ≥2 work.** If acceptance fails, stop and fix the kernel bug (do not flip).

### Stage 2 — owned deterministic sample
Replace `engine/sampler.py` `df.sample(n, seed=42)` (Polars PRNG) with an owned deterministic reservoir/stride sample over the Arrow table (seeded, stable across runs + `--workers`). Register as an accepted divergence. This is the only sampling seam on the scan path (recon §2).

### Stage 3 — flip authority + delete shadows + owned dtype
- Route `_scan_dataframe_impl`'s column loop through the Frame seam (it currently calls `df[col].n_unique()`/`col.to_arrow()` directly) so the Arrow column drives it.
- `scanner.py:406` `inferred_type=str(col.dtype)` → `dtype_category(col.dtype)` (neutral vocab; the seam already returns it).
- Remove the now-dead Population B shadow blocks (13 sites) — the kernel is now the authoritative `col.method()`.
- Migrate the scipy-backed baseline/drift/correlation paths to the kernels they already shadow (chi2_gof/pearson_r/chi2_contingency) as the authoritative call; keep scipy only where declined (dist.fit/kstest — those stay, gated on the `[baseline]` extra, NOT `[polars]`).

### Stage 4 — Arrow-native scan_file + remove `[polars]`
- `scan_file` reads into an Arrow table (owned csv_infer / pyarrow parquet / openpyxl excel — all already polars-free) and wraps in the Arrow Frame. No `pl.read_csv`.
- `scan_dataframe`: accept a `pyarrow.Table` natively; keep a `pl.DataFrame` convenience overload (converts via `.to_arrow()`) ONLY when polars is importable — so polars becomes a pure convenience, not a requirement.
- Remove the `polars = ["polars>=1.0"]` extra from pyproject. Audit the ~30 `from goldencheck._polars_lazy import pl` modules: on the scan_file finding path they must not need polars; off-path modules (llm/agent/cli/reporters/tui/differ/db_scanner) may keep the lazy shim but must degrade cleanly.

### Stage 5 — version, tests, docs
- Bump 3.0.0 in lockstep: pyproject.toml, `__init__.py __version__`, both `server.json` version fields (the `version_consistency` required gate enforces this).
- Rewrite `tests/nopolars/test_polars_absent.py`: the assertions that polars-absent *declines* (esp. csv full-scan RAISES, line 149) invert — the full scan must now SUCCEED via the owned/Arrow path. This lane is in `ci-required`.
- Rollout docs sweep (skill): every surface that says polars is required / that CSV/full-scan needs `goldencheck[polars]`; CHANGELOG 3.0.0; tuning/config docs; the `engine/CLAUDE.md` seed=42 contract note (goes stale).
- **PyPI publish of 3.0.0 stays HUMAN-GATED** — landing 3.0.0 code on main is not the release; the maintainer cuts the tag (goldencheck 2.0.0 precedent).

## Contract / parity
- Non-stat findings: strict identity (Jaccard 1.0), enforced by the Stage-1 harness as a CI gate going forward.
- Registered accepted divergences (populate `ACCEPTED_DIVERGENCES`, currently empty): owned-sample class, statrs-threshold-flip class, owned dtype vocabulary.
- Population A/C held constant — differential proves zero delta there or the flip is blocked.

## Risks
- **Scope**: Stages 3–4 are the large, irreversible part. Each stage is independently verifiable; Stage 1 (measurement) gates the rest. Do NOT proceed past Stage 1 without the differential coming back within acceptance + human sign-off.
- **Off-path polars modules**: complete dependency removal may surface a module that genuinely needs polars on the scan path; if so, that becomes a scoped sub-task, not a silent `[polars]` retention.
- **Arrow column perf**: the kernels already carry the CPU work; the seam wrapper must not re-materialize per call. Measure wall on the >100k dataset.
- **nopolars test inversion** is a required gate — rewrite, don't just re-enable.

## Non-goals
- No new numerics (kernels already validated). No DataFusion. No PyPI publish (human-gated). No reproduction of the declined scipy dist.fit()/kstest (they stay, gated on `[baseline]`). No touching Population A/C authority.
