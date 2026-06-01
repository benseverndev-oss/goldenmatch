# Multi-pass blocking for the bucket backend (design)

**Date:** 2026-06-01
**Status:** design (approved, pre-plan)
**Decision context:** Discovered while landing bucket-native-as-default (PR #667,
spec `2026-06-01-bucket-native-default-backend-design.md`). Making `goldenmatch-native`
a default dependency flips `_scoring_backend()` to `"bucket"` for the typical
user. CI caught a Febrl3 F1 regression (0.9332 -> 0.8483). Root cause is NOT the
native kernel -- it is the bucket backend's single-pass blocking limitation.

## Problem

`backends/score_buckets.py::score_buckets` builds its bucket key from
`blocking_config.keys[0]` only (line ~364; the docstring at ~335 states
"multi-key blocking is not supported in bucket mode v1"). Auto-config emits
**multi_pass** blocking for person-shaped data -- Febrl3 gets 6 passes
(`state+given_name`, `state+given_name` substring, `given_name` soundex,
`surname` soundex, `surname` substring, `date_of_birth`). polars-direct's
`build_blocks` unions candidate pairs across all passes; bucket applies only
`keys[0]` and silently drops every pass. (Note: for multi_pass, auto-config sets
`keys[0]` to a DISTINCT primary key that is not even `passes[0]` -- e.g.
`autoconfig.py:1493` makes `keys[0]` a soundex key while `passes[0]` at `:1496`
is a substring key. So bucket today runs a single key that isn't in the pass
list at all.)

**Reproduced deterministically (Febrl3, 5000 rows, local):**

| Backend                          | F1     | Precision | Recall | candidate pairs |
| -------------------------------- | ------ | --------- | ------ | --------------- |
| polars-direct                    | 0.9332 | 0.9458    | 0.9209 | thousands       |
| bucket (native on OR off, equal) | 0.8483 | 1.0000    | 0.7366 | ~460            |

bucket native-on and native-off are byte-identical, so the native kernel is
exonerated -- this is purely the bucket candidate-generation. Every "proven"
bucket scale run (5M/25M) used single-key blocking (`block_key=last_name`),
where bucket == polars, which is why the gap was latent until native-by-default
made bucket the typical user's path on multi_pass auto-config.

## Goal

Make the bucket backend honor ALL blocking passes so it reaches F1 parity with
polars-direct on multi_pass configs, WITHOUT regressing the proven single-key
5M/25M scale path (wall + peak RSS).

## Decision: success bar (approved)

**Parity is enough.** Once fixed bucket hits F1 parity with polars-direct on
multi_pass, it stays the default even if wall-clock is only on par (not clearly
faster) at 200k-750k -- betting on bucket's memory/RSS + scale-path consistency
(its real value; at 200-750k polars-direct fits RAM fine anyway). Speed is a
"don't regress badly" sanity check, NOT a hard gate. (Contrast: the original
bucket-native bench was a hard speed gate; this one is not.)

## Approach (approved): loop passes inside `score_buckets`

Extract the current per-key body of `score_buckets` (build `key_expr` -> bucket
assign -> partition_by -> score blocks via the native/python fast path -> emit
pairs) into an inner helper `_score_single_pass(slim_df, key, fast_path,
frozen_exclude, native_exclude_handle, n_buckets, across_files_only,
source_lookup, target_ids) -> list[tuple]`. The fast-path resolution and the
exclude set/handle are computed ONCE by the caller and passed in (hoisted), so
the helper is pure per-key scoring with no shared-state mutation.

`score_buckets` then:
1. Resolves `pass_keys = blocking_config.passes or blocking_config.keys`.
   (`passes` is `None` for static/single-key configs -> falls back to `keys`;
   non-empty for multi_pass -> wins.)
2. Computes the slim projection ONCE, keeping `__row_id__`, `__source__`,
   `__xform_*__`, and the union of source fields across ALL `pass_keys` (today
   the keep-set scans only `keys` at `:390`).
3. Freezes the exclude set ONCE (the existing `frozen_exclude` /
   `native_exclude_handle` build at `:464`/`:521`) BEFORE the pass loop, and
   resolves the matchkey fast-path ONCE (`_resolve_fast_path` is key-independent).
