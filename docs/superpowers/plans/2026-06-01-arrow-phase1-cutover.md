# Arrow Phase 1 cutover (pair stream columnar) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the fuzzy scorer + `build_clusters` use a Polars DataFrame `(id_a,id_b,score)` as the canonical pair-stream representation (instead of `list[tuple]`), migrate the hot consumers to it natively, shim the cold ones, and gate the cutover on feasibility(5M)+parity(1M) — so the 131M-tuple Python list that OOMs at 5M never materializes.

**Architecture:** Approach A from the spec — `find_fuzzy_matches`/`score_blocks_*` return a uniform `pl.DataFrame`; `build_clusters` ingests it natively via the existing `_pairs_df_to_list_numpy`; N-scaling consumers go DataFrame-native, sample/serialization consumers keep a `pairs_df_to_list()` boundary shim. Legacy list emit stays behind the existing flag for one release, deleted in N+1.

**Tech Stack:** Python 3.12, Polars, numpy, rapidfuzz, pytest. Scope per `docs/superpowers/specs/2026-06-01-arrow-phase1-cutover-design.md`. Issue #623.

**Scope guard:** This plan does NOT change `_is_columnar_eligible` / widen the pipeline fast-path (Phase B/C), and does NOT delete the legacy path (that's release N+1). It changes the scorer/cluster CONTRACT and migrates consumers.

---

## File Structure

- **Modify** `core/scorer.py` — `find_fuzzy_matches` (uniform DataFrame return across all 3 branches), `_score_one_block` + `score_blocks_parallel` (DataFrame accumulation), keep `pairs_df_to_list` shim. `PAIR_STREAM_SCHEMA` already defined.
- **Modify** `core/cluster.py` — `build_clusters` polymorphic on `pl.DataFrame | list[tuple]`; route the DataFrame branch through `_pairs_df_to_list_numpy` (no Python tuple list).
- **Modify** hot consumers: `backends/score_buckets.py`, `core/chunked.py`, `backends/ray_backend.py`, `core/pipeline.py` (cluster-ingest), `core/golden.py` (only if it consumes the pair list).
- **Modify** cold consumers (add `pairs_df_to_list()` at boundary): `core/blocker.py` (learned-blocking sample), `tui/engine.py`, `web/preview.py`, `mcp/*`, `core/lineage.py`, `core/report.py`, `core/dashboard.py`, etc. — full list discovered in Wave 4 Task 1.
- **Modify** `scripts/arrow_finish_line_sweep.py` — phase1 bench scale 5M→1M + columnar-only 5M feasibility check.
- **Create** `scripts/check_scored_pairs_list.py` — CI lint banning new hot-path list annotations.
- **Modify** `tests/test_pair_stream_columnar_parity.py` — parity at 1M; add a `@pytest.mark.bench` 5M feasibility test.

**Wave order (dependency-driven):** 1 (scorer contract) → 2 (cluster ingest) → 3 (hot consumers) → 4 (cold consumers) → 5 (gate + bench + lint). Waves 1-2 are the substrate; 3 depends on both.

---

## Wave 1: Uniform DataFrame return from the scorer

`find_fuzzy_matches` has three return branches (NE-penalty, `exclude_pairs`, hot path). Today only the hot path emits a DataFrame (under `_emit_dataframe=True`); the others return lists. Make ALL branches return a `pl.DataFrame` (schema `PAIR_STREAM_SCHEMA`), controlled by `_emit_dataframe` for the deprecation window.

### Task 1.1: `find_fuzzy_matches` — DataFrame from the exclude_pairs + NE branches

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/scorer.py` (the branches ending at `:867` and the NE-penalty branch above it)
- Test: `packages/python/goldenmatch/tests/test_pair_stream_columnar_parity.py`

- [ ] **Step 1: Read the current branches.** Read `scorer.py:780-882` to see all three return paths (NE penalty, `exclude_pairs` at :861-867, hot path at :876-882) and `PAIR_STREAM_SCHEMA`.

- [ ] **Step 2: Write the failing test** (parity: the non-hot branches emit a DataFrame equal to the list, under `_emit_dataframe=True`):

```python
import polars as pl
from goldenmatch.core.scorer import find_fuzzy_matches, pairs_df_to_list
# build a small block_df + mk that exercises exclude_pairs; see existing
# fixtures in this test module for the block_df/mk shape.

def test_exclude_pairs_branch_emits_dataframe(simple_block_df, weighted_mk):
    excl = {(0, 1)}
    as_list = find_fuzzy_matches(simple_block_df, weighted_mk, exclude_pairs=excl)
    as_df = find_fuzzy_matches(simple_block_df, weighted_mk, exclude_pairs=excl, _emit_dataframe=True)
    assert isinstance(as_df, pl.DataFrame)
    assert as_df.columns == ["id_a", "id_b", "score"]
    assert pairs_df_to_list(as_df) == as_list  # byte-identical content
```

- [ ] **Step 3: Run, verify it fails** (the exclude_pairs branch returns a list even with `_emit_dataframe=True`).

- [ ] **Step 4: Implement.** In the `exclude_pairs` and NE branches, when `_emit_dataframe` is True, build the filtered results then return `pl.DataFrame(..., schema=PAIR_STREAM_SCHEMA)` instead of the list. Keep the list return when `_emit_dataframe` is False. Reuse the hot-path construction pattern (numpy arrays → DataFrame) where the data is already arrays; for the filtered (post-exclude) results, construct from the filtered id_a/id_b/score lists via `pl.DataFrame`.

- [ ] **Step 5: Run, verify pass. Commit** (`feat(scorer): DataFrame emit from find_fuzzy_matches exclude/NE branches`).

### Task 1.2: `score_blocks_parallel` / `_score_one_block` — DataFrame accumulation

**Files:**
- Modify: `core/scorer.py` (`_score_one_block:889`, `score_blocks_parallel:952`)
- Test: `tests/test_pair_stream_columnar_parity.py`

- [ ] **Step 1: Read** `_score_one_block` (note the `assert isinstance(pairs, list)` at :913 and the across-files filter) and `score_blocks_parallel` (how it accumulates the per-block returns).
- [ ] **Step 2: Failing test** — `score_blocks_columnar(blocks, mk, matched)` returns a DataFrame whose `pairs_df_to_list` equals `score_blocks_parallel(blocks, mk, matched)`, including the across-files-only filter case.
- [ ] **Step 3: Run, verify fails.**
- [ ] **Step 4: Implement** — thread `_emit_dataframe=True` through `_score_one_block` (remove/replace the `assert isinstance(pairs, list)`; apply the across-files filter via a Polars filter on the frame, OR convert the small per-block frame at the boundary — across-files is per-block so not N-global). `score_blocks_columnar` (already exists at :1341) accumulates via `pl.concat` of per-block frames; verify it does, and that the empty case returns an empty frame with `PAIR_STREAM_SCHEMA`.
- [ ] **Step 5: Run, verify pass. Commit** (`feat(scorer): columnar block accumulation parity with parallel`).

---

## Wave 2: `build_clusters` native DataFrame ingest

### Task 2.1: `build_clusters` accepts `pl.DataFrame`

**Files:**
- Modify: `core/cluster.py:347` (`build_clusters`)
- Test: `tests/test_cluster.py` (or the columnar parity test)

- [ ] **Step 1: Read** `build_clusters:347-420` (how it consumes the pair list today) and `build_clusters_columnar:862` + `_pairs_df_to_list_numpy` (the numpy conversion).
- [ ] **Step 2: Failing test** — `build_clusters(pairs_df)` (DataFrame) produces a cluster dict byte-identical to `build_clusters(pairs_list)` on the same pairs (same members, sizes, confidence, quality). Use a fixture with a few multi-member clusters.
- [ ] **Step 3: Run, verify fails** (build_clusters doesn't accept a DataFrame yet).
- [ ] **Step 4: Implement** — at the top of `build_clusters`, dispatch on type: if `isinstance(pairs, pl.DataFrame)`, convert via `_pairs_df_to_list_numpy` (the numpy path, NOT a Python tuple list) into the arrays the Union-Find/csgraph path needs; else the existing list path. Keep the return shape (`dict[int, dict]`) UNCHANGED — that's Phase 2. The `list[tuple]` branch stays (deprecation window).
- [ ] **Step 5: Run, verify pass. Commit** (`feat(cluster): build_clusters accepts columnar pair DataFrame`).

---

## Wave 3: Migrate hot consumers (DataFrame-native)

**CRITICAL — do NOT flip the shared `all_pairs` accumulator to a DataFrame.**
In `core/pipeline.py` the pair accumulator (`all_pairs: list`) is fed by EVERY
matchkey type via `.extend()` — exact (~:1076), bucket (~:1137), fuzzy
build_blocks (~:1287), probabilistic (~:1363), chunked (~:2016). Exact /
probabilistic going columnar is Phase C, OUT OF SCOPE here. Flipping `all_pairs`
to a DataFrame mid-accumulation would `TypeError` on every un-migrated `.extend()`.
So `all_pairs` STAYS a list in this plan.

Where the columnar feasibility win actually lands (two places, neither is `all_pairs`):
1. **The separate eligible columnar branch** (`pipeline.py:~1261`, gated by
   `_use_columnar`): it already routes `score_blocks_columnar -> build_clusters_columnar`
   end-to-end as DataFrames, bypassing `all_pairs`. Phase 1 makes the CONTRACT it
   uses uniform (Waves 1-2) and adds the parity@1M + feasibility@5M tests on THIS
   path (Wave 5). This is the 5M-no-OOM path. Phase 1 does NOT change when this
   branch engages (still flag/eligibility-gated — Phase B/C widens that).
2. **Inside `score_blocks_columnar`** itself: no per-pair Python list during
   block accumulation (Wave 1.2).

For the mixed/ineligible path, the rule is **convert-at-boundary**: a direct
caller that feeds `all_pairs` calls the scorer then `pairs_df_to_list(...)` once at
its boundary before `.extend()`. `all_pairs` never sees a DataFrame.

**Per-consumer tasks** (one commit each; each with an equivalence test that
clusters/output are byte-identical vs the pre-change list path on a small fixture;
mind the `max_workers=4` RSS guard at `scorer.py:924` — do not raise it):

- [ ] **Task 3.1 `core/pipeline.py` eligible columnar branch (~:1018/:1261).** Read
  `_use_columnar` routing. Ensure the branch consumes the uniform
  `score_blocks_columnar` return (Wave 1) and passes the frame straight to
  `build_clusters`/`build_clusters_columnar` (Wave 2) with NO `pairs_df_to_list`
  round-trip. Equivalence test: eligible config yields identical clusters via this
  branch vs the legacy list branch at small N.
- [ ] **Task 3.2 `core/pipeline.py` mixed/ineligible path.** The non-columnar fuzzy
  path calls `score_blocks_parallel` and `.extend()`s `all_pairs`. Since Wave 1.2
  makes `score_blocks_parallel`/`score_blocks_columnar` DataFrame-canonical, convert
  at the boundary: `all_pairs.extend(pairs_df_to_list(score_blocks_columnar(...)))`
  (or keep calling a list-returning wrapper). `all_pairs` stays a list. Test: a
  mixed exact+fuzzy config still produces identical clusters.
- [ ] **Task 3.3 `backends/score_buckets.py:711`.** NOTE: it calls
  `find_fuzzy_matches(block_df, mk, ...)` INLINE per bucket-partition (no blocks
  list) and `local_pairs.extend(pairs)`. It does NOT use `score_blocks_columnar`.
  Migrate: pass `_emit_dataframe=True`, accumulate per-bucket frames via `pl.concat`,
  and convert once via `pairs_df_to_list` at the point it hands back to `pipeline.py`
  (which feeds `all_pairs`). Equivalence test: bucket backend identical clusters.
- [ ] **Task 3.4 `core/chunked.py:125/367/411`.** Direct `score_blocks_parallel`
  consumers (inline, no `scored_pairs` var). Convert at boundary (the chunked path
  is already its own backend; keep `all_pairs`-equivalent list semantics).
- [ ] **Task 3.5 `backends/ray_backend.py`.** TWO paths: the small-block fallback
  (~:114) delegates to `score_blocks_parallel`; the main path (~:123) calls
  `_score_one_block` per Ray remote task. After Wave 1.2 removes the
  `assert isinstance(pairs, list)`, BOTH the Ray-remote `_score_one_block` call and
  the fallback must handle the return-type change — convert at the collection
  boundary (`pairs_df_to_list`) so the Ray driver still accumulates a list. Test the
  Ray call path, not just the thread-pool path.

**Gate before Wave 4:** Wave 1-2 parity test + each hot consumer's equivalence test green.

---

## Wave 4: Cold consumers (boundary shim)

**Reframed by the Wave 3 correction:** because `all_pairs` STAYS a list and the
pipeline result's `scored_pairs` is therefore still a list, the ~24 DOWNSTREAM
consumers that read `result.scored_pairs` (lineage, report, dashboard, graph,
memory/corrections, identity, web/run, mcp, cli, db/sync, api/server, TUI tabs)
are UNAFFECTED — they keep receiving a list. Wave 4 is mostly a verification, not a
migration. The only cold consumers that need a shim are those that DIRECTLY call
the scorer (now DataFrame-returning).

### Task 4.1: Confirm downstream consumers are unaffected

- [ ] **Step 1:** `rg -n "scored_pairs" packages/python/goldenmatch/goldenmatch` → 32 files. Confirm each is one of: (a) a Wave-3 direct scorer caller (already handled), or (b) a reader of `result.scored_pairs` / a pipeline-produced list. For (b), confirm the value is still a list after Waves 1-3 (it is, because `all_pairs` stays a list). Record the (a)/(b) split in the PR description.
- [ ] **Step 2:** Add ONE guard test: `dedupe_df(eligible_fuzzy_df)` and a mixed exact+fuzzy `dedupe_df` both yield a result whose `scored_pairs` is a `list[tuple]` (the public contract is unchanged for downstream consumers). This is the regression net proving Wave 4 is a no-op for (b).

### Task 4.2: Shim the cold DIRECT-callers only

The only cold sites that call the scorer directly (so receive a DataFrame after Wave 1): `core/blocker.py:790` (learned-blocking sample) and `tui/engine.py:206`.

- [ ] **Step 1: Read** each.
- [ ] **Step 2: Test** — the consumer still produces identical output.
- [ ] **Step 3: Implement** — convert once at the boundary: `pairs = pairs_df_to_list(find_fuzzy_matches(...))` (or `score_blocks_*`). These are sample/interactive (bounded N), so the shim is acceptable per the roadmap rule. Do NOT push the shim into any per-pair loop.
- [ ] **Step 4: Run, verify pass. Commit** (`refactor(blocker,tui): shim columnar scorer return at cold boundary`).
- [ ] **Step 4: Run, verify pass. Commit** (`refactor(<area>): shim columnar pairs at cold boundary`).

Batch related cold consumers into one commit where they share a pattern (e.g. all TUI tabs).

---

## Wave 5: Gate, bench fix, lint

### Task 5.1: Parity at 1M + feasibility at 5M

**Files:** `tests/test_pair_stream_columnar_parity.py`

- [ ] **Step 1:** Extend the parity test to assert byte-identical cluster assignments (Rand index 1.0) at **1M** rows (`realistic_person_df(1_000_000)`), marked `@pytest.mark.bench` (CI bench lane only — NEVER local, per `feedback_avoid_full_suite_oom`).
- [ ] **Step 2:** Add a `@pytest.mark.bench` **feasibility** test: the columnar path (scorer → DataFrame → `build_clusters`) COMPLETES at **5M** without OOM (assert it returns a non-empty cluster dict). No legacy comparison (legacy OOMs at 5M).
- [ ] **Step 3: Commit** (`test(scorer): columnar parity@1M + feasibility@5M (bench lane)`).

### Task 5.2: Bench-scale fix

**Files:** `packages/python/goldenmatch/scripts/arrow_finish_line_sweep.py`

- [ ] **Step 1:** Lower `PHASE_BENCH_SCALE["phase1"]` from 5M to 1M (so the legacy `list` baseline fits and the wall ratio is measurable). Add a unit test asserting the new value.
- [ ] **Step 2:** Add a separate phase1 feasibility metric: run ONLY the columnar path at 5M (via the bench's `--worker 5000000 columnar` subcommand) and record completion as a `bool_true` criterion `columnar_completes_5m`. Update `PHASE_CRITERIA["phase1"]` to the reframed gate (drop the un-measurable 5M wall ratio; keep wall@1M as secondary/non-gating, add `columnar_completes_5m` bool + parity bool). First check whether `tests/test_arrow_finish_line_sweep.py` has phase1-specific criterion assertions (e.g. `test_phase1_criteria_match_spec`) to update; if it only has the registry-coverage test, add a new phase1 assertion rather than editing a nonexistent one.
- [ ] **Step 3: Run** `pytest tests/test_arrow_finish_line_sweep.py -v` green. **Commit** (`fix(sweep): phase1 bench scale 1M + columnar-only 5M feasibility`).

### Task 5.3: CI lint banning new hot-path list annotations

**Files:** Create `packages/python/goldenmatch/scripts/check_scored_pairs_list.py`; wire into CI (mirror `scripts/check_map_elements.py`).

- [ ] **Step 1: Read** `scripts/check_map_elements.py` (the model lint + how it's wired in CI).
- [ ] **Step 2: Failing test** — the lint flags a `scored_pairs: list[tuple[int, int, float]]` (and `list[tuple]`) annotation in a hot module but allows it in cold/boundary modules (allowlist) and allows `pairs_df_to_list` calls.
- [ ] **Step 3: Implement** the lint. SCOPE CAREFULLY: it must flag NEW
  `scored_pairs`/pair-return list annotations only in the scorer's columnar
  functions and the eligible columnar cluster branch — NOT the pipeline's
  `all_pairs: list` (intentionally a list here, Phase C territory) nor the public
  `result.scored_pairs: list` contract (intentionally a list for downstream
  consumers). Use a small allowlist of the hot columnar functions/regions rather
  than a blanket repo grep. Match both `list[tuple[int, int, float]]` and
  `list[tuple]` forms; exit nonzero on a violation. Wire into CI like
  `check_map_elements.py`.
- [ ] **Step 4: Run, verify pass. Commit** (`ci(scorer): lint banning hot-path scored_pairs list annotations`).

---

## Done when

- `find_fuzzy_matches`/`score_blocks_*` return a uniform `pl.DataFrame`; `build_clusters` ingests it natively; the eligible columnar branch flows DataFrame end-to-end (no `all_pairs`); the mixed/ineligible direct callers convert at the boundary (`all_pairs` stays a list); downstream `result.scored_pairs` consumers unaffected (still a list); the 2 cold direct-callers shimmed.
- Parity@1M (Rand 1.0) + feasibility@5M (completes) green on the bench lane.
- Phase1 sweep gate reframed + measurable; CI lint live.
- The full goldenmatch suite passes in CI (run the whole suite in GH Actions, never locally — `feedback_avoid_full_suite_oom`).
- DataFrame is the DEFAULT; legacy list emit still reachable behind the flag for rollback (deletion is release N+1, a separate follow-up).

## Notes / references

- Spec: `docs/superpowers/specs/2026-06-01-arrow-phase1-cutover-design.md`.
- `PAIR_STREAM_SCHEMA`, `pairs_df_to_list` (scorer.py), `_pairs_df_to_list_numpy` (cluster.py) already exist — reuse, don't reinvent.
- `score_blocks_parallel` `max_workers=4` is a deliberate RSS guard (scorer.py:924) — do not raise it.
- Run targeted test files locally; the full suite + 5M/1M bench tests run in CI only.
- @superpowers:test-driven-development for every task.
