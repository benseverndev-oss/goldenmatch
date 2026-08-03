# FS frame-residency Phase 3b — chunk the golden build (the real 50M lever)

**Status:** design (supersedes this file's earlier "free the polars frame" draft — that diagnosis was refuted by measurement; see below)
**Date:** 2026-08-02 (measured 2026-08-03)
**Owner:** goldenmatch FS out-of-core
**Predecessors:** Phase 1 (bucketed scoring), Phase 3a (join-free output)

## The wrong turn (kept as a lesson)

This spec originally proposed freeing the **polars** `score_frame` to kill a
"polars/arrow 2× duplication" seen as `base = _fw.to_arrow()`. A spec-review +
per-stage measurement **refuted** it:
- The bench (`dedupe_to_parquet` on a file) runs the **arrow lane**, where
  `collected_df` is a `pa.Table` and `_tf(t).to_arrow()` is **zero-copy**
  (measured `zero_copy=True`, +0 MB). There is **no polars frame to free**.
- Per-stage peak RSS at 4M: prep → 3425 MB, then every FS stage through
  `bucket_score` **flat**. WCC (+111 MB) and the batched output write (+275 MB)
  are cheap. The 6.5 GB peak is a **transient inside the golden build**, near the
  end of `_stream_fs_dedupe_output_batched`.
- Chunking `build_golden_records_batch`'s per-cluster compute did NOT help
  (peak 6485); an allocator-retention test (`dirty_decay_ms:0`) did NOT help
  (peak 6482) — so it is **live** memory in the golden **materialization**, not
  the per-cluster loop and not jemalloc.

Lesson: measure the actual peak locus before designing the fix; a plausible
duplication story on the wrong code lane cost a full spec.

## Problem (measured)

`_stream_fs_dedupe_output_batched` accumulates the golden-eligible subset across
output batches (`golden_parts`), then at the end does, **all at once**:
`pa.concat_tables(golden_parts)` → `pl.from_arrow(...)` →
`build_golden_records_batch(...)` (returns a `list[dict]`, one per cluster) →
`pl.DataFrame(records).write_parquet(...)`. At 4M that subset is 1.2M rows /
547,846 clusters, and the simultaneously-resident set — the arrow `base`
(~3.2 GB) + the golden arrow table + its polars copy + the 547K-dict `records`
list (~0.5 GB) + the output frame — spikes to **6.5 GB**. Whole-run peak scaled
~1476 MB/M → **50M ≈ 74 GB (OOM on 64 GB)**.

## Fix — chunk the golden build+write by whole clusters

Golden records are **per-cluster independent** (`build_golden_records_batch`
computes one record per `__cluster_id__` from that cluster's rows only). So the
build+write can be chunked, bounding the resident records/frames to one chunk:

- Concatenate `golden_parts` once (arrow, cheap ~cluster-subset-sized), then drop
  `golden_parts`.
- Partition into `n = ceil(golden_rows / _GOLDEN_BUILD_CHUNK_ROWS)` chunks by
  **`__cluster_id__ % n`** (numpy — pyarrow.compute has no `modulo` kernel here).
  Every row of a cluster shares `__cluster_id__`, so `% n` keeps each cluster
  wholly in one chunk (never split → per-cluster survivorship is exact).
- For each non-empty chunk: `build_golden_records_batch(pl.from_arrow(chunk))` →
  `pl.DataFrame(records).to_arrow()` → append to a single `pq.ParquetWriter`
  opened at the first chunk's schema; accumulate `golden_count`; `del` the chunk +
  records before the next. Preserve the empty-golden `unlink` + `golden_path=None`
  contract.

`_GOLDEN_BUILD_CHUNK_ROWS` default 200,000 (matches the output batch size). At/
below one chunk (`n == 1`, small runs) the path is the prior single-build behavior.

## Correctness / parity

- **Byte-identical output.** Clusters are independent; chunk assignment and order
  are irrelevant to each cluster's golden record, and `% n` never splits a
  cluster. Row-set of the golden parquet is unchanged (order may differ — golden
  is keyed by cluster). Validated: 4M/8M bench golden_count + unique/dupes
  identical to the single-build path; the bucketed↔sequential parity tests stay
  green.
- **No caller-side change.** Local to `_stream_fs_dedupe_output_batched`; the
  arrow `base` stays as-is (freeing it is unnecessary — see below).

## Measured result

Local (person fixture, bucketed, edge-bearing stable-anchor blocking):
| rows | peak BEFORE | peak AFTER (chunked) |
|------|-------------|----------------------|
| 4M   | 6539 MB     | **3825 MB** (−42%)   |
| 8M   | ~13 GB (extrap.) | **6643 MB**     |

AFTER slope ≈ **704 MB/M** → **50M ≈ 36–40 GB**, clears 64 GB with ~24 GB
headroom (BEFORE: ~1476 MB/M → 74 GB OOM). Output byte-identical; wall slightly
*faster* (4M 141→123 s — less allocation). The arrow `base` (~0.8 GB/M) is the
remaining resident term (~40 GB at 50M); it need NOT be freed since the chunked
golden already clears 64 GB. If a future workload needs below ~1× base, freeing
`base` before golden is a follow-on (caller-side, arrow lane: drop
`collected_df`/`combined_lf` — the mechanism the earlier draft described, now for
the *right* reason).

## Gates

- **Parity unit test** (`tests/test_fs_out_of_core.py::test_bucketed_golden_chunked_write_parity`):
  forcing many chunks (`_GOLDEN_BUILD_CHUNK_ROWS=1`) yields byte-identical golden
  to a single chunk (`=10**9`). Plus the existing bucketed↔sequential parity.
- **Scale gate** (`bench-fs-out-of-core.yml`, bucketed, 50M): completes under
  64 GB at ~36–40 GB peak — the binding proof.

## Rollout

- FS bucketed route; no new public flag (`_GOLDEN_BUILD_CHUNK_ROWS` is an internal
  constant). Default path byte-unchanged. Update the FS out-of-core CLAUDE.md
  bullet with the measured golden-build diagnosis + the chunked-write fix.
