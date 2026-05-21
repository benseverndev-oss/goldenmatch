# ADR-0005: Streaming-block sync as the >500K-row path

**Status:** Accepted
**Date:** 2026-05-21 (PRs #400, #402)

## Context

Pre-#386, `run_sync`'s full-scan path collected the entire input into memory before dispatching to `dedupe_df`. On an 8 GB sandbox, a 1.13M-row table × 60 cols × ~50 bytes/cell ≈ 5-8 GB collected, leaving no headroom for `dedupe_df`'s allocations. SIGKILL (exit 137) at the dispatch boundary; no clusters written.

#386 added streaming-block sync (`_full_scan_streaming` in `db/sync.py`): walk the staging parquet one block at a time, score the block, write `gm_match_log` incrementally, drop the block frame, repeat. Peak RSS bounded by the largest individual block's scoring footprint, not the dataset total. #400 wired it behind a threshold-gated router.

The remaining question (#401): what's the threshold?

## Decision

Default `GOLDENMATCH_SYNC_STREAMING_THRESHOLD = 500_000` rows. Above this, route to streaming-block. Below, use the legacy single-collect path (faster per-row, fits comfortably in 16 GB).

Reasoning: 500K × 60 cols × ~50 bytes/cell ≈ 1.5 GB collected. Leaves room for `dedupe_df`'s allocations (~3-5 GB peak) on an 8 GB sandbox. The pre-#402 default of 5M was tuned for 16 GB+ hosts and failed open on 8 GB sandboxes.

Rejected alternatives:
- **Always stream.** Streaming is ~20-30% slower per row than the legacy single-collect on small frames (the per-block orchestration overhead doesn't pay off below ~500K rows). Default-on would tax every small-N user.
- **Auto-detect host memory + size threshold to it.** Tempting but the user often runs goldenmatch in containers / sandboxes where free memory misrepresents available capacity. Explicit threshold + env override is simpler and predictable.
- **Always single-collect, fix dedupe_df to accept a LazyFrame.** Bigger refactor; `dedupe_df`'s internal pipeline materializes anyway for matchkey computation. Wouldn't help.

## Consequences

Positive:
- 1.13M-row syncs now complete on 8 GB sandboxes (the original #401 scenario).
- The narrow scoring kernel (`_score_partition_with_config` in `core/pipeline.py`) is shared between streaming-block sync and distributed scoring (`distributed/scoring.py::_score_partition`). One kernel, two code paths.
- Backend-selection log line at the routing point makes the chosen path observable without grepping.

Negative:
- Small-frame syncs (<500K) don't get the bounded-RSS guarantee. Users on memory-constrained hosts with <500K data either tolerate it (still fits) or lower `GOLDENMATCH_SYNC_STREAMING_THRESHOLD` explicitly.
- The 500K default is a magic number tuned for 8 GB sandboxes. Users on 32 GB+ hosts may prefer the legacy path's speed even up to 5M. Documented; env-overridable.
- V1 streaming-block sync is non-resumable. A 50M-row sync that fails partway requires re-running from scratch. Resumable state is a documented V2 follow-up; the spec calls this out.

Cross-references:
- Spec: `docs/superpowers/specs/2026-05-21-streaming-block-sync-design.md`
- PRs: #386 (issue), #400 (implementation), #401 (followup issue), #402 (threshold change)
