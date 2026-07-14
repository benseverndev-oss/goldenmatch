# Splink Migration Upgrade Pass Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `upgrade_splink_conversion(conversion, data, ...)` — a data-aware pass over a converted Splink config that computes TF tables, re-derives distance thresholds from measured string lengths, calibrates link/review thresholds, and MEASURES baseline-vs-upgraded on a bounded sample; surfaced as `goldenmatch import-splink --upgrade DATA`.

**Architecture:** New module `goldenmatch/config/splink_upgrade.py` (sibling of `from_splink.py`, same findings machinery via a shared `ConversionReport`; findings use an `upgrade:`-prefixed splink_path). Three independent copy-on-write levers + a measurement stage that injects both EMResults into `dedupe_df` via temp model files. The converter itself is untouched.

**Tech Stack:** Pure Python POC (thesis phase 1). Reuses: `_build_tf_tables`, `compute_thresholds`, `_sample_blocked_pairs`/`comparison_vector`, `dedupe_df`, `EMResult.save_json`.

**Spec:** `docs/superpowers/specs/2026-07-14-splink-migration-upgrade-design.md` (in the worktree — READ IT FIRST; it pins every lever mechanism).

**Working branch:** `feat/splink-migration-upgrade` (worktree `..\goldenmatch-wt-splink-converter`, off current main which contains the shipped 3.2.0 converter). Package root for all paths: `packages/python/goldenmatch/`.

