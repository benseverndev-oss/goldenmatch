# Handoff — FS out-of-core single-box scale (bucketed route)

**Date:** 2026-08-03
**Branch:** `claude/sequential-batch-review-7qp50y` (fresh on `main`, no open PR)
**Mission:** make the single-box Fellegi-Sunter (FS) dedupe path complete a **50M-row**
dedupe on a **64 GB** box, where it previously OOM'd (~82 GB projected) — then push
further and faster.

---

## TL;DR status

- ✅ **50M FS dedupe COMPLETES on 64 GB** — measured **39.7 GB peak** (run
  `30777912723`, on the merged golden-chunking fix). Was ~74 GB OOM.
- ✅ Four PRs merged (see below). Path is opt-in, byte-identical, default-off.
- 🔄 **One validation in flight:** run **`30817660536`** (50M, bucketed) checks that
  the newly-merged **parallel shard scoring** (more shards in flight → higher peak)
  still fits 64 GB. Expected ~40–50 GB. **Read `peak_rss_sampled_mb` from its
  Summarize-step JSON when done.** A self check-in is armed (~13:56 UTC) to do this.
- 📋 **Next lever spec'd, not built:** frame-residency (raise max scale past ~75M).

---

## How to use the path (for a user)

```python
import goldenmatch as gm
# single probabilistic matchkey + static/multi_pass blocking + output_dir
gm.dedupe_to_parquet(*files, out_dir="out/", config=cfg)   # writes unique/dupes/golden parquet
```
Enable the bucketed route: `GOLDENMATCH_FS_BLOCK_SOURCE=bucketed`. Falls back to the
in-memory pipeline (same output files) when not FS-eligible.

Eligibility (`_fs_streaming_dedupe_eligible`): `output_dir` set + exactly one
probabilistic matchkey + `static`/`multi_pass` blocking.

---

## What shipped (merged, in order)

| PR | change | measured effect |
|----|--------|-----------------|
| **#2367** | edge-bearing stable-anchor bench blocking (bench fix) | co-block recall **0.49→0.94**; the scale bench now measures real edges |
| **#2372** | **Phase 3a** — join-free batched output (`_stream_fs_dedupe_output_batched`) | output 2×→1× frame (removed `frame.join(asn).join(sizes)`) |
| **#2378** | **Phase 3b** — chunk the golden build (`__cluster_id__ % n`) | 4M peak **6539→3825 MB (−42%)**; **50M: 74 GB OOM → 39.7 GB, completes** |
| **#2385** | parallel shard scoring + numpy array union-find | 4M wall **226→182 s (−19%)**; WCC dict-UF → `int64[N]` array-UF |

All four are byte-identical output (parity-gated). The external WCC is invariant to
edge/shard order; the array-UF is partition-exact; golden chunking keeps each cluster
whole.

---

## Measured scale envelope (the numbers to trust)

Peak RSS, bucketed route, person fixture (13 cols, 20% dupe), post-golden-chunk:

| rows | peak RSS | source |
|------|----------|--------|
| 1M | 2.1 GB | local |
| 4M | 3.8 GB | local |
| 8M | 6.6 GB | local |
| 25M | 36 GB | CI (old code; peak was the end-of-run golden-build jump) |
| **50M** | **39.7 GB** | **CI, merged fix — the binding proof** |

**~0.79 GB per million rows, linear.** The peak is now **~1× the resident arrow
`base` frame** (≈0.8 GB/M ≈ 40 GB at 50M) — golden (chunked), WCC (array-UF), and
output all fit in the slack. **The path is purely frame-residency bound.**

**Predicted max: ~75M rows on 64 GB** (safe ~60 GB ceiling; ~82M at the edge).
Box-proportional: ~150M on 128 GB, ~310M on 256 GB. Wall ≈ 20 min at 50M, linear.

---

## Where the code lives

`packages/python/goldenmatch/goldenmatch/backends/fs_out_of_core.py`:
- `run_fs_dedupe_bucketed` — orchestrator: `base = to_arrow()` → `bucket_frame_to_shards`
  → **parallel** shard scoring (`_fs_bucket_score_workers`) → `external_wcc_from_shards`
  → `_stream_fs_dedupe_output_batched`.
- `bucket_frame_to_shards` — per-pass hash-bucket the frame to disk Arrow shards.
- `external_wcc_from_shards` — numpy array-UF (contiguous ids) / `_external_wcc_dict`
  (gapped fallback).
- `_stream_fs_dedupe_output_batched` — join-free batched output; **chunked golden
  build** (`_GOLDEN_BUILD_CHUNK_ROWS`).

`core/pipeline.py::_run_fs_streaming_dedupe` — trains EM on the resident frame (100K
sample cap), then dispatches to the orchestrator by `fs_streaming_route()`.

---

## Env knobs