4. For each key in `pass_keys`, calls `_score_single_pass(slim_df, key, ...,
   frozen_exclude, native_exclude_handle, ...)` and extends the pair list.
5. Returns the accumulated pairs; `matched_pairs` is updated once at the end
   (union of exact pairs + all fuzzy pairs found) for downstream consumers.

**Cross-pass dedup = mirror polars-direct exactly (do NOT add a per-pass skip).**
`score_blocks_parallel` (polars) freezes its exclude set ONCE across the whole
FLATTENED multi-pass block list (`scorer.py:1067`) and EMITS duplicate pairs for
any pair that surfaces in more than one pass; the duplicates collapse downstream
in `build_clusters`, whose `pair_scores` dict assigns per canonical `(min,max)`
pair (`cluster.py:471`). bucket adopts the identical algorithm: one frozen
exclude across all passes, each pass emits independently (cross-pass duplicates
included), downstream collapses them. This is parity-correct BY CONSTRUCTION --
same algorithm as polars, no subtle per-pass exclude-rebuild to get wrong.

Because a pair's score is a pass-invariant function of `(row_a fields, row_b
fields, matchkey)` -- the blocking key only decides candidacy, not the score --
a pair scored in pass 1 and pass 3 gets the SAME value both times. So the
downstream collapse (whichever duplicate "wins") yields identical scores, and
bucket and polars agree on the pair set AND the per-pair score.

**Implementation guard (the review's #1 silent-break risk):** the exclude set
and native handle are frozen ONCE and HOISTED above the pass loop ON PURPOSE
(this is what matches polars). Do NOT rebuild them per pass and do NOT add an
intra-loop `matched_pairs` skip -- that would DIVERGE from polars (bucket would
keep first-surfacing where polars keeps the downstream-collapsed duplicate),
which is exactly the semantics mismatch to avoid.

Both call sites (`core/pipeline.py:1128` bucket sync, `:2349` distributed) are
unchanged -- they already pass the whole `blocking_config`.

### Rejected alternatives
- **Loop at the caller (pipeline):** duplicates the per-pass loop + union across
  2 call sites and widens the `score_buckets` signature. More blast radius, no
  benefit.
- **Reuse `build_blocks` for bucket:** defeats bucket's entire reason to exist
  (skipping per-block LazyFrame materialization that hung 7 consecutive 5M runs
  at 62.99 GB RSS).

## Data flow

```
prepared_df (eager, all cols)
  -> slim ONCE: keep __row_id__/__source__/__xform_*__ + union(fields over pass_keys)
  -> freeze exclude ONCE (frozen_exclude + native_exclude_handle); resolve fast-path ONCE
  -> for key in pass_keys:
       slim_df.with_columns(key_expr(key))    # per-pass bucket key
       -> hash % n_buckets -> partition_by(bucket)
       -> score blocks (native fast path if resolved, else python), excluding frozen_exclude
       -> emit (id_a,id_b,score)              # cross-pass duplicates allowed (mirrors polars)
       -> accumulate
  -> matched_pairs |= {emitted}; return accumulated pairs
       # build_clusters collapses duplicate canonical pairs downstream (cluster.py:471)
