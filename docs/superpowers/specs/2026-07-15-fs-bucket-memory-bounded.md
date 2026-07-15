# Memory-bounded + fast Fellegi-Sunter scoring via a batched native bucket worker

**Issue:** #1792 — a probabilistic (FS) matchkey has no config that is both memory-bounded and fast at scale. Default/native-FS OOMs (materializes the full candidate-pair set); `backend="bucket"` is memory-safe but slow (its per-bucket worker scores FS **per block**, no native batching — the weighted path batches a whole bucket into one native kernel call).

**Goal:** give an FS matchkey the same "partition-and-bound frames + one native kernel call per bucket" treatment the weighted fast path has. Route FS to the bucket path by default when native FS is available. Byte-parity vs the existing per-block FS output, gated.

## Architecture (verified 2026-07-15)

- `backends/score_buckets.py::score_buckets` bounds *frame* memory: two-level hash partition, one pass resident, zero-copy per-block `slice()`. But FS rides the **slow** worker `_score_one_bucket` (l.~745), which loops blocks calling `prob_scorer(block_df, frozen_exclude)` one block at a time (no native batching).
- The **weighted** fast worker `_score_one_bucket_fast` (l.~587) is the template: it seam-sorts the bucket by `__block_key__`, gets `size_list = sorted_frame.run_lengths("__block_key__")` (the per-block sizes over the sorted bucket), builds a `keep` mask (`s>=2 and not (skip_oversized and s>max_block_size)`), filters the per-row arrays + `size_list` to `kept_size_list`, and makes **one** native Arrow kernel call `score_block_pairs_arrow(..., size_list, ...)` over the whole block-sorted bucket. The kernel only compares WITHIN each contiguous block (delimited by the sizes list).
- The native FS kernel already accepts a block-**sizes** list: `probabilistic.py::score_probabilistic_native` (l.~1600) calls `native_module().score_block_pairs_fs(row_ids, [n], field_values, scorer_ids, levels, partials, weights, calibrated, prior_w, min_weight, weight_range, link_threshold, excl)`. Today it's only ever called with a single-element `[n]` (one block). Its per-block prep helper is `_field_values_for_block`. Eligibility: `_fs_native_enabled()` (needs `GOLDENMATCH_FS_NATIVE` truthy AND `native_enabled("block_scoring")`) + `_fs_native_eligible(mk)` (scorers ⊆ `_NATIVE_FS_SCORER_IDS`, no `tf_adjustment`, kernel has `score_block_pairs_fs`).
- Pipeline routing: `core/pipeline.py::_run_dedupe_pipeline` probabilistic branch (`if mk.type == "probabilistic"`, ~l.1430) does `score_buckets(...)` only when `config.backend == "bucket"` (~l.1460), else `score_probabilistic_blocks_parallel(...)` (the OOM path). `from_splink` produces an EXPLICIT config with `backend=None` → the OOM path. Same pattern in `_run_match_pipeline`.

## Deliverables

### 1. Batched native FS scorer — `probabilistic.py`

