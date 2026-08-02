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

Stream the FS dedupe output **without ever holding a whole-frame join** —
dropping the output-phase peak from ~2× frame to ~1× frame + a compact per-row
assignment lookup. Target: **50M FS dedupe completes on a 64 GB box** where it
currently OOMs in output, **byte-identical** output to the in-memory sequential
path.

**The load-bearing win is join-elimination, not frame-freeing.** A spec-review
pass established two facts that reshape this design:

1. `_stream_fs_dedupe_output_arrow`'s second full-width copy is the
   `frame.join(asn).join(sizes)` materialization (`joined`). Removing that
   `joined` copy drops the output peak 2×→1×, and 1× frame (~31 GB, the scoring
   plateau) already clears 64 GB. So **join-elimination alone hits the 50M
   goal.**
2. Freeing the resident frame does NOT happen via a `del base` local to
   `run_fs_dedupe_bucketed`: `base = _fw.to_arrow()` is largely zero-copy over
   the caller's polars `collected_df`, and the pipeline holds that frame live
   (`collected_df` / `combined_lf` in `_run_dedupe_pipeline`, `score_frame` /
   `_em_src` in `_run_fs_streaming_dedupe`) across the whole output call. A real
   below-1×-frame drop needs caller-side reference drops in `core/pipeline.py`,
   which is a separate, riskier change touching the shared pipeline — and is NOT
   required to complete 50M-on-64GB.

So this spec's scope is **Phase 3a: eliminate the output join** by streaming from
the *resident* `base` in batches. No disk spill, no `del base`. Freeing the
resident frame to push the peak *below* 1× frame is **Phase 3b** — a documented
caller-side follow-on, built only if a measured peak shows it's needed.

Non-goal: reducing the LOAD/prep peak below ~1× frame (streaming the input
parquet → prep in bounded batches). Separate follow-on
(`2026-07-20-fs-frame-residency-bucket-streaming-design.md` load axis).

## Design (Phase 3a — join-elimination)

One change, inside `backends/fs_out_of_core.py`, behind the existing `bucketed`
route (no new public flag; gated by `_fs_streaming_dedupe_eligible` +
`GOLDENMATCH_FS_BLOCK_SOURCE=bucketed`). No disk spill, no `bucket_frame_to_shards`
change, no `del base`.

New `_stream_fs_dedupe_output_batched(frame, assignments, config, out_dir)`
replaces the `frame.join(asn).join(sizes)` in the output path. It reads the
resident `frame` **in `__row_id__`-ordered slices** and, per batch, attaches
`__cluster_id__` + cluster size via a `.take` gather (below), splits
unique/dupes/golden, and writes each batch to per-file `pq.ParquetWriter`s —
never building the whole-frame `joined`. Peak = 1× resident `frame` + the
compact lookup arrays + one batch + the (bounded) golden subset. Semantics are
**identical** to `_stream_fs_dedupe_output_arrow`: unique = singleton clusters,
dupes = multi-member (oversized included), golden = non-oversized multi-member
via `build_golden_records_batch`, `__xform_*` columns excluded from
`record_cols`, and the same return-dict keys.

**Per-row lookup — the attach mechanism.** `assignments` is a compact
`{__row_id__, __cluster_id__}` Arrow table (2× int64, ~800 MB at 50M — the WCC
output, unavoidable and ~40× smaller than the ~30 GB frame). `external_wcc_from_shards`
emits it by iterating `all_ids` (so it covers **every** row incl. singletons),
but the row order is `all_ids` order — row_id-sorted only in the contiguous
case, and starting at `lo` (not necessarily 0). Two regimes:

- **Contiguous `__row_id__` (the common pipeline case — dense global index
  `{lo..hi}`, `hi-lo+1 == n`; the same test `_prep_all_ids_frame` uses).** Build
  two int arrays once by **scatter** (NOT a direct slice — `assignments` is not
  guaranteed row_id-position-aligned):
  `cluster_of[asn.__row_id__ - lo] = asn.__cluster_id__` (int64, sized `n`,
  ~400 MB at 50M) and a per-cluster `size_of[cluster_id]`, then
  `n_of[row_id-lo] = size_of[cluster_of[row_id-lo]]`. Per batch, gather with
  `cluster_of.take(batch["__row_id__"] - lo)` / `n_of.take(...)` — robust to any
  batch layout, no reliance on IPC/slice-position alignment.
- **Gapped `__row_id__` (post-quarantine).** Fall back to a per-batch pyarrow
  hash join of the (small) batch against `assignments` + a `sizes` table.
  Correctness — not peak — is what matters in this rare case; the resident cost
  is still bounded by `assignments` (~800 MB), not 2× frame.

