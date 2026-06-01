# Arrow Phase 1 cutover: pair stream columnar (design)

**Date:** 2026-06-01
**Status:** design (approved, pre-plan)
**Parent:** `2026-06-01-arrow-native-finish-line-design.md` (the finish-line
roadmap). This is the first phase cutover under that plan. Issue: #623.

## Why now

Stage 0 measured Phase 1 (sweep run 26770594830 + profiler 26759043853):
- At 1M, columnar wall 354.9s vs legacy `list` 564.3s = **0.63 ratio** (CLOSE,
  misses the 0.50 target).
- At **5M the legacy `list` pair-stream OOM'd the 64 GB box** (SIGTERM ~2.5 min).
  The bench could not produce a baseline -- the legacy path is untenable at scale.

The OOM is the result: the legacy pair stream cannot survive 5M on one box, which
is exactly Phase 1's thesis. Phase 1 is also the substrate every later phase
consumes, so it is the correct first cutover.

## Current state (verified 2026-06-01)

The columnar machinery already exists behind opt-in seams:
- `core/scorer.py:618` `find_fuzzy_matches(..., *, _emit_dataframe=False) ->
  list[tuple] | pl.DataFrame`. Emits a `pl.DataFrame` only when
  `_emit_dataframe=True` AND on the hot path (no negative-evidence, no
  `exclude_pairs`, no `pre_scored_pairs`); otherwise returns `list[tuple]`.
- `core/scorer.py:1245` `find_fuzzy_matches_columnar`, `:1341`
  `score_blocks_columnar`, `:1226` `pairs_df_to_list` (the boundary shim).
- `core/cluster.py:347` `build_clusters` (returns `dict[int, dict]`), `:862`
  `build_clusters_columnar`; `_pairs_df_to_list_numpy` converts the pair frame
  without a Python tuple list.
- `core/pipeline.py:139` `_columnar_pipeline_enabled` (`GOLDENMATCH_COLUMNAR_PIPELINE`,
  default OFF) + `:146` `_is_columnar_eligible` (narrow: single weighted matchkey,
  no exact/probabilistic, no `_preflight_report`, no rerank/LLM/boost, default
  backend, columnar-safe scorers).
- 32 files reference `scored_pairs`; ~8 are direct scorer callers
  (`backends/score_buckets.py:711`, `backends/ray_backend.py:116`,
  `core/chunked.py:125/367/411`, `core/blocker.py:790`, `tui/engine.py:206`,
  plus `core/pipeline.py` and `scorer.py` internals).

## Scope (the boundary)

**Scorer/cluster CONTRACT only.** Phase 1 flips `find_fuzzy_matches` /
`score_blocks_*` to DataFrame-canonical, makes `build_clusters` consume the
DataFrame natively, migrates the hot consumers, and retires the legacy list path.

**Out of scope (Phase B/C):** widening the pipeline-level
`GOLDENMATCH_COLUMNAR_PIPELINE` eligibility (auto-config postflight, exact /
probabilistic aggregation, rerank, LLM). The eligibility predicate
(`_is_columnar_eligible`) is UNCHANGED by Phase 1. Phase 1 changes what the
scorer/cluster functions return and who consumes it, not which configs route
through the pipeline fast-path.

## Approach: canonical DataFrame internally + boundary shim at cold consumers

### 1. Uniform DataFrame return

`find_fuzzy_matches` and `score_blocks_*` return a `pl.DataFrame`
`(id_a: i64, id_b: i64, score: f64)` in ALL branches. The non-hot-path branches
(negative-evidence, `exclude_pairs`, `pre_scored_pairs`) that build a Python list
today get converted to a DataFrame at the function boundary, so the return type
is uniform (no `list | DataFrame` union). `_emit_dataframe` collapses (DataFrame
always emitted). The legacy list emit stays reachable for ONE release behind the
existing flag for rollback, then is deleted.

### 2. Consumer split: hot native, cold shim

- **Hot** (touch every pair, scales with row count): bucket / chunked / ray
  scorer accumulation (`pl.concat` of block frames instead of `list.extend`),
  pipeline cluster-ingest, golden. -> DataFrame-native.
