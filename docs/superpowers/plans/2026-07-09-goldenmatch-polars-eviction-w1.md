# GoldenMatch Polars Eviction W1 Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Land Wave 1 of the Polars eviction: the Arrow IO front (`core/io_arrow.py`) proven by an ingest-parity corpus, the ArrowFrame backend, the env-gated `GOLDENMATCH_FRAME=arrow` reader swap for file-based runs, and the differential harness (frozen canonical fixtures + advisory CI lane). Default behavior byte-identical; arrow mode is experimental.

**Architecture:** In arrow mode, file-based runs read via pyarrow (CSV/parquet/Excel) into a `pa.Table`, then convert once at the boundary (`pl.from_arrow`, zero-copy) into the existing pipeline. The parity contract's gate #1 (ingest parity) is enforced by a corpus test comparing both readers over every standard fixture. The differential harness runs file-based dedupe under both backends and diffs canonicalized outputs (cluster sets, golden value maps, pair sets).

**SCOPE NOTE vs the spec's W1 row (recon-driven adjustment, flagged for user review):** the spec's W1 row says "the fused spine wired end-to-end on ArrowFrame". Recon (2026-07-09, post-W0) found `run_match_fused_arrow`'s own prep derives block keys and transformed score columns via Polars expressions (`_build_block_key_expr`, `_get_transformed_values`, and an internal `pl.DataFrame(...).cast(pl.Utf8)` at `core/fused_match.py:147`) -- that derivation is expression glue, which the spec assigns to W2. W1 therefore delivers the Arrow IO front + backend + harness, and the polars-free kernel-prep (the true "spine on ArrowFrame") moves to W2 where the expression glue ports. Task 9 amends the spec's W1/W2 rows to record this. The pre-W2 arrow mode still exercises: pyarrow readers end-to-end, boundary conversion, and the full differential gate.

