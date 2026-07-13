# GoldenMatch autoconfig Arrow-port Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Each PR is
> its own branch off the predecessor's merged main. Steps use checkbox (`- [ ]`).

**Goal:** Make zero-config `dedupe_df` run Polars-free by Arrow-porting the
autoconfig stack + the `run_dedupe_df` controller front-door.

**Architecture:** Extend the existing `frame.py` dual-backend seam (add the few
missing ops), reroute each polars-bound autoconfig call site through `to_frame(df)`
leaving module test files unedited (byte-parity proof), then flip the boundary
last. Sampled paths assert config-equivalence, not row-identity.

**Spec:** `docs/superpowers/specs/2026-07-13-goldenmatch-autoconfig-arrow-port-design.md`

**Reference skills:** @superpowers:test-driven-development, @superpowers:verification-before-completion

---

## Box + CI conventions (every PR)

- goldenmatch Python tests: `.venv/Scripts/python.exe -m pytest <path>` with
  `POLARS_SKIP_CPU_CHECK=1 PYTHONIOENCODING=utf-8`; `GOLDENMATCH_NATIVE=0` where
  the native kernel would hang the box. Heavy/native/distributed lanes = CI's job.
- Toggle backends with `GOLDENMATCH_FRAME=arrow` / `=polars` to exercise both.
- Each PR: fold `origin/main` before shipping; `ruff check packages/python/goldenmatch`
  (whole package, isort); watch pytest-split shard-shift (new test files → 
  rootdir-relative deselects, verify "N deselected").
- Commit trailer:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01RvH3nXr1xZeVdx6772
  ```
- **CI note (learned this session):** any ci.yml edit force_all's the full matrix
  in the merge queue; the goldenmatch-consuming lanes now install `goldenmatch[polars]`
  (#1747 stopgap), so zero-config-needs-polars is masked — do NOT rely on those
  lanes to catch a polars regression until PR-6's tripwire lands.

---

## PR-1 — seam-op foundation (fixtures-first, pure-additive)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/frame.py` (Protocol + both backends)
- Modify: `packages/python/goldenmatch/goldenmatch/core/arrow_derive.py` (arrow impls if needed)
- Test: `packages/python/goldenmatch/tests/test_frame_relational_ops.py`

New seam ops (each: `Frame`/`Column` Protocol method + `PolarsFrame` + `ArrowFrame`):
1. **`cast_all_str()`** — cast every non-`__`-prefixed column to Utf8. Polars:
   `df.cast({c: pl.Utf8 for c in df.columns if not c.startswith("__")})`. Arrow:
   cast those columns to LargeUtf8 (match `run_dedupe_df:439` semantics exactly —
   `__`-prefixed columns untouched).
2. **`count_duplicate_rows()`** — `height - distinct_row_count()`. (Confirm
   `distinct_row_count` semantics on both backends match `df.unique().height`.)
