# GoldenCheck Flip cutover — implementation plan

> Executes the owned-contract cutover from `2026-07-11-goldencheck-flip-3.0.0-design.md`, decision folded: **gate on `[baseline]` extra** — base install = polars-free default scanning; baseline/drift/correlation stay polars+scipy behind an extra.

**Goal:** `scan_file`/`scan_dataframe` run polars-free on the default path via the Arrow seam (kernel-authoritative), owned deterministic sample + neutral dtype vocabulary become the emitted contract, `[polars]` leaves the base/default surface, version → 3.0.0. PyPI publish stays human-gated.

**Foundation (landed, Stage 0 / PR #1692):** `ArrowColumn`/`ArrowFrame` seam + §8b differential (strict Jaccard 1.000). Baseline suite = 838 engine/profilers/relations/core green.

**Gate at every stage:** the 838-test suite green (fixing tests to the owned contract where the contract legitimately changed), the §8b differential still 1.000 strict, `import goldencheck` polars-free.

---

## Stage A — scan engine Arrow-native (the core; owned contract on)

**Files:** `engine/scanner.py` (`_scan_dataframe_impl`, `_post_classification_checks`, the `scan_file`/`scan_dataframe` entry points), `engine/reader.py`, `engine/sampler.py`, `semantic/classifier.py`, the 5 non-seam-clean relation profilers.

- **A1 — read into Arrow.** `read_file`/`read_columns` return a `pyarrow.Table` (owned `csv_infer` for CSV, `pyarrow.parquet` for parquet, `openpyxl`→Arrow for excel — all already polars-free). `scan_file(path)` builds an `ArrowFrame`. `scan_dataframe` accepts a `pyarrow.Table` natively; keep a `pl.DataFrame` convenience overload that converts via `.to_arrow()` ONLY when polars imports.
- **A2 — loop through the seam.** `_scan_dataframe_impl` iterates `frame.column(name)` (ArrowFrame) instead of `df[col]`; `inferred_type = frame.column(name).dtype_repr()` (neutral vocab). Cache the per-column `column_aggregate` for len/null/n_unique. Remove the Population B shadow blocks (the kernel is now the authoritative `col.method()`).
- **A3 — owned deterministic sample.** `engine/sampler.py`: replace `df.sample(n, seed=42)` with an owned stride/reservoir sample over the Arrow table (seeded, stable across runs + workers). Register the owned-sample divergence class in the parity harness.
- **A4 — relation profilers.** Port `age_validation` + `approx_duplicate` to their W3 kernels (`age_mismatch`/`duplicate_signatures`) authoritatively over the ArrowFrame (they already shadow them). Reroute `composite_key`/`functional_dependency`/`approx_fd` off `frame.native` onto `frame.column(...)`/seam `to_list`.
- **A5 — `_post_classification_checks` + `classify_columns` + `semantic/classifier`.** Route through the seam; add the small missing seam ops semantic needs (`str_len_chars`-mean via Arrow, `head(n).to_list`). These are on the default path so must be polars-free.
- **Gate A:** 838 suite green (owned-contract test fixes: inferred_type neutral strings, owned-sample-dependent findings), differential 1.000 strict, `python -c "import goldencheck"` imports zero polars, and a default `scan_file` on a parquet/csv works with polars UNINSTALLED (smoke).

## Stage B — dependency surface

**Files:** `pyproject.toml`, module import audit.
- Move `polars` from the standalone `polars` extra to a dep of the `[baseline]` extra (scipy+polars). Base deps: no polars.
- Audit every `from goldencheck._polars_lazy import pl` on the default scan path — none may be reached without polars. Off-path modules (llm/agent/cli/reporters/tui/differ/db_scanner) keep the lazy shim but must not be imported by the default path.
- **Gate B:** default `scan_file` + `scan_columns` + CLI `check` run in a polars-UNINSTALLED interpreter; `[baseline]`-gated features raise a clean "install goldencheck[baseline]" when polars/scipy absent.

## Stage C — version + nopolars tests

**Files:** `pyproject.toml` `version`, `goldencheck/__init__.py __version__`, `server.json` (×2), `tests/nopolars/test_polars_absent.py`.
- Bump 3.0.0 in lockstep (the `version_consistency` required gate).
- Rewrite `tests/nopolars/test_polars_absent.py`: the assertions that the polars-absent path *declines* invert — `scan_file(csv)`/`scan_file(parquet)` must now SUCCEED via the owned/Arrow path. This lane is in `ci-required`.
- **Gate C:** `version_consistency` green; nopolars lane green with the inverted assertions.

## Stage D — docs sweep (rollout-docs-sweep skill)

- Every surface that says polars is required / that CSV or full-scan needs `goldencheck[polars]`; the `[baseline]` extra; CHANGELOG 3.0.0; tuning/config docs; `engine/CLAUDE.md` seed=42 contract note (goes stale — owned sample now).
- Removal/rename grep: `seed=42`, `pl.read_csv`-requires, `goldencheck[polars]` messaging.
- **Gate D:** doc CI gates (callout-sync/version-consistency/nav) green.

## Stage E — land

- PR to main; merge on green (merge queue). **Do NOT tag/publish 3.0.0 to PyPI — human-gated** (2.0.0 precedent).

---

## Risk register
- **Test fan-out:** A2/A3 change the emitted contract (dtype strings, sampled rows) → many existing tests assert the old contract. Each fix must be a genuine contract update, not a masking change; the differential is the guard that findings are otherwise identical.
- **Excel→Arrow** path: openpyxl returns Python cells; confirm the owned typing matches the prior `pl.read_excel` dtype inference or register the delta.
- **age_validation/approx_duplicate kernel ports:** their W3 kernels were shadow-validated on fixtures; porting them to authoritative must keep the relation-profiler tests green (they encode the Polars behavior) — the W3 parity already proved byte/set-identity, so expect green.
- **Non-goals:** no baseline/drift/correlation port (gated on `[baseline]`), no new numerics, no DataFusion, no PyPI publish.