**Write path + counts.** Unlike `_stream_fs_dedupe_output_arrow` (one-shot
`write_table` + `num_rows` counts), the batched variant opens each of
`unique.parquet` / `dupes.parquet` with a `pq.ParquetWriter` **once at a fixed
schema** and calls `write_table` per batch, accumulating `unique_count` /
`dupes_count` as running sums. The **shareable tail** with the resident function
is only the filter/select logic (`n==1` → unique; `n>1` → dupes+`__cluster_id__`;
`2≤n≤max_cluster_size` → golden subset) — extract that; the write+count path is
new machinery.

**Golden** is accumulated across batches (each batch's non-oversized multi-member
subset collected), then built **once** at the end via
`build_golden_records_batch(pl.from_arrow(union), rules)` — a cluster's members
can straddle batches, so golden CANNOT be finalized per batch. The accumulated
subset is bounded (multi-member, non-oversized), the same subset the resident
function already held; the single terminal `pl.from_arrow` pulls polars in
exactly as today. Preserve the resident function's empty-golden semantics:
**unlink a stale `golden.parquet`** and return `golden_path=None` when
`golden_count == 0`.

## Correctness / parity

- **Byte-identical to the sequential in-memory path.** The batched output must
  produce the same unique/dupes/golden parquet content (row sets + values) as
  `_stream_fs_dedupe_output_arrow` over the same `assignments`. The only change
  is HOW cluster_id/size are attached (scatter-array `.take` vs inner join) and
  that rows are written per batch rather than one-shot — the row-membership
  semantics are unchanged. Order-within-file differs (streaming in `__row_id__`
  order vs join's hash order); parity assertions compare row SETS, not order —
  the existing bucketed tests already do this via `_partition_set_from_parquet`
  (sorted tuples per `__cluster_id__`) + count equality.
- **Singletons preserved.** `external_wcc_from_shards` iterates `all_ids`, so
  every `__row_id__` (incl. rows in no pair) is in `assignments` as its own
  singleton cluster; the scattered `cluster_of` array is fully populated — no row
  silently dropped, matching the resident inner-join contract.
- **`__xform_*` exclusion + golden semantics** (incl. the stale-`golden.parquet`
  unlink and `golden_path=None` on empty) carried verbatim.

## Gates

- **Parity test** (`tests/test_fs_out_of_core.py`): the bucketed batched-output
  result equals the sequential in-memory result on a small person frame — extend
  `test_dedupe_to_parquet_bucketed_parity_with_in_memory` /
  `test_bucketed_and_sequential_orchestrators_agree` (they compare row sets via
  `_partition_set_from_parquet` + counts). Add a gapped-`__row_id__` case
  (filtered prep, non-contiguous ids) to exercise the hash-join fallback.
- **Contiguous fast-path unit test**: the scatter build of `cluster_of` / `n_of`
  + a per-batch `.take` attach equals a reference `frame.join(assignments)` on a
  tiny fixture (locks the scatter-not-slice + `lo`-offset correctness).
- **Scale gate** (`bench-fs-out-of-core.yml`, bucketed mode at 50M): completes
  under 64 GB with a measured output-phase peak at ~1× frame (well below the
  ~63 GB Phase-1 OOM) — the binding proof. Uses the edge-bearing stable-anchor
  blocking from PR #2367 so the 50M run scores a real edge load.

## Rollout

- Behind the existing `bucketed` route; no new public flag. Default OFF (opt-in
  via `GOLDENMATCH_FS_BLOCK_SOURCE=bucketed`).
- `_stream_fs_dedupe_output_arrow` (resident join) is retained for the
  sequential / spill routes; only `run_fs_dedupe_bucketed` switches to the
  batched variant. (Extending the batched variant to those routes is a trivial
  follow-on once proven.)
- No CLAUDE.md flag row (no new env var); update the FS out-of-core CLAUDE.md
  bullet to record that the bucketed route now writes output batched (join-free).

## Phase 3b (deferred) — free the resident frame

To push the output peak *below* 1× frame, the caller must drop its live frame
references around the streamed-output call: `_run_fs_streaming_dedupe` drops
`score_frame` / `_em_src`, and `_run_dedupe_pipeline` drops `collected_df` /
`combined_lf`, before `run_fs_dedupe_bucketed` reaches output (with `base`
captured + `all_ids` taken first). This is a shared-pipeline change with its own
parity surface (every route funnels through `_run_dedupe_pipeline`), so it is
gated on a measured need: build it only if the Phase-3a 50M scale gate shows the
1×-frame plateau is still too close to 64 GB. Since the scoring plateau already
sat at ~31–36 GB, Phase 3a is expected to complete 50M comfortably without it.
