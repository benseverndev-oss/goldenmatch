# FS frame-residency Phase 3 — streamed output (free the resident frame)

**Status:** design
**Date:** 2026-08-02
**Owner:** goldenmatch FS out-of-core
**Predecessor:** `docs/superpowers/specs/2026-08-02-fs-frame-residency-bucketed-scoring-design.md` (Phase 1, merged)

## Problem

Phase 1 (`run_fs_dedupe_bucketed`) bounded the **scoring** phase of the FS
out-of-core path: at 50M rows the SCORING loop held steady at ~31–36 GB for
~15 min (vs the spill path's continuous OOM-climb to 62.8 GB), because it never
holds a whole-frame scoring copy — it hash-buckets the frame to disk shards and
scores bucket-by-bucket.

But the **output** phase then OOM'd. `run_fs_dedupe_bucketed` keeps the resident
`base` Arrow frame (~30 GB at 50M) alive through the whole run and hands it to
`_stream_fs_dedupe_output_arrow`, whose core is:

```python
joined = frame.join(asn, keys="__row_id__", join_type="inner").join(
    sizes, keys="__cluster_id__", join_type="inner")   # ~2× frame resident
```

That inner join materializes a second full-width copy of the frame (frame +
joined ≈ 2× frame). Measured at 50M: after scoring held ~31 GB, the output
phase climbed 31 → 63 GB and was OOM-killed. **Output — not the dict-UF, not the
edge list — is the term that gates 50M-on-64GB.** (This is why Phase 2, the
native array-UF, was deprioritized: the bench showed the dict-UF was not the
killer.)

## Goal

Stream the FS dedupe output without ever holding a whole-frame join, and free
the resident prep/base frame before output — dropping the output-phase peak from
~2× frame to ~1× frame-streamed + a compact per-row assignment lookup. Target:
**50M FS dedupe completes on a 64 GB box** where it currently OOMs in output,
**byte-identical** output to the in-memory sequential path.

Non-goal: reducing the LOAD/prep peak below ~1× frame (streaming the input
parquet → prep in bounded batches). That is a separate follow-on
(`2026-07-20-fs-frame-residency-bucket-streaming-design.md` load axis); this
spec addresses only the score→cluster→**output** back-half, which is where the
measured 50M OOM lives.

## Design

Three changes, all inside `backends/fs_out_of_core.py`, behind the existing
`bucketed` route (no new public flag; the route is already gated by
`_fs_streaming_dedupe_eligible` + `GOLDENMATCH_FS_BLOCK_SOURCE=bucketed`).

### (A) Spill `base` once, row-complete, during bucketing

`bucket_frame_to_shards` already streams `base` in `batch_rows` slices to write
the per-pass hash-bucket shards. Those shards are **not** a row-complete copy of
`base`: a row appears in a pass shard only when its block key for that pass is
non-null, and it appears once per pass it survives — so they double-count and
under-cover. They cannot serve as the output source.

Fold a **single row-complete spill** of `base` into the same batch loop: one
extra `RecordBatchFileWriter` (`base_spill.arrow`) that receives every slice
unfiltered, in `__row_id__` order. Cost: one extra full-frame streaming disk
write (compressed Arrow IPC), no extra resident memory (it rides the slice loop
already in flight). Return its path alongside the pass-shard map.

Rationale for spilling rather than re-reading the input parquet: `base` is the
**post-prep** frame (transforms, auto-fix, `__row_id__`, `__xform_*` columns);
the input file has none of that. Re-deriving prep during output would re-run the
whole prep pipeline. A single spill of the already-materialized `base` is the
cheap, faithful source.

### (B) Free the resident frame before output

In `run_fs_dedupe_bucketed`, after bucketing + spilling and after computing the
id set for WCC:

- Capture `all_ids` from `base` (`_prep_all_ids_frame` — min/max/count, returns a
  `range` when contiguous) **before** freeing.
- `del base` / drop the last reference so the ~30 GB frame is reclaimed before the
  output phase allocates.

Scoring (step B) already reads from disk shards, so `base` is not needed there;
its only post-bucketing uses were `_prep_all_ids_frame(base)` and the output
join — both removed here.

### (C) Stream output from the spill against a compact assignment lookup

New `_stream_fs_dedupe_output_from_spill(spill_path, assignments, config,
out_dir)` replaces the resident-frame join. It reads `base_spill.arrow`
batch-by-batch and, per batch, attaches `__cluster_id__` + cluster size, splits
unique/dupes/golden, and appends to the same three `ParquetWriter`s — never
holding the whole frame or a whole-frame join. Semantics are **identical** to
`_stream_fs_dedupe_output_arrow`: unique = singleton clusters, dupes =
multi-member (oversized included), golden = non-oversized multi-member via
`build_golden_records_batch`, `__xform_*` columns excluded from `record_cols`.

**Per-row lookup — the join-mechanism decision.** `assignments` is a compact
`{__row_id__, __cluster_id__}` Arrow table (2× int64, ~800 MB at 50M — the WCC
output, unavoidable and ~40× smaller than the ~30 GB frame). We must attach, per
spill batch, each row's `__cluster_id__` and its cluster's size. Two regimes:

- **Contiguous `__row_id__` (the common pipeline case — dense global row index
  0..N-1).** Build two int arrays once from `assignments`: `cluster_of[row_id]`
  (int64[N]) and `size_of_cluster` keyed by cluster id, then
  `n_of_row[row_id] = size_of_cluster[cluster_of[row_id]]`. For a spill batch
  covering `__row_id__ ∈ [a, b)`, slice `cluster_of[a:b]` / `n_of_row[a:b]` and
  append as columns — O(N) total, one resident int64[N] array (~400 MB at 50M),
  no per-batch hash-table rebuild. `_prep_all_ids_frame` already special-cases
  contiguity, so the same `max-min+1 == count` test selects this fast path.
- **Gapped `__row_id__` (post-quarantine).** Fall back to a per-batch pyarrow
  hash join of the small batch against `assignments` + `sizes`. Building the
  N-row probe side once and reusing it is not exposed by pyarrow, but the gapped
  case is the rare exception and correctness — not the peak — is what matters
  there; the resident cost is still bounded by `assignments` (~800 MB), not 2×
  frame.

Both regimes produce a per-batch table equivalent to a slice of the Phase-1
`joined`, so the downstream filter/select/write logic is shared with
`_stream_fs_dedupe_output_arrow` (extract the common tail into a helper).

Golden is accumulated across batches: each batch's non-oversized multi-member
subset is collected; `build_golden_records_batch` runs on the bounded union at
the end (one golden per multi-member cluster — a cluster's members can straddle
batches, so golden CANNOT be finalized per batch). The golden subset is bounded
(multi-member, non-oversized) and small relative to the frame, so accumulating
it resident is safe — the same subset `_stream_fs_dedupe_output_arrow` already
built in one shot.

## Correctness / parity

- **Byte-identical to the sequential in-memory path.** The streamed output must
  produce the same unique/dupes/golden parquet content (row sets + values) as
  `_stream_fs_dedupe_output_arrow` over the same `assignments`. The only change
  is WHERE the frame rows are read from (disk spill vs resident) and HOW
  cluster_id/size are attached (array lookup vs join) — the semantics are
  unchanged. Order-within-file may differ from the resident join (streaming in
  `__row_id__` order vs join's hash order); parity assertions must compare row
  SETS, not row order (sort or set-compare), matching how the Phase-1 parity
  tests already treat pipeline non-determinism.
- **Singletons preserved.** Every `__row_id__` in `all_ids` (including rows in no
  pair) is in `assignments` as its own singleton cluster (the WCC folds them),
  so the contiguous `cluster_of` array is fully populated — no row silently
  dropped, matching the resident inner-join contract that `_prep_all_ids`
  documents.
- **`__xform_*` exclusion + golden semantics** carried verbatim from
  `_stream_fs_dedupe_output_arrow`.

## Gates

- **Parity test** (`tests/test_fs_out_of_core.py`): the bucketed streamed-output
  result equals the sequential in-memory result on a small person frame — extend
  the existing `test_dedupe_to_parquet_bucketed_parity_with_in_memory` /
  `test_bucketed_and_sequential_orchestrators_agree` to cover the streamed-output
  path (unique/dupes/golden row sets + counts). Add a gapped-`__row_id__` case
  (filtered prep) to exercise the fallback.
- **Contiguous fast-path unit test**: `cluster_of` / `n_of_row` array build +
  batch-slice attach equals a reference join on a tiny fixture.
- **Scale gate** (`bench-fs-out-of-core.yml`, bucketed mode at 50M): completes
  under 64 GB with a measured output-phase peak below the ~63 GB Phase-1 OOM —
  the binding proof. Uses the edge-bearing stable-anchor blocking from the bench
  fix (PR #2367) so the 50M run scores a real edge load.

## Rollout

- Behind the existing `bucketed` route; no new public flag. Default OFF (the
  route is opt-in via `GOLDENMATCH_FS_BLOCK_SOURCE=bucketed`).
- `_stream_fs_dedupe_output_arrow` (resident) is retained for the sequential /
  spill routes, which keep the resident frame anyway; only `run_fs_dedupe_bucketed`
  switches to the streamed variant.
- No CLAUDE.md flag row (no new env var); update the FS out-of-core CLAUDE.md
  bullet to record that the bucketed route now streams output + frees the frame.