- **Cold** (sample, few rows, or serialization; never N-scaling): web preview,
  MCP tools, lineage explain, TUI matches/boost tabs, report, dashboard. -> keep
  ONE `pairs_df_to_list()` call at their boundary. Acceptable per the roadmap's
  "no `.to_pylist()` in any path that runs N times with row count" rule.

The implementation plan tags each of the ~32 `scored_pairs` files hot or cold
explicitly. Design rule: **hot -> native, cold -> shim.**

### 3. build_clusters native ingest

`build_clusters` accepts the `pl.DataFrame` pair stream as a first-class input,
polymorphic on `pl.DataFrame | list[tuple]` during the deprecation window (mirrors
the existing distributed `is_ray_dataset` dispatch). Internally it converts to the
arrays its Union-Find / scipy.csgraph path needs via `_pairs_df_to_list_numpy` --
never a Python list of tuples. This is where the feasibility win lands: no
131M-tuple list ever materializes. The `list[tuple]` branch is removed in N+1.

## Binding gate (feasibility + parity)

Reframed from the original literal kill criterion (wall <= 0.50 / RSS <= 0.25):
the legacy baseline does not run at 5M, so a ratio is unmeasurable there. Cutover
is blocked until, on `realistic_person` / `large-new-64GB`:

- **Feasibility:** the columnar path (scorer -> DataFrame -> `build_clusters`)
  COMPLETES at **5M** -- the scale where legacy OOM'd.
- **Parity:** cluster assignments are byte-identical to the legacy path at **1M**
  (a scale legacy survives); Rand index 1.0 on the same fixture+seed.
- **Secondary (NOT blocking):** wall <= 0.50 of legacy at 1M (currently 0.63),
  tracked and optimized AFTER cutover.

This is a deliberate, evidence-forced deviation from the parent spec's "kill
criteria unchanged" note: you cannot measure a ratio against a baseline that will
not run. Documented here as the Phase 1 exception.

## Bench-scale fix (folded in)

The Stage-0 driver runs phase1's legacy path at 5M and OOMs. Phase 1:
- lowers `PHASE_BENCH_SCALE["phase1"]` to **1M** (legacy fits -> wall ratio
  measurable), and
- adds a separate feasibility check that runs ONLY the columnar path at 5M
  (proves completion without a legacy baseline).

Contained change to `scripts/arrow_finish_line_sweep.py`.

## Deprecation window (one release)

- **Release N:** DataFrame is the default scorer/cluster return; hot consumers
  migrated; cold consumers shimmed; legacy list emit + `list[tuple]`
  `build_clusters` branch reachable behind the existing flag for rollback; a CI
  lint bans new `scored_pairs: list[tuple]` annotations on hot paths.
- **Release N+1:** delete the list emit branch, the `_emit_dataframe` flag, the
  `list[tuple]` `build_clusters` branch, and the flag.

## Testing

- **Parity:** extend `tests/test_pair_stream_columnar_parity.py` to assert
  byte-identical clusters (Rand index 1.0) at 1M, not just fixture scale.
- **Feasibility:** a `@pytest.mark.bench` test (5M, CI bench lane ONLY -- never
  local, per `feedback_avoid_full_suite_oom`) that the columnar path completes.
- **Per-consumer:** each migrated hot consumer gets a test that it produces
  identical clusters/output via the DataFrame path vs the prior list path.
- **Lint:** the CI guard banning new hot-path `scored_pairs: list[tuple]`.

## What this does NOT do

- Change `_is_columnar_eligible` / widen the pipeline fast-path (Phase B/C).
- Touch exact/probabilistic/rerank/LLM scoring paths (they keep the list shim).
- Delete the legacy path in the same release it flips the default (one-release
  window first).

## References

- Parent: `2026-06-01-arrow-native-finish-line-design.md` (+ Stage 0 results).
- Issue #623; columnar machinery PRs #631/#634/#639/#647/#648.
- Stage 0 sweep run 26770594830; profiler run 26759043853.