Add `score_probabilistic_bucket_native(sorted_bucket_df, size_list, mk, em_result, exclude_pairs) -> list[tuple[int,int,float]]`:
- Precondition: `sorted_bucket_df` is already sorted by `__block_key__`; `size_list` is the run-length list of block sizes over it (blocks are contiguous). Caller guarantees `_fs_native_eligible(mk)` and `_fs_native_enabled()`.
- Build `row_ids` (all rows' `__row_id__`, int64) and per-field `field_values` over the WHOLE sorted bucket in row order (generalize `_field_values_for_block` to the full frame — the kernel slices per block using `size_list`), plus `scorer_ids/levels/partials/weights/calibrated/prior_w/min_weight/weight_range/link_threshold` exactly as `score_probabilistic_native` builds them (factor the shared prep out of `score_probabilistic_native` so BOTH call it — DRY; single-block is just `size_list=[n]`).
- One call: `native_module().score_block_pairs_fs(row_ids, size_list, field_values, ...)`. Round scores to 4dp identically.
- **Byte-parity requirement:** for the same `sorted_bucket_df` + `size_list`, output MUST equal concatenating `score_probabilistic_native(block_df, mk, em, exclude)` over each block slice (same block order, same rounding). The kernel already isolates blocks by the sizes list, so this should hold by construction — assert it in a test.

### 2. Wire it into the bucket worker — `score_buckets.py::_score_one_bucket`

When `is_probabilistic` AND FS-native-eligible (`_fs_native_enabled()` + `_fs_native_eligible(mk)`), replace the per-block `prob_scorer` loop with: seam-sort the bucket by `__block_key__` (reuse the `_score_one_bucket_fast` sort + `run_lengths` pattern), apply the SAME `keep` mask (size<2 + `skip_oversized and s>max_block_size` — and honor the #372/#1790 auto-split behavior: if the fast worker auto-splits oversized blocks, mirror that; otherwise the size<2/oversized skip is the parity contract), then one `score_probabilistic_bucket_native(...)` call. Apply the existing `across_files_only`/`target_ids` post-filters to the returned pairs exactly as the per-block path does. Fall back to the per-block `prob_scorer` loop when NOT FS-native-eligible (vectorized numpy / model-backed / `tf_adjustment` / `GOLDENMATCH_FS_NATIVE` off) — byte-identical to today.
- Gate the batched path so it can be disabled: `GOLDENMATCH_FS_BUCKET_NATIVE=0` forces the per-block loop (parity escape hatch), default ON when eligible.

### 3. Default-route FS to the bucket path — `pipeline.py`

In `_run_dedupe_pipeline` AND `_run_match_pipeline` probabilistic branches: route to `score_buckets(...)` when `config.backend == "bucket"` OR (`config.backend` is None/unset AND `native_enabled("block_scoring")` AND `_fs_native_eligible(mk)`). Keep `score_probabilistic_blocks_parallel` as the fallback for the non-native / explicitly-non-bucket case. Do NOT change behavior when `config.backend` is an explicit non-bucket value (`polars-direct`, `ray`, `duckdb`, ...). Add `GOLDENMATCH_FS_DEFAULT_BUCKET=0` to force the legacy default route (escape hatch).

## Tests (byte-parity is the gate)

- `tests/test_fs_bucket_native.py`:
  - `score_probabilistic_bucket_native` over a multi-block sorted bucket == concat of per-block `score_probabilistic_native` (same fixture, fixed `em_result`, sorted pairs equal). Skip if `not _fs_native_enabled()` (native kernel absent).
  - `_score_one_bucket` FS-native path emits the SAME pair set (canonical `(min,max)`) as the per-block `prob_scorer` loop on a synthetic person frame with several blocks incl. an oversized one.
  - Routing: an FS-matchkey config with `backend=None` runs through `score_buckets` when native FS is on (assert via a spy/log or by the bucket-only metric), and through the parallel path when `GOLDENMATCH_FS_DEFAULT_BUCKET=0`.
  - `GOLDENMATCH_FS_BUCKET_NATIVE=0` reproduces the per-block output byte-for-byte.
- Existing FS tests must stay green: `test_probabilistic*.py`, `test_fs_*.py`, `test_score_buckets*.py`, `test_native_parity.py`.

## Non-goals (call out, don't do)

- Zero-copy Arrow marshaling of FS `field_values` (kernel still takes Python lists) — a follow-on perf lever, not needed for memory-bounding/correctness.
- Bounding the emitted-pair accumulation itself / spill — the bucket path already bounds *frames*; emitted pairs are O(matches) which tight FS blocking keeps small. Note it as future work.
- Avoiding `build_blocks` (still built for EM even on bucket) — EM trains on a sample; leave as-is.

## Validation (after implement + local parity)

Build native in the worktree (`uv run python scripts/build_native.py`), run parity tests with `GOLDENMATCH_FS_NATIVE=1 GOLDENMATCH_NATIVE=1 POLARS_SKIP_CPU_CHECK=1`. Then the scale proof runs on the 64GB runner (the person-1M FS lane that OOM'd should complete, bounded + fast) — done by the controller, not the subagent.
