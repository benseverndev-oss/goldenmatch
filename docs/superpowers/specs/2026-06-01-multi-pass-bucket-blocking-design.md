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
`build_blocks` unions candidate pairs across all passes; bucket applies pass 1
and silently drops the other 5.

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
pairs) into an inner helper `_score_single_pass(slim_df, key, mk, matched_pairs,
n_buckets, across_files_only, source_lookup, target_ids) -> list[tuple]`.

`score_buckets` then:
1. Resolves `pass_keys = blocking_config.passes or blocking_config.keys`.
2. Computes the slim projection ONCE, keeping `__row_id__`, `__source__`,
   `__xform_*__`, and the union of source fields across ALL `pass_keys` (today
   the keep-set scans only `keys`).
3. For each key in `pass_keys`, calls `_score_single_pass` against the one
   slim_df and extends the pair list.
4. Returns the accumulated pairs.

Cross-pass dedup is already handled by `matched_pairs`: it is mutated in-place as
pairs are emitted, and `_score_single_pass` skips pairs already present -- the
same mechanism `score_blocks_parallel` (polars-direct) uses, so a pair surfaced
by pass 1 is not re-emitted by pass 3, with identical (first-surfacing) score
semantics. (Implementation must VERIFY the current emit path actually consults
`matched_pairs` before emitting; if it only adds without checking, add the check
so cross-pass union is correct.)

Both call sites (`core/pipeline.py:1128` bucket sync, `:2348`) are unchanged --
they already pass the whole `blocking_config`.

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
  -> for key in pass_keys:
       slim_df.with_columns(key_expr(key))    # per-pass bucket key
       -> hash % n_buckets -> partition_by(bucket)
       -> score blocks (native fast path if resolved, else python)
       -> emit (id_a,id_b,score) for pairs not already in matched_pairs
       -> accumulate
  -> return accumulated pairs
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

## Cost

N passes ~= N x bucket-scoring work. This is inherent to multi_pass blocking --
polars-direct pays the same N-pass cost. bucket's remaining edge over polars is
native per-pass scoring + no per-block LazyFrame. Per the success-bar decision,
wall is a sanity check, not a gate.

## Testing

- **Unit (parity):** a 2-pass blocking config on a small synthetic df where
  pass 2 surfaces a true pair that pass 1's key cannot block together. Assert
  `score_buckets` returns the SAME canonical pair set as the polars-direct path
  (`build_blocks` + `score_blocks_parallel`) for the same config.
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