3. **all-empty-row count** — decide: a seam op, OR a Python helper over
   `to_frame(df).select_dicts()`/`to_list()` (cold path; W3 plan's stated approach).
   Prefer the Python-side helper if the fold is cheap; document the choice.
4. **grouped block-size** (only if PR-5 needs it beyond `group_len`/`run_lengths`) —
   defer to PR-5 if unclear; do NOT speculatively add.

- [ ] **Step 1: Write failing parity fixtures** in `test_frame_relational_ops.py`
  for `cast_all_str` and `count_duplicate_rows`: build a frame with mixed dtypes +
  `__`-prefixed cols + duplicate rows + nulls; assert `PolarsFrame` and `ArrowFrame`
  produce identical results (cast: same string spelling per cell; dup-count: same
  integer). Pin the semantic deltas (null handling, `__`-col exclusion).
- [ ] **Step 2: Run — expect FAIL** (ops undefined).
  `POLARS_SKIP_CPU_CHECK=1 ... pytest tests/test_frame_relational_ops.py -k "cast_all_str or duplicate" -v`
- [ ] **Step 3: Implement** the ops on the Protocol + both backends (+ arrow_derive
  helper if the arrow impl is non-trivial). Cite the call site in each docstring.
- [ ] **Step 4: Run — expect PASS.** Also run the full `test_frame_relational_ops.py`
  (unchanged existing ops must stay green).
- [ ] **Step 5: whole-package ruff + commit.**
  `git commit -m "feat(goldenmatch): cast_all_str + count_duplicate_rows seam ops (autoconfig arrow-port PR-1)"`

**Review:** spec-compliance (are ONLY these ops added, both backends, fixtures pin
semantics?) + code-quality (DRY vs sibling ops, no call-site changes leaked).
Arm auto-merge; land before PR-2.

---

## PR-2 — `profiler.profile_dataframe` port

**Files:** Modify `core/profiler.py`; leave `tests/test_profiler.py` UNEDITED.

- [ ] Route `profile_dataframe`'s `df.unique().height` → `count_duplicate_rows`/
  `distinct_row_count`; the all-empty-row fold → the PR-1 helper; `df.filter(...)`
  and `df[col].dtype in (pl.Utf8, pl.String)` → seam (`filter_valid_key`/`semantic_dtype`
  or the dtype-spelling check). Accept `to_frame(df)` at the top.
- [ ] **Gate:** `test_profiler.py` UNEDITED passes (byte-parity on the polars path)
  AND a new arrow-path assertion (`GOLDENMATCH_FRAME=arrow`) matches.
- [ ] ruff + commit + review + land.

**Risk: low.**

---

## PR-3 — dtype contract + `autoconfig.py` body residue

**Files:** Modify `core/autoconfig.py`; add an arrow→polars dtype-spelling map
(new small module or a table in autoconfig/frame); tests for the map.

- [ ] **Dtype map:** at the `profile_columns` → `autoconfig_classify_columns`
  boundary, map arrow dtype spelling to the polars spelling the kernel expects
  (`"double"`→`"Float64"`, `"large_utf8"`/`"string"`→`"Utf8"`, int/bool/date/…).
  Pin against the kernel's accepted vocabulary + the existing golden classifier
  vectors (which must NOT change).
- [ ] Route the remaining `autoconfig.py` scalar accessors (`.height`/`.columns`/
  `.dtype`), `df.filter(pl.col().is_not_null())`, and `auto_configure(files)` file
  ingest (off `pl.read_csv/parquet/excel`+`pl.concat` → the io_arrow ingest) through
  the seam. Do NOT touch the ~3595 unwrap yet (that's PR-6).
- [ ] **Gate:** autoconfig's config decisions unchanged on the polars path
  (existing autoconfig tests unedited) + the dtype map produces identical
  `classify_columns` output arrow-vs-polars.
- [ ] ruff + commit + review + land.

**Risk: medium** (dtype contract is cross-surface; assert classify-output identity).

---

## PR-4 — controller residue

**Files:** Modify `core/autoconfig_controller.py`; leave its tests unedited.

- [ ] `pl.concat([df, reference], how="vertical_relaxed")` (2 sites) →
  `concat_frames(relaxed=True)`; fix type hints; confirm sampling is fully seam.
- [ ] **Gate:** controller tests unedited pass; arrow-path config-equivalence.
- [ ] ruff + commit + review + land.

**Risk: low-medium.**

---

## PR-5 — blocker measurement / block-key tail  [USER CHECKPOINT — recall-critical]

**Files:** Modify `core/blocker.py`; new block-membership parity corpus + test.

- [ ] **Before rewiring:** build a block-membership corpus that pins, byte-identical,
  the block assignment (`build_blocks` / `_fast_static_block_sizes` /
  `measure_blocking_profile`) native/polars-vs-arrow on a realistic multi-matchkey
  fixture. This is the recall-critical gate.
- [ ] Give `_build_block_key_expr` (`pl.Expr`) / `_fast_static_block_sizes` /
  `measure_blocking_profile` an arrow twin via `derive_block_key` + `group_len`/
  `run_lengths` (+ a grouped block-size op if genuinely needed). Handle the
  `pl.concat_str`/`map_elements`/soundex fallbacks — hand-roll to match, no
  `pl.Expr`. Remove the 2nd `pl.from_arrow` (blocker.py ~1191).
- [ ] **Gate:** block membership byte-identical on the corpus; `test_blocker.py`
  unedited passes.
- [ ] **STOP — surface to the user** (block-key parity evidence + the diff) before
  arming auto-merge. Only land after the user confirms.

**Risk: HIGH.** `pl.Expr` has no direct seam analog; block-key parity is recall-critical.

---

## PR-6 — boundary flip + controller front-door  [the crux]

**Files:** Modify `core/autoconfig.py` (remove ~3595 unwrap; widen sig),
`core/pipeline.py` (`run_dedupe_df` front), `_api.py` (`dedupe_df` sig); new
zero-config-no-polars tripwire test.

- [ ] Remove the `auto_configure_df` unwrap (`pl.from_arrow`, ~3595). Widen
  `auto_configure_df`/`dedupe_df` signatures to accept `pl.DataFrame | pa.Table |
  Frame`; route through `to_frame(df)` at the top (idempotent-coercion, spine
  pattern). Let `reference` flow arrow too.
- [ ] Port `run_dedupe_df`'s front: `df.cast({...pl.Utf8})` (pipeline.py:439) →
  `cast_all_str`; `_add_row_ids` on `pl.LazyFrame` (731/856) → `ensure_row_ids` on
  the Frame; drop the `.lazy()/.collect()` polars round-trip. So the controller's
  per-iteration sample re-run is polars-free.
- [ ] **Acceptance test (the whole port's gate):** a subprocess tripwire — block
  polars import (`sys.modules["polars"]=None` or a meta-path finder), assert
  `native_available()==True`, then `dedupe_df(pa.Table, config=None)` (zero-config)
  runs to completion and returns a result. Mirror the eviction's covered-spine
  tripwire. Add to CI (`GOLDENMATCH_FRAME=arrow` lane).
- [ ] **Gate:** all existing dedupe/autoconfig tests green (polars path unchanged);
  the tripwire green; config-equivalence arrow-vs-polars on the differential harness.
- [ ] ruff + commit + review + land (arm after review; the tripwire is the sign-off).

**Risk: HIGH** — blast radius = every zero-config caller.

---

## PR-7 (follow-up) — drop the CI [polars] stopgap + docs sweep

- [ ] Now that zero-config runs polars-free (PR-6 tripwire green on CI), revert the
  #1747 `[polars]` additions on the goldenmatch-consuming lanes (rust/rust_pgrx/
  duckdb/dbt) IN STAGES, confirming each lane green polars-free. (Keep [polars] only
  where a lane genuinely exercises the polars backend by design.)
- [ ] Docs sweep (rollout-docs-sweep): correct the eviction "COMPLETE" claim re:
  zero-config; ADR for the autoconfig port; update `project_goldenmatch_polars_eviction`
  + `project_goldenmatch_autoconfig_arrow_port` memory + the work tracker.

---

## Landmine checklist (per PR)

- [ ] Module test file UNEDITED where the PR claims byte-parity (the proof).
- [ ] Sampled paths assert config-equivalence, NOT row-identity.
- [ ] Whole-package `ruff check` (isort).
- [ ] New test file → verify pytest-split "N deselected" (rootdir-relative).
- [ ] Fold origin/main before shipping (avoid stale-base dup).
- [ ] PR-5: block-membership byte-identical corpus BEFORE rewire; user checkpoint.
- [ ] PR-6: the no-polars tripwire is the acceptance gate; do not claim done without it green on CI.