**Env (Windows box):**
```powershell
cd D:\show_case\goldenmatch-wt-splink-converter\packages\python\goldenmatch
$env:PYTHONPATH = "D:\show_case\goldenmatch-wt-splink-converter\packages\python\goldenmatch"
$env:POLARS_SKIP_CPU_CHECK = "1"; $env:PYTHONIOENCODING = "utf-8"
D:\show_case\goldenmatch\.venv\Scripts\python.exe -m pytest tests/<file> -v
```
Targeted test files ONLY (full suite = CI). NEVER `git stash`. Pre-push: `ruff check packages/python/goldenmatch` (0.15.12) + pyright on touched files (config/ IS in pyright's include — write strict-clean code; the 3.2.0 release taught us: no unused imports, no str|None dict keys, no conditional function redefs). CI pitfalls already survived once: version gate, parity manifest (NO new public API here except exports — add `upgrade_splink_conversion` to `_api.py`/`__init__.py`; it is LIBRARY-only, no new CLI command name and no MCP tool, so parity/goldenmatch.yaml needs NO change — verify: `import-splink` stays one command, `--upgrade` is a flag).

**Verified anchors (from spec review):** `_build_tf_tables(df, mk)` at `core/probabilistic.py:944`; `compute_thresholds(em_result, scored_weights=None, calibrated=None)` at :1225 (posterior mode returns fixed (0.99, 0.50) at :1247-1254; distribution branch needs len>50); `_sample_blocked_pairs` at :440; `comparison_vector` at :300; `dedupe_df` in `_api.py:474` (model via file: matchkey `model_path` / `fs_model_path` param — investigate which in Task U5); converter formula inversion precedent `from_splink.py:268`; CLI current write ordering `cli/import_splink.py:64-133`.

---

## File structure

- Create `goldenmatch/config/splink_upgrade.py` — dataclasses (`MigrationResult`, `MeasurementResult`, `RunStats`, `PairwiseAgreement`, `TruthMetrics`), sampling + column validation, the three levers, orchestrator. Section banners like `from_splink.py`.
- Create `goldenmatch/config/splink_upgrade_measure.py` — the measurement stage only (imports `dedupe_df` lazily; keeps the lever module import-light/edge-clean).
- Modify `goldenmatch/cli/import_splink.py` — `--upgrade/--splink-clusters/--labels/--sample-cap/--no-measure` flags + 4-file write + delta table.
- Modify `goldenmatch/_api.py`, `goldenmatch/__init__.py` — export `upgrade_splink_conversion`, `MigrationResult`.
- Tests: `tests/test_splink_upgrade_levers.py`, `tests/test_splink_upgrade_measure.py`, `tests/test_cli_import_splink_upgrade.py`.

---

### Task U1: scaffold — dataclasses, sampling, validation, orchestrator with skip semantics

**Files:** Create `goldenmatch/config/splink_upgrade.py`, `tests/test_splink_upgrade_levers.py`.

- [ ] **Write failing tests** (TDD): construct a `SplinkConversion` via `from_splink()` on a small settings dict (reuse the 4-level JW fixture shape from `tests/test_from_splink_api.py`); a 30-row polars DataFrame with the matching columns.
  - `upgrade_splink_conversion(conv, df, measure=False)` returns `MigrationResult`; `baseline_config is conv.config` semantics: baseline EQUALS the input config (assert `model_dump()` equality) and the INPUT conversion is unmutated (deep-compare before/after).
  - `upgraded_config` is a distinct object (mutating it doesn't touch baseline).
  - Missing data column for a matchkey field → raises `SplinkUpgradeError` naming the column, BEFORE any lever finding is emitted.
  - `data` as a parquet path loads (write with `df.write_parquet(tmp)`).
  - Sampling: 30 rows with `sample_cap=10` → findings note sampling; the sample is deterministic across two calls (same seed).
  - Bare-settings conversion (no m/u in the fixture) → levers `tf_tables` and `calibration` findings are INFO "skipped: run-time EM training covers this"; `distance_thresholds` still attempted.
  - `levers={"tf_tables"}` runs only that lever.
- [ ] **Implement** the module: dataclasses per the spec sketch; `SplinkUpgradeError(ValueError)`; `_load_frame(data)` (polars read_parquet/read_csv by suffix, DataFrame passthrough); `_sample(df, cap, seed)` using `df.sample(n=cap, seed=seed)` when needed; upfront validation over `conversion.config.get_matchkeys()[0].fields` (skip `__record__`); orchestrator `upgrade_splink_conversion` that deep-copies config (`GoldenMatchConfig(**conversion.config.model_dump())`) and EMResult (rebuild via `EMResult.from_dict(em.to_dict())`), runs registered levers in order (each a function `(ctx) -> None` appending findings to a NEW `ConversionReport` seeded with a copy of `conversion.report.findings`), returns `MigrationResult`. Findings use paths like `upgrade:tf_tables`, `upgrade:sample`. Lever bodies for U1 = the skip/dispatch logic only; the three levers land in U2-U4 (raise `NotImplementedError` behind the skip checks is fine for now — but tests in U1 must only exercise paths that don't hit them: use bare-settings fixtures + `levers=set()` where needed).
- [ ] Run `tests/test_splink_upgrade_levers.py` → PASS. Ruff + pyright the new file. **Commit** `feat(splink-upgrade): scaffold — dataclasses, sampling, validation, lever dispatch`.

### Task U2: lever 1 — TF tables

**Files:** Modify `goldenmatch/config/splink_upgrade.py`; test file from U1.

- [ ] **Failing tests:** trained fixture (m/u on levels; `tf_adjustment_column` on the exact field so the converted field has `tf_adjustment=True`) + a 40-row df with a skewed value distribution on that column.
  - After upgrade: `result.em_model.tf_freqs[field]` is a dict of value→relative-frequency summing to ~1.0 over distinct values; `tf_collision[field] == sum(freq**2)` (match `_build_tf_tables`' exact semantics — READ it first and assert against what it actually computes, incl. transform application).
  - Baseline `conversion.em_model.tf_freqs` stays None (copy-on-write).
  - Field WITHOUT `tf_adjustment` → no table, no finding.
  - Finding: info at `upgrade:tf_tables`, message includes field name + distinct count.
  - Data column full of nulls → warning + skip for that field.
- [ ] **Implement** by CALLING `_build_tf_tables` (probabilistic.py:944) — read its signature first; it takes (df, mk) and returns the per-field tables for the whole matchkey. If it computes for all tf fields at once, call once and merge into the copied EMResult (only fields missing tables). Do NOT reimplement frequency math.
- [ ] Run + **commit** `feat(splink-upgrade): TF tables lever`.

### Task U3: lever 2 — measured distance thresholds

**Files:** Modify `goldenmatch/config/splink_upgrade.py`; test file from U1.

- [ ] **Failing tests:** converted config with an email levenshtein field, thresholds `[0.9, 0.7]` (from Splink distances 1 and 3); df whose email column has mean post-transform length 20.
  - After upgrade: thresholds become `[0.95, 0.85]` (`1 - 1/20`, `1 - 3/20`); `levels` unchanged; findings record old→new + d + L per band.
  - 2-level legacy field (`partial_threshold=0.9` from d=1): partial_threshold → `1 - 1/L`.
  - jaro_winkler fields untouched.
  - Threshold collapse case: contrive L where two distances map to the same sim (e.g. d=1,d=2 with tiny L→ sims clamp/collide) → bands merged, imported m/u SUMMED (mirror `import_em`'s collapse handling), warning emitted, config still validates (`GoldenMatchConfig(**dump)` round-trip).
  - Empty column → warning + skip.
- [ ] **Implement:** per levenshtein-scorer field: `d = round((1 - t) * 10)` per threshold (10 = `_LEV_ASSUMED_LEN` — import the constant from `from_splink` rather than re-hardcoding); L = mean `len()` of the transform-applied non-null sample values (reuse the same transform-application helper `_build_tf_tables`/train_em use — find it once in U2); new `t = max(0.0, 1 - d/L)`; clamp into (0,1] (drop band + warn if 0, like the converter); dedupe/sort desc; when bands collapse and an EMResult exists, sum the collapsed levels' m/u and renormalize (extract or mirror the small collapse block from `import_em`); update the COPIED field's `level_thresholds`/`partial_threshold`/`levels`.
- [ ] Run + **commit** `feat(splink-upgrade): measured distance thresholds lever`.

### Task U4: lever 3 — threshold calibration

**Files:** Modify `goldenmatch/config/splink_upgrade.py`; test file from U1.

- [ ] **Investigate first (30 min cap):** how dedupe builds blocks from a `BlockingConfig` — find the public-ish entry (`goldenmatch.core.blocker` — likely `build_blocks(df, blocking_config)` or similar; grep `def build_block` and how `train_em` gets its `blocks` argument from the pipeline). Record the call in a comment. If block construction requires pipeline context too heavy for the lever, FALLBACK mechanism (allowed by spec intent, document as finding): sample candidate pairs from the blocking keys directly — group rows by each blocking key's transformed tuple, take within-group pairs, cap total (the same shape `_sample_blocked_pairs` produces).
- [ ] **Failing tests:** trained 4-level fixture + 60-row df engineered so blocked pairs exceed 50 and produce a spread of scores.
  - After upgrade: `upgraded_config.get_matchkeys()[0].link_threshold` and `.review_threshold` are set floats in (0,1), absent on baseline; finding records both + n pairs.
  - <=50 scorable pairs (tiny df) → warning + skip, thresholds stay None. BOUNDARY: `compute_thresholds`' data-driven branch requires len STRICTLY > 50; implement the lever skip as `<= 50` — at exactly 50 the function silently falls through to fixed defaults (0.50, 0.35), which the lever must never present as calibrated. Test at exactly 50 pairs.
  - `GOLDENMATCH_FS_CALIBRATION=posterior` (monkeypatch env) → info + skip.
  - Runs AFTER lever 2 (thresholds computed from the lever-2-adjusted model: assert via a fixture where skipping lever 2 would give a different banding — or simpler, assert lever execution order via findings order).
- [ ] **Implement:** candidate pairs per the investigation; per pair `comparison_vector(row_a, row_b, mk_upgraded)` + sum `em.match_weights[field][level]`; min-max normalize the weight vector to 0-1 (the shape `compute_thresholds`' scored_weights branch expects — read its percentile math at :1234-1249 and match); `link, review = compute_thresholds(em, scored_weights=normalized)`; set on the copied matchkey.
- [ ] Run + **commit** `feat(splink-upgrade): threshold calibration lever`.

### Task U5: measurement stage

**Files:** Create `goldenmatch/config/splink_upgrade_measure.py`; test `tests/test_splink_upgrade_measure.py`; wire into the orchestrator.

- [ ] **Investigate:** exact model-injection route into `dedupe_df` — read `_api.py:474` signature (`fs_model_path`?) AND how matchkey `model_path` is consumed (`load_or_train_em` in core). Choose: set `model_path` on each config copy's matchkey pointing at a temp `save_json` file (spec's pinned approach). Confirm `dedupe_df` accepts a config OBJECT (not just path) — it does for the bench (`run_converted_splink.py` used it); mirror that call shape.
- [ ] **Failing tests** (small: 60-row df with planted duplicates, trained fixture):
  - `measure=True` → `MeasurementResult` with `baseline`/`upgraded` `RunStats` (cluster counts ints, wall floats > 0); temp model files cleaned up (glob the tmpdir).
  - `splink_clusters` df (id→cluster_id covering the sample ids) → `vs_splink.baseline`/`.upgraded` pairwise P/R/F1 floats in [0,1].
  - `labels` → `vs_labels` with pairwise + b-cubed (port the b-cubed helper from the bench orchestrator `D:\ER\splink_convert_dogfood\bench\orchestrator.py` — small pure function; put it in the measure module with tests).
  - Neither reference → both None + info finding "shape-only".
  - Measurement raising (monkeypatch dedupe_df to raise) → error finding, `measurement is None`, upgraded config still returned.
  - Bare-settings input → measurement still runs (EM trains on the sample); info finding notes the delta is not imported-model-vs-imported-model.
  - Snowball flag: contrive an upgraded run yielding a giant cluster vs reference max → flag True (test the flag FUNCTION directly with synthetic RunStats if end-to-end contriving is awkward).
- [ ] **Implement:** `run_measurement(ctx) -> MeasurementResult | None`; id column: use the df row identity consistent with `dedupe_df`'s output (investigate what id the result clusters carry — record_id/`__row_id__`/user id column — and REQUIRE an explicit `id_column` param defaulting sensibly; document). Pairwise metrics via within-cluster pair sets with the bench's >5000-member cap guard. Findings for wall/shape go into the report as info.
- [ ] Run + **commit** `feat(splink-upgrade): measurement stage`.

### Task U6: exports + CLI `--upgrade`

**Files:** Modify `goldenmatch/_api.py`, `goldenmatch/__init__.py`, `goldenmatch/cli/import_splink.py`; test `tests/test_cli_import_splink_upgrade.py`.

- [ ] Exports: `upgrade_splink_conversion` + `MigrationResult` (+ `SplinkUpgradeError`) following the `from_splink` lazy-wrapper pattern in `_api.py` (typed signature; one-line docstring deferring to the canonical) and `__init__.py` `__all__`.
- [ ] **Failing CLI tests** (CliRunner pattern from `tests/test_cli_import_splink.py`; no --help scraping):
  - `--upgrade data.parquet --model-out m.json` on a trained settings file → FOUR files: `out.yaml` (model_path→`m.json`), `m.json` (upgraded model WITH tf_freqs when applicable), `out.baseline.yaml` (model_path→`m.baseline.json`), `m.baseline.json` (as-imported model). Baseline pair written FIRST (assert via write-failure injection on the upgraded pair → baseline pair still on disk).
  - `--upgrade` + trained input WITHOUT `--model-out` → exit 1, clear message.
  - `--upgrade` + bare settings → works without `--model-out` (2 yaml files, no models).
  - `--no-measure` skips measurement; `--sample-cap 10` respected (finding text).
  - `--splink-clusters old.parquet` → delta table printed (assert a stable substring like "baseline" + "upgraded" row labels, not rich formatting).
  - Existing non-upgrade tests in `tests/test_cli_import_splink.py` still pass unchanged.
- [ ] **Implement:** new options; with `--upgrade`: run conversion → `upgrade_splink_conversion` → write baseline pair (reuse the existing single-pair write logic refactored into a `_write_pair(config, yaml_path, model, model_path)` helper — keep ordering + partial-model refusal per pair) → write upgraded pair → print combined findings table + a compact delta table (plain rows: metric | baseline | upgraded).
- [ ] Run new + existing CLI tests + **commit** `feat(cli): import-splink --upgrade`.

### Task U7: success-bar validation + docs

- [ ] **Run the success bar** (local, uses the bench assets): fresh-ish venv not needed — use the bench venv `D:\ER\splink_convert_dogfood\bench\.venv` BUT with the WORKTREE goldenmatch via PYTHONPATH override (published 3.2.0 lacks the upgrade pass): from the worktree package root, run `import-splink` with `--upgrade` on `real_time_settings.json` + fake_1000 parquet (`D:\ER\splink_convert_dogfood\bench\data_fake_1000.parquet` — check the bench dir for the exact name) + `--splink-clusters` from the bench's splink clusters CSV, then score the upgraded config through the bench's metric path (adapt `orchestrator.py` minimally or hand-run `child_gm.py` with the upgraded yaml). **Success bar: upgraded pairwise F1 vs truth >= 0.541 (half the 0.482→0.601 gap) on this pair, and re-run the other two pairs (saved_model_from_demo, model_h50k) — neither regresses below its baseline F1 (0.677 / 0.707).** Record all numbers in the plan-completion report and PR body. If the bar fails: debug with @superpowers:systematic-debugging (prime suspects: TF collision-rate semantics, calibration operating point too aggressive) — do NOT ship a failing bar; surface to the coordinator if genuinely blocked.
- [ ] **Docs:** CHANGELOG Unreleased (upgrade pass); README "Migrating from Splink" section gains the `--upgrade` one-liner; docs-site `goldenmatch/cli.mdx` import-splink row mentions `--upgrade`; scoring.mdx or a migration section gets 3 sentences on the levers. `_fs_native_eligible`/parity manifest untouched (no new command/tool).
- [ ] **Commit** `docs: splink upgrade pass documentation + measured success-bar numbers`.

### Task U8: land it

- [ ] Pre-push gates: `ruff check packages/python/goldenmatch`; pyright (CI pin 1.1.409) on the repo config; targeted pytest of all new/touched test files in one invocation, plus `tests/test_from_splink_api.py tests/test_cli_import_splink.py` (regression).
- [ ] Push `feat/splink-migration-upgrade` (benzsevern token-URL dance), PR with the measured success-bar table in the body, `gh pr merge --auto`, background-watch to merged. Fix CI reds if any (the 3.2.0 lessons: ruff import sorts in new test files; pyright strict on config/; version gate only trips on version bumps — none here).