**Tech Stack:** pyarrow (csv/parquet), openpyxl (Excel, already a dep), pytest. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-07-09-goldenmatch-polars-eviction-design.md`

**Working directory:** NEW worktree off fresh origin/main AFTER PR #1616 (W0) merges: `git worktree add .worktrees/gm-polars-w1 -b feat/goldenmatch-polars-eviction-w1 origin/main`. All paths relative to `packages/python/goldenmatch/` unless starting with `docs/` or `scripts/`.

**Test invocation (worktree + main .venv, Windows)** -- abbreviated `RUNPY` below:

```bash
cd /d/show_case/goldenmatch/.worktrees/gm-polars-w1/packages/python/goldenmatch
PYTHONPATH="D:\\show_case\\goldenmatch\\.worktrees\\gm-polars-w1\\packages\\python\\goldenmatch" \
POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 PYTHONIOENCODING=utf-8 \
D:/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest <target> -v --timeout=120
```

Never run the full suite locally. Do not edit existing test files.

**Verified recon facts (do not re-derive):**
- `core/ingest.py::load_file` semantics to mirror: parquet -> `pl.scan_parquet`; Excel -> `pl.read_excel(engine="openpyxl"[, sheet_name])`; CSV -> explicit `encoding` in {utf8, utf8-lossy} uses `pl.scan_csv(path, separator, encoding)`; any OTHER explicit Python codec decodes the file in Python then `pl.read_csv(io.StringIO(text))`; AUTO mode probes `_is_probably_utf8`: valid-UTF-8 -> `scan_csv(encoding="utf8-lossy")` (NOT "utf8"), non-UTF-8 -> cp1252 decode with `errors="replace"` plus a WARNING log. Returns LazyFrames. (Verified against source 2026-07-09; re-read `load_file` before implementing.)
- The pipeline pre-casts user columns to Utf8 on the dedupe path (pipeline.py:1188), so ingest dtype differences that survive to scoring are limited -- but the parity corpus still pins reader-level values + canonicalized dtypes (gate #1: fix divergences AT THE IO LAYER, not downstream).
- Known Arrow-CSV divergence knobs to pin in Task 2: `ConvertOptions(timestamp_parsers=[])` (arrow auto-parses ISO timestamps; polars scan_csv does not -- NOTE arrow may STILL infer `date32` for `YYYY-MM-DD` via a separate inference path; if `timestamp_parsers=[]` does not suppress it, the remaining knob is per-column `column_types` after a header peek, or the documented-delta valve), `null_values` defaults differ, quoting/junk-row handling differs from `ignore_errors=True`. The corpus decides each case; the rule is bug-compatible-with-polars during transition (deliberate all-text inference like goldenflow's columnar reader is a W5+ option, NOT W1).
- `GOLDENMATCH_FRAME` did not exist before this wave. `core/frame.py` (W0) has Frame/Column Protocols + PolarsFrame + `to_frame`.
- Differential lane precedent: advisory CI lanes (not in `ci-required`) = `infermap_native`, `goldenanalysis_native`. Path-filter entry needed in `.github/workflows/ci.yml` `changes` job + `if:` gate.

---

### Task 1: Ingest-parity corpus harness (red first)

**Files:**
- Test: `tests/test_io_arrow_ingest_parity.py`

- [ ] **Step 1: Write the corpus test.** It enumerates the standard fixtures (`tests/conftest.py` fixture files: sample CSVs + parquet; plus purpose-built dirty files created by the test into tmp_path: latin-1 encoded CSV, utf8-with-invalid-bytes CSV, junk-row CSV, leading-zero zip CSV, ISO-date-string CSV) and for each file asserts `read_table_arrow(path)` equals the polars `load_file(path).collect()` read on: column names, row count, per-cell VALUES after canonicalization (both casted to string via each engine's cast; nulls compared as None), and canonicalized dtype class (str/int/float/date/other -- reuse the goldencheck `_neutral_dtype` idea locally in the test).
- [ ] **Step 2: Run -> expect ImportError (`goldenmatch.core.io_arrow` does not exist).** Commit the red test with `test:` prefix ONLY if the repo's hooks allow red commits -- otherwise hold the commit until Task 2 greens it (single commit for tasks 1+2 is acceptable).

### Task 2: `core/io_arrow.py` -- CSV arm

**Files:**
- Create: `goldenmatch/core/io_arrow.py`
- Test: (Task 1's corpus)

- [ ] **Step 1: Implement `read_table_arrow(path, *, separator=",", encoding=None, sheet=None) -> pa.Table`** mirroring `load_file`'s CSV branch: auto-probe utf8 (reuse/`import` `_is_probably_utf8` from ingest.py -- do NOT duplicate it); utf8 -> `pyarrow.csv.read_csv` with pinned `ParseOptions(delimiter=separator)` + `ConvertOptions(timestamp_parsers=[])`; utf8-lossy and non-utf8 codecs -> decode bytes in Python (`errors="replace"` for lossy; the named codec otherwise) and feed `pa.csv.read_csv(pa.BufferReader(encoded_utf8_bytes))`. Import pyarrow lazily INSIDE functions (pyarrow is a base dep but keep import-time lean; the W0 gate does not cover pyarrow, no gate risk either way).
- [ ] **Step 2: Iterate against the corpus until green.** Every divergence gets fixed in io_arrow via reader options, NOT by weakening the test. If a divergence is genuinely unfixable at the reader (document why), it may be canonicalized in the TEST with an inline comment + an entry in the module docstring's "known reader deltas" list -- expected to be rare; surface any such case in the task report.
- [ ] **Step 3: ruff + commit** `feat(goldenmatch): Arrow CSV reader with polars-parity corpus (eviction W1)`.

### Task 3: `io_arrow` parquet + Excel arms

- [ ] **Step 1: Extend the corpus** with the parquet fixture + a small xlsx written by the test (openpyxl).
- [ ] **Step 2: Implement:** parquet -> `pyarrow.parquet.read_table(path)`; Excel -> openpyxl direct (values -> columns -> `pa.table`), honoring `sheet`. Mirror `pl.read_excel(engine="openpyxl")` semantics per the corpus.
- [ ] **Step 3: Green + ruff + commit** `feat(goldenmatch): Arrow parquet/Excel readers (eviction W1)`.

### Task 4: ArrowFrame backend

**Files:**
- Modify: `goldenmatch/core/frame.py`
- Test: `tests/test_frame_seam_arrow.py` (new; existing `test_frame_seam.py` unedited)

- [ ] **Step 1: Failing tests:** mirror `test_frame_seam.py`'s five tests for ArrowFrame (constructed from `pa.Table`), PLUS a cross-backend parity test: same logical data via PolarsFrame and ArrowFrame -> identical `columns`/`height`/`null_count`/`n_unique`/`to_list`/`to_arrow_columns` values. PLUS `to_frame(pa_table)` returns ArrowFrame; `to_frame` stays idempotent for both.
- [ ] **Step 2: Implement `ArrowColumn`/`ArrowFrame`** (`__slots__`, ops over `pa.Table`/`pa.ChunkedArray`: `len`, `null_count` (chunked array `.null_count`), `n_unique` (`pyarrow.compute.count_distinct` -- verify null-counting parity with polars `n_unique`, which COUNTS null as a distinct value; pin with a test row and adjust: pc.count_distinct(mode=...) or manual +1 when nulls present), `to_list` (`to_pylist`), `to_arrow` (identity), `to_arrow_columns` (column slices)). Extend `to_frame` to accept `pa.Table` (lazy `import pyarrow` inside, checked AFTER the polars isinstance so the W0 gate posture is unchanged).
- [ ] **Step 3: Green + gate re-check** (`tests/test_lazy_import_gate.py`) + ruff + commit `feat(goldenmatch): ArrowFrame backend with cross-backend parity (eviction W1)`.

### Task 5: `GOLDENMATCH_FRAME` resolution + file-ingest wiring

**Files:**
- Modify: `goldenmatch/core/frame.py` (resolver), `goldenmatch/core/ingest.py` (wiring seam)
- Test: `tests/test_frame_backend_env.py`

- [ ] **Step 1: Failing tests:** `resolve_frame_backend()` returns "polars" by default, "arrow" when `GOLDENMATCH_FRAME=arrow`, raises on unknown values; `load_file` under `GOLDENMATCH_FRAME=arrow` (monkeypatched env) returns a LazyFrame whose collected content equals the polars-mode read for a sample CSV (the boundary conversion `pl.from_arrow(table).lazy()`); a log record notes arrow mode once.
- [ ] **Step 2: Implement:** `resolve_frame_backend()` in frame.py; in `ingest.load_file`, an arrow-mode branch: `read_table_arrow(...)` -> `pl.from_arrow(tbl).lazy()` with `logger.info("GOLDENMATCH_FRAME=arrow: file read via pyarrow (experimental)")`. The arrow branch applies ONLY to the branches io_arrow covers (CSV/parquet/Excel); the `smart_load` route (non-.csv text files / non-auto `parse_mode`) stays on the Polars path unchanged under arrow mode. Default path byte-identical (the branch is checked ONCE via the env). Do NOT touch dedupe_df (DataFrame entry -- arrow mode is file-path-only until W5's API change).
- [ ] **Step 3: Green + ruff + commit** `feat(goldenmatch): env-gated Arrow ingest lane GOLDENMATCH_FRAME=arrow (eviction W1)`.

### Task 6: Differential runner + frozen canonical fixtures

**Files:**
- Create: `scripts/diff_frame_backends.py`
- Create: `packages/python/goldenmatch/tests/fixtures/frame_diff/` (frozen canonical outputs, small)
- Test: `tests/test_frame_backend_differential.py`

- [ ] **Step 1: Canonicalization helpers + runner.** `scripts/diff_frame_backends.py`: for each corpus dataset (2-3 SMALL fixture CSVs with known duplicates -- reuse/derive from `tests/conftest.py` fixtures; keep CI wall < 2 min), run file-based `run_dedupe` in a SUBPROCESS with `GOLDENMATCH_FRAME` set (env isolation), using an EXPLICIT DETERMINISTIC config -- NOT zero-config. Zero-config on small frames is documented-nondeterministic across processes (EM sample order shifts clusters; 3+ field weighted matchkeys enable `rerank=True`, which downloads a HuggingFace cross-encoder and fails offline CI). Pin exact + fuzzy matchkeys with `rerank=False`, no probabilistic/EM, per-dataset in the script. Canonicalize results: clusters -> sorted list of sorted member-id lists; golden -> map sorted-members-key -> {col: str(value)}; pairs -> sorted `(min,max,round(score,12))` list; write JSON per backend; diff; exit 1 on mismatch; also print wall + peak RSS per backend (psutil, already a dep).
- [ ] **Step 2: Freeze fixtures:** run the polars backend once, commit its canonical JSONs under `tests/fixtures/frame_diff/`. New test `test_frame_backend_differential.py`: (a) polars backend reproduces the frozen JSON (regression anchor); (b) arrow backend reproduces the SAME frozen JSON (the differential gate). Subprocess-based, `--timeout=120`-safe sizes.
- [ ] **Step 3: Green + ruff + commit** `feat(goldenmatch): frame-backend differential harness + frozen canonical fixtures (eviction W1)`.

### Task 7: Advisory CI lane

**Files:**
- Modify: `.github/workflows/ci.yml`

- [ ] **Step 1:** Add `goldenmatch_frame_diff` filter entry in the `changes` job (paths: `packages/python/goldenmatch/**`, `scripts/diff_frame_backends.py`) and a new job gated on it: checkout, setup-python, `uv sync` per existing goldenmatch-lane pattern (copy an adjacent advisory lane's steps, e.g. `infermap_native`), run `python scripts/diff_frame_backends.py`. NOT added to `ci-required` (advisory, mirrors the `infermap_native` precedent). NOTE: editing ci.yml forces the full matrix on this PR's queue entry -- expected, one-time.
- [ ] **Step 2:** Validate YAML (`python -c "import yaml,io;yaml.safe_load(io.open('.github/workflows/ci.yml',encoding='utf-8'))"`) + commit `ci(goldenmatch): advisory frame-backend differential lane (eviction W1)`.

### Task 8: Docs

**Files:**
- Modify: `docs-site/goldenmatch/tuning.mdx` (the canonical runtime-config surface)
- Modify: `packages/python/goldenmatch/CHANGELOG.md` ([Unreleased])

- [ ] **Step 1:** tuning.mdx: add `GOLDENMATCH_FRAME` row -- values `polars` (default) / `arrow` (experimental: file ingest via pyarrow; part of the Polars-eviction program; output-equivalence enforced by the differential harness). ASCII only.
- [ ] **Step 2:** CHANGELOG [Unreleased] Added: one line for the experimental flag. Commit `docs(goldenmatch): document GOLDENMATCH_FRAME experimental lane (eviction W1)`.

### Task 9: Spec amendment (the W1/W2 boundary)

**Files:**
- Modify: `docs/superpowers/specs/2026-07-09-goldenmatch-polars-eviction-design.md`

- [ ] **Step 1:** Amend the wave table: W1 row -> "Arrow IO (`core/io_arrow.py`) + ArrowFrame backend + env-gated ingest lane + differential harness/fixtures"; W2 row gains "port the fused-kernel prep derivation (`_build_block_key_expr`, `_get_transformed_values`, fused_match.py's internal pl.DataFrame) so the covered-config spine runs polars-free end-to-end". Add one sentence to section 3 recording the recon rationale. Commit `docs(goldenmatch): amend eviction spec W1/W2 boundary per W0 recon (eviction W1)`.

### Task 10: Verification + PR

- [ ] ruff on package+tests+scripts -> clean; pyright on new/touched files -> no NEW errors (local `reportMissingImports` noise excepted).
- [ ] Batches: all W1 test files + `tests/test_lazy_import_gate.py tests/test_frame_seam.py tests/test_pipeline.py tests/test_api.py` -> pass.
- [ ] Invariants: default-mode diff purity (`git diff origin/main -- packages/python/goldenmatch/goldenmatch/` touches only ingest.py [the gated branch], frame.py, io_arrow.py [new]); no dependency change (`pyproject.toml` diff empty).
- [ ] Push (auth dance: `unset GH_TOKEN; gh auth switch --user benzsevern`), PR titled `feat(goldenmatch): Polars eviction W1 -- Arrow IO front + differential harness`, arm `gh pr merge --auto --squash`, switch back, STOP.

## Out of scope for W1 (explicitly)

- NO classic-engine op ports (blocker/scorer/cluster/golden stay Polars) -- W2.
- NO polars-free fused-kernel prep -- W2 (see scope note).
- NO controller/autoconfig changes -- W3.
- NO API change; results remain Polars frames -- W5.
- NO dependency changes.