| var | default | effect |
|-----|---------|--------|
| `GOLDENMATCH_FS_BLOCK_SOURCE` | `auto` | `bucketed` selects this route |
| `GOLDENMATCH_FS_BUCKET_SCORE_WORKERS` | `min(cpu,8)` | shard-scoring concurrency; `1`=serial. More workers ⇒ faster ⇒ higher peak |
| `GOLDENMATCH_FS_EM_SAMPLE_ROWS` | `100000` | EM-block sample cap (bounds EM memory) |
| `_GOLDEN_BUILD_CHUNK_ROWS` (constant) | `200000` | golden build+write chunk size |

Bench: `scripts/bench_fs_out_of_core_scale.py --rows N --mode bucketed`.
CI gate: `.github/workflows/bench-fs-out-of-core.yml` (workflow_dispatch, 64 GB runner).

---

## Pending / next work

### 1. Finish the in-flight 50M validation (run `30817660536`)
Confirm parallel scoring keeps 50M **under 64 GB**. If peak > ~55 GB or it OOMs, lower
the `_fs_bucket_score_workers` default (e.g. `min(cpu,4)`) or make it headroom-aware,
and push a fix. (Serial baseline was 39.7 GB; parallel adds in-flight shards.)

### 2. Frame-residency levers — the #1 max-scale follow-on (spec'd, NOT built)
Spec: **`docs/superpowers/specs/2026-08-03-fs-frame-residency-disk-spill-design.md`**.
Peak = ~1× `base`; to go past ~75M you must shrink or stop fully materializing `base`.
Each lever is **measurement-gated** (A/B first):
- **Lever 1 (best ROI, ~1.6× scale):** don't materialize `__xform_` columns on the
  whole frame — recompute per shard → ~40% narrower `base`. NOTE: the FS native path
  currently *prefers* the precomputed `__xform_` column (a −82% `bucket_score` win),
  so this trades that back; A/B must show the peak win outweighs the recompute.
- **Lever 3 (enables parallelism):** spill `base` row-complete to disk + free the RAM
  copy during the back-half → headroom for more scoring workers (doesn't lower peak).
- **Lever 2 (streaming prep):** the ceiling-breaker; high risk (shared-pipeline refactor
  of GoldenCheck/GoldenFlow/precompute). Defer — past ~100–150M the **distributed Ray
  path** is the better tool.

### 3. WCC beyond ~200M
The numpy array-UF is cheap now; a native/Rust array-UF or two-phase WCC is only needed
well past the frame-residency limit. Not the ceiling yet.

---

## Design specs (all git-tracked under `docs/superpowers/specs/`)
- `2026-08-02-fs-frame-residency-bucketed-scoring-design.md` (Phase 1)
- `2026-08-02-fs-frame-residency-streamed-output-design.md` (Phase 3a)
- `2026-08-02-fs-frame-residency-phase3b-free-frame-design.md` (Phase 3b — **retains
  the wrong-turn lesson**)
- `2026-08-03-fs-frame-residency-disk-spill-design.md` (the pending levers)

---

## Load-bearing lessons (do not relearn the hard way)

1. **Measure the peak locus with per-stage RSS before designing a memory fix.** This
   effort had **two** plausible-but-wrong diagnoses that only per-stage profiling caught:
   (a) "polars/arrow frame duplication" — wrong, the bench runs the **arrow lane** where
   `base = to_arrow()` is **zero-copy**; (b) the golden spike mis-attributed to
   `build_golden_records_batch`'s per-cluster compute — wrong, it was the whole-subset
   materialization (`concat` + `from_arrow` + the 547K-dict records list). Chunking the
   build fixed it; chunking the per-cluster compute didn't.
2. **Throughput A/Bs need a same-box back-to-back.** A first 8M parallel-scoring A/B
   showed a bogus **2× regression** — pure box noise (loaded vs unloaded). The clean
   same-box 4M back-to-back showed the real **−19%**.
3. **Arrow lane vs classic lane matters.** `dedupe_to_parquet(file)` on polars-free
   goldenmatch takes the **arrow lane** (`collected_df` is a `pa.Table`); a spec-review
   caught that the "free the polars frame" fix was a no-op there.
4. **The 50M CI runner OOMs to NO artifact** — an OS-level OOM kills the runner before
   the `if: always()` upload, so a failed run leaves job=failure + step frozen
   `in_progress` + 0 artifacts + 404 logs. Don't burn a 50M run to confirm a predicted
   OOM; extrapolate from clean 4M/8M locals first.

---

## Operational notes
- Git auth: `benzsevern`. Commit trailers use `noreply@anthropic.com`.
- A self check-in (send_later) is armed to read run `30817660536` and drive any fix.
- The GitHub squash-merge sets committer `noreply@github.com` on merged commits — those
  show "Unverified" but are merged history; do NOT amend them (rewrites shared history).