```

## Edge cases / invariants

- **Single-pass config (static / one key):** `pass_keys = [keys[0]]` -> exactly
  one iteration -> byte-identical to today. This is the load-bearing guard for
  the proven 5M/25M single-key runs: NO behavior or perf change on that path.
- **Peak RSS:** one pass's buckets are materialized at a time (loop, not
  concat-all-passes), so peak RSS stays at the single-pass level. Slim is done
  once. The RSS win (bucket's real value) is preserved.
- **Oversized blocks (`skip_oversized`, `max_block_size`):** applied per pass by
  the reused inner helper, unchanged.
- **Match mode (`across_files_only`, `target_ids`, `source_lookup`):** applied
  per pass; the union remains correct (cross-file filter is per-pair).
- **A pass field absent from `prepared_df`:** defensively skip that pass (log);
  auto-config should not emit it, but bucket must not crash where polars would
  have produced blocks.
- **Telemetry shape:** the per-pass loop emits the bucket `record_metrics`
  (`:755`) once per pass (N emissions) vs polars' single blocking profile. This
  is a reporting-shape difference, not a correctness issue; bench/validation
  readers should expect per-pass metric lines.

## Cost

N passes ~= N x bucket-scoring work. This is inherent to multi_pass blocking --
polars-direct pays the same N-pass cost. bucket's remaining edge over polars is
native per-pass scoring + no per-block LazyFrame. Per the success-bar decision,
wall is a sanity check, not a gate.

Two parity-faithful overheads (both mirror what polars already does, so they do
not widen the bucket-vs-polars gap): (1) cross-pass DUPLICATE candidate pairs are
emitted into `all_pairs` and collapse downstream -- inflates `fuzzy_pair_count`
identically to polars, comparable between backends; (2) unlike polars'
`_build_multi_pass_blocks` (which dedups BLOCKS by `block_key` string across
passes, `blocker.py:640`), bucket has no block-level dedup, so two passes that
produce the same `__block_key__` value re-score that block. Output is still
correct (pair-level collapse downstream); it is strictly a small amount of extra
scoring on overlapping-key passes. Not worth block-level dedup machinery for v1.

## Testing

- **Unit (parity):** a 2-pass blocking config on a small synthetic df where
  pass 2 surfaces a true pair that pass 1's key cannot block together. Assert
  `score_buckets` returns the same set of canonical `(min,max)` pairs as the
  polars-direct path (`build_blocks` + `score_blocks_parallel`) for the same
  config. Compare on the deduplicated pair SET (canonicalize + dedup both sides
  first, since both backends emit cross-pass duplicates); the per-pair score is
  pass-invariant so a `max`-per-pair comparison also holds, but the pair set is
  the primary assertion.
- **Unit (regression lock):** a single-pass (static) config -> `score_buckets`
  output identical to the pre-change behavior (same pairs, same scores). Locks
  the 5M/25M single-key path.
- **Integration (headline):** Febrl3 via the existing `dqbench_adapters.febrl3`
  path with `backend="bucket"` reaches F1 parity with polars-direct (target:
  ~0.93, within noise of the 0.9332 polars number; floor >= 0.90 to match the
  CI smoke gate). Recordlinkage is an optional dep -> skip when absent.
- **Fix the 7 currently-red tests CORRECTLY (not by reverting the feature):**
  - `test_planner_integration.py` (simple/fast_box/postflight) and
    `test_autoconfig_planner_protocol.py` assert `backend == "polars-direct"`
    for small dfs. Once bucket is parity-correct, bucket IS the right default
    when native is importable. Update these to be native-aware: assert
    `plan.backend == _scoring_backend(...)` (or pin `GOLDENMATCH_NATIVE=0` where
    the test specifically verifies the polars-direct fallback string).
  - `test_partitioned_block_scoring_pipeline.py` (2 tests) and
    `test_prepared_record_store_pipeline.py` (1 test) exercise orthogonal
    pipeline features; the backend flip diverted them off their code path. Pin
    `GOLDENMATCH_NATIVE=0` (or `GOLDENMATCH_PLANNER_BUCKET=0`) so they exercise
    the polars-direct path they were written for.
- **Re-bench (sanity, not a gate):** re-run `bench-fs-stages` at 200/500/750k
  with the fix; record F1 parity + wall + (if available) RSS. Confirm wall is
  not dramatically worse than polars-direct. Fold the numbers into the
  bucket-native-default spec's validation section.

## Scope boundary (YAGNI)

- ONLY fix bucket multi-pass candidate generation in `score_buckets.py` (+ the
  slim keep-set). Do NOT touch the native kernel, polars-direct, or the planner
  rule logic (Task 1's `rule_bucket_suggested` stays; it just becomes safe).
- Do NOT add new blocking strategies (canopy/sorted-neighborhood for bucket are
  out of scope; only the `passes`/`keys` list that auto-config already emits).

## References

- Root cause: `backends/score_buckets.py:335,364`. Bucket dispatch:
  `core/pipeline.py:1126-1139`, `:2348`. polars-direct multi-pass reference:
  `core/blocker.py::build_blocks` + `core/scorer.py::score_blocks_parallel`.
- Parent feature: `docs/superpowers/specs/2026-06-01-bucket-native-default-backend-design.md`
  (PR #667). Related memory: bucket native scoring win; goldenmatch-native
  package; arrow-native finish line.
- Bench tool: `scripts/bench_fs_and_stages.py` + `.github/workflows/bench-fs-stages.yml`.
