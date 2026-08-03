# FS frame-residency — lowering the resident-frame ceiling (raise single-box max scale)

**Status:** design (not yet built — each lever is measurement-gated below)
**Date:** 2026-08-03
**Owner:** goldenmatch FS out-of-core
**Predecessors:** Phase 1 (bucketed), 3a (join-free output), 3b (chunked golden),
parallel-scoring + array-UF (PR #2385)

## Problem (measured)

After chunking the golden build (the 50M OOM fix), the bucketed FS path is
**purely frame-residency bound**. Measured 50M peak = **39.7 GB**, and the arrow
`base` (the wide prepared frame) is ~0.8 GB/M ≈ 40 GB — i.e. **`base` alone is
essentially the entire peak.** Golden (chunked), WCC (array-UF), and output all
fit in the slack now.

`base` is built by prep and reaches its full size at end-of-prep, then is held
resident through the whole back-half. So the peak plateau IS `base`, reached
before scoring even starts.

**Consequence:** peak ≈ 1× `base` ≈ 0.8 GB/M → single-box max ≈ **~75M rows on
64 GB** (`peak ≤ ~60 GB`), box-proportional above that.

`base` is **13 columns** at the person shape: 6 source + `__source__` +
`__row_id__` + **~5 `__xform_<sig>__`** columns that
`precompute_matchkey_transforms` materializes on the whole frame.

## The distinction that governs which lever helps

- **Lowering the PEAK ⇒ raises max scale.** Requires `base` to be smaller, or
  never fully resident. (Levers 1, 2.)
- **Freeing the plateau DURING the back-half ⇒ frees headroom (wall/parallelism),
  NOT max scale.** Because prep already peaked at full `base` before the back-half
  runs. (Lever 3.)

The user's "the whole prepared frame lives in RAM" (max scale) is levers **1/2**;
it composes with the parallel-scoring PR via lever **3**.

## Lever 1 — don't materialize `__xform_` on the whole frame (narrower base)

`precompute_matchkey_transforms` roughly **doubles the frame width** (adds ~5
transformed columns). If the FS scoring path recomputed each matchkey field's
transform **per bucket shard** (on ~1/64 of the frame at a time) instead of
reading a whole-frame-materialized `__xform_` column, `base` drops from 13 → ~8
columns ≈ **~40% smaller ⇒ ~1.6× max scale** (~75M → ~120M on 64 GB).

- **Cost:** recompute transforms per shard. The transforms are cheap
  (lowercase/soundex/substring), and the shards are small — likely negligible vs
  the scoring itself. But the FS native path currently PREFERS the precomputed
  `__xform_<sig>__` column (a deliberate −82% `bucket_score` win, CLAUDE.md); this
  lever would make it recompute on the shard instead, so it trades that back.
- **MEASUREMENT GATE (build only if this holds):** on a 4M/8M local A/B, dropping
  the `__xform_` columns from `base` before bucketing (recompute per shard) must
  (a) lower peak by ≈ the `__xform_` width fraction, and (b) not regress wall by
  more than a few %. If recompute regresses wall materially, this lever is a
  peak↔wall tradeoff, not a free win — decide then.

## Lever 2 — streaming prep (break the 1× ceiling)

The ultimate lever: build `base` in bounded row-batches and spill to disk during
prep, so the full wide frame is **never simultaneously resident**. Then bucketing
and output stream from the disk spill. This lowers the peak below 1× `base`
(bounded by one batch + working set), breaking the frame-residency ceiling
entirely.

- **Scope (high):** GoldenCheck quality scan, GoldenFlow transforms, auto-fix,
  standardize, and precompute all run whole-frame in `_run_dedupe_pipeline`. Most
  are **row-independent** (per-cell / per-row), so streamable in principle — but
  it's a shared-pipeline refactor touching every FS + non-FS caller, with its own
  parity surface. The quality scan may have cross-row aggregates (dup-rate,
  functional deps) that need a two-pass or approximate streaming form.
- **DEFER** until levers 1 + 3 are measured — this is the biggest change for the
  highest ceiling, and past ~100–150M the **distributed Ray path** is the better
  tool anyway, so the ROI of pushing single-box this far is bounded.

## Lever 3 — spill `base` + free it during the back-half (headroom for #2)

Fold a **single row-complete spill** of `base` into `bucket_frame_to_shards`
(one extra unfiltered `RecordBatchFileWriter`), then free the RAM `base` before
scoring; stream output from the spill instead of the resident frame. (The per-pass
bucket shards can't serve as the output source — a row appears in multiple passes
and null-key rows are dropped — hence a separate row-complete spill.)

- **Does NOT lower the peak** (prep already peaked at full `base`), so it does not
  raise max scale. What it buys: the long scoring/WCC/output phase runs without
  `base` resident, so the freed ~0.8 GB/M is **headroom the parallel-scoring PR
  can safely spend on more workers** — i.e. it removes the peak↔parallelism
  tradeoff that PR flagged. Low risk, contained to `run_fs_dedupe_bucketed`.
- **MEASUREMENT GATE:** confirm the freed headroom lets a higher
  `GOLDENMATCH_FS_BUCKET_SCORE_WORKERS` cut wall further at 50M without exceeding
  64 GB.

## Recommended sequence

1. **Lever 1** (narrow base) first — best ROI (~1.6× scale), medium risk, cleanly
   measurable. Gate on the 4M/8M A/B above.
2. **Lever 3** (spill+free) next — removes the parallel-scoring peak tradeoff;
   low risk.
3. **Lever 2** (streaming prep) only if a real >100M single-box workload
   materializes; otherwise it's distributed-path territory.

## WCC (already addressed, noted for completeness)

The Python dict-UF → numpy array-UF landed in PR #2385 (`int64[N]` parent +
vectorized pointer-jump compression). The next WCC step, if edge counts grow past
~200M, is a native/Rust array-UF or the two-phase WCC — but the numpy version is
cheap enough (+111 MB at 4M scale) that it's not the ceiling until well past the
frame-residency limit.

## Discipline note

Every lever here is **measurement-gated** — this session already had two
plausible-but-wrong frame diagnoses (polars/arrow duplication; the golden build
mis-attributed to per-cluster compute) that only per-stage RSS profiling caught.
Do NOT build any lever before its A/B confirms the predicted peak/wall effect.
