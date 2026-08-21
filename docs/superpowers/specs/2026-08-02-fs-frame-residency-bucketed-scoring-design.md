# Frame-residency-bounded FS scoring: per-pass disk bucketing + native external WCC

**Status:** design (measured prototype in hand)
**Predecessors:**
- `2026-07-20-fs-frame-residency-bucket-streaming-design.md` — the DuckDB-spilled `run_fs_dedupe_streaming` + in-RAM `run_fs_dedupe_sequential`; named "load-peak below ~1× frame" as the open item.
- `2026-08-02-fs-duckdb-free-spill-external-wcc-design.md` — `run_fs_dedupe_spill`: bounds the EDGE term (per-pass shards + `external_wcc_from_shards`). Left frame residency + the native array-UF as open phases.

## Problem (measured)

The `spill` path bounds the edge term but **holds the whole prepared frame resident**
for scoring, plus a `to_arrow()` copy at the spill entry, plus `score_buckets`'
internal `partition_by` copy. Peak RSS therefore tracks **~2.5× frame**, not the
edge list.

The `bench-fs-out-of-core` run on 2026-08-02 (64 GB runner, `spill` +
`force_shard`) confirmed it empirically:

- **spill @ 25M** completed at **42 GB** peak (`internal_path=edge_shard`) — but the
  fixture/auto-config produced 0 edges, so the 42 GB is essentially frame + prep.
- **spill @ 50M** climbed monotonically — 49 GB → 55 GB → **62.8 GB** — and the
  runner was **killed at the 64 GB ceiling**. It did NOT complete.

So bounding edges cannot get under a frame-residency floor that is itself over the
box. **Frame residency, not the edge term, is what gates 50M-on-64GB.**

## Key insight (measured)

**Hash-partitioning the frame by the blocking-pass key co-locates every block
wholly inside one bucket** (all members of a block share the block key → the same
hash → the same bucket). So a block never spans buckets, and scoring each bucket
*independently* yields the **same edge set** as scoring the whole frame — for a
single static pass, exactly; for `multi_pass`, apply it **per pass** (bucket the
frame once per pass on that pass's key). Union the per-bucket, per-pass edges; the
union-find is **idempotent under duplicate edges** (`external_wcc_from_shards`
applies `union(a,b)` on every raw edge, duplicates included, and `n_pairs` is the
raw count — it does NOT dedup), so the partition is invariant to the cross-pass /
cross-bucket duplicates and comes out identical.

**Scope: static / multi_pass only.** This equivalence holds only where a pass's
block is a single-key equivalence class, i.e. `_build_block_key_expr` exists for it.
It is **unsound for `sorted_neighborhood`** (a sliding window over a *sort* key —
one record appears in several windows straddling neighbors that hash to different
buckets) and equally for `adaptive`/`ann`/`canopy`. The design is confined to
static/multi_pass by the existing `_fs_streaming_dedupe_eligible` gate (see
Correctness gate) — the same guard `build_em_blocks_agg` uses when it raises
`NotImplementedError` for non-key strategies.

This means the frame need **never be fully resident during scoring**: read the
input in batches, bucket to on-disk Arrow shards, then stream **one bucket at a
time** through the existing per-bucket scorer + the existing edge-shard spill. Peak
during scoring becomes `O(one bucket)`.

### Prototype evidence (local measurement harness, single static pass, person)

Measured 2026-08-02 on a 4c/15 GB box with a standalone two-mode harness
(baseline = whole-frame `score_buckets_arrow`; prototype = batched-read →
hash-bucket-to-disk → per-bucket score), one mode per process for a clean
`ru_maxrss`:

| rows | baseline (resident frame) | prototype (bucket→disk, stream) | Δ peak | wall (base→proto) |
|---|---|---|---|---|
| 3.9M | 1665 MB | 955 MB | **−43%** | 39.1s → 39.2s |
| 7.8M | 3566 MB | 1704 MB | **−52%** | 139.5s → 107.7s (**−23%**) |

Byte-identical cluster partition at every scale (same count + SHA of member sets),
identical pair counts (10.9M / 41.9M). The relative win **grows with scale** (the
frame term grows linearly; the per-bucket working set is flat), and at 7.8M the
prototype is also **faster** (the baseline pays to hold a 3.5 GB frame + a 42M-edge
partition working set).

**Second finding:** the prototype's *new* floor is the **external-WCC Python
dict union-find** (`core.cluster.UnionFind`), not the frame — a single bucket is a
few MB; the residual peak is the `dict` parent/rank tables over millions of ids.
This is exactly the phase-2 native `FsExternalWcc(n_rows)` array-UF the spill spec
named.

## Design

Four bounded mechanisms; (A) and (C) are the new work, (B) reuses the spill path.

### (A) Batched-read + per-pass disk bucketing — `bucket_frame_to_shards`
Read the prepared input in row batches (`pq.ParquetFile.iter_batches` /
`RecordBatch` stream — never a full resident frame). For each blocking pass,
derive that pass's block key (reuse `core.blocker._build_block_key_expr`, which
aliases `__block_key__`) and apply the SAME null/sentinel drop `build_blocks` uses
— `core.frame.Frame.filter_valid_key(col)` (not a re-implemented `["nan","null",
"none"]` filter), so key filtering is byte-identical to `build_blocks`. Hash the
key to a bucket id and append each batch's rows to per-`(pass, bucket)` Arrow IPC
shards via bounded `RecordBatchFileWriter`s. Resident working set = one input batch
+ the open writers' current batch. `__row_id__` is assigned as a running counter
across batches (matching the pipeline's ingest row-id contract).

Bucketing is on the **block key only**, so a stable non-cryptographic hash
(`crc32`/`xxhash` of the key bytes) suffices — it does not need to match
`score_buckets`' internal bucketing; it only needs each block wholly in one bucket.

### (B) Sequential bucket scoring + edge spill (reuse)
For each `(pass, bucket)` shard: memory-map it, run `score_buckets_arrow` on a
single-pass config for that pass, and spill the pass's edges via the existing
`spill_pair_shard` / `pair_sink(callable(pa.Table))` seam (arrow-mode only, invoked
per pass). Peak = one bucket's rows + one bucket's edges. No new scoring code.

**The reference is the FUSED kernel, not `score_buckets`.** `run_fs_dedupe_sequential`
(and `_spill`) default to `_fused_fs_assignments` (`run_match_fused_fs_arrow` —
group + FS-score + WCC in one call) and only fall back to `score_buckets_arrow +
_cluster_arrow_native` when the config is uncovered. So the partition-equality gate
is really **fused-kernel (reference) vs `score_buckets`-per-bucket (treatment)** in
the common person case — the SAME `fused == score_buckets` invariant `sequential` ↔
`spill` already rely on. `score_buckets` **auto-splits oversized blocks in-bucket**
(`_split_oversized`/`_auto_split_block`, a deterministic pure function of the block's
members) identically to the whole-frame call, so oversized blocks are NOT a
bucketing-induced divergence on the scoring path; any oversized parity question
lives in the fused-vs-`score_buckets` reference equivalence, not in the bucketing
step (see Correctness gate).

### (C) Native external union-find — `FsExternalWcc(n_rows)`
Replace the Python dict-UF with a stateful native kernel over an `O(N)` `Vec<i64>`
parent array (path-compression + union-by-rank):
- `FsExternalWcc(n_rows)` — allocate the parent array.
- `.union_arrow(shard)` — apply one edge shard's `(id_a, id_b, score)` over the
  array, filtered by `score >= link_threshold` (Arrow C Data Interface in, no
  Python objects per edge).
- `.finalize() -> assignments` — path-compress + emit `(__row_id__, __cluster_id__)`
  as a `pa.Table` (same shape `_cluster_arrow_native` / `external_wcc_from_shards`
  return, so `_stream_fs_dedupe_output_arrow` consumes it unchanged).

Byte-parity gated against the Python `external_wcc_from_shards` (dict-UF) on the
same shards. Lives in the `native` crate next to the existing Rust
`build_clusters_arrow` kernel (`packages/rust/extensions/native/src/cluster.rs`,
registered in `lib.rs`; note `core.cluster.build_clusters_arrow_native` is the
Python *wrapper* over that kernel). The Python `external_wcc_from_shards` stays as
the classified fallback (`GOLDENMATCH_FS_EXTERNAL_WCC_NATIVE=0`).

### (D) End-to-end — `run_fs_dedupe_bucketed`
`train EM (on the resident frame, unchanged) → bucket_frame_to_shards (per pass) →
per-bucket score + edge spill → FsExternalWcc → _stream_fs_dedupe_output_arrow`.
Routed via a new `resolve_fs_block_source()` value **`bucketed`** (alongside
`eager`/`frame`/`sequential`/`spill`/`duckdb`) + a `fs_streaming_route()` return of
`"bucketed"`, so `_fs_streaming_dedupe_eligible` (`pipeline.py`) fires — reachable
through `gm.dedupe_to_parquet(*files, out_dir=…)` with
`GOLDENMATCH_FS_BLOCK_SOURCE=bucketed`. DuckDB-free, polars-free except the bounded
golden builder.

**EM training is identical to the reference and is NOT bucketed.** Like
`_run_fs_streaming_dedupe`, EM trains via `load_or_train_em` on the resident frame's
bounded EM blocks (the `GOLDENMATCH_FS_EM_SAMPLE_ROWS` sample); only the
score→cluster→output back-half is bucketed. The `EMResult` must be byte-identical or
the sampled pairs — hence every score — diverge. (EM's own `build_blocks` residency
is the separate, already-studied `GOLDENMATCH_FS_EM_AGG_BLOCKS` axis.)

**The eligibility gate is the guardrail.** `_fs_streaming_dedupe_eligible`
(`pipeline.py`) already enforces the two preconditions this design silently needs:
**static/multi_pass blocking only** (rejects the SN/adaptive/ann/canopy strategies
that make per-pass hash-bucketing unsound) and **exactly one probabilistic
matchkey** (so there is no cross-bucket `matched_pairs` exclude set to share — which
is what makes independent per-bucket scoring safe; arrow-mode `score_buckets`
doesn't build the exclude set anyway). `bucketed` routes behind it.

**Note on prep.** (A) removes frame residency during *scoring*. The pipeline's
*prep* (auto_fix / standardize / matchkey precompute) currently still materializes
the frame upstream; a fully-streamed prep (batch prep → prepared parquet) is a
follow-on. The measured 50M ceiling is the scoring frame + its copies, which (A)
addresses; prep streaming is the next ceiling after this lands.

## Phasing

1. **(A)+(B)+(D) behind `GOLDENMATCH_FS_BLOCK_SOURCE=bucketed`, Python dict-UF** —
   the measured ~50% peak cut, partition-exact. Ships the routing + parity tests.
2. **(C) `FsExternalWcc` native kernel** — removes the dict-UF floor the prototype
   surfaced; `GOLDENMATCH_FS_EXTERNAL_WCC_NATIVE` default-on with the Python
   fallback. Also back-ports into `run_fs_dedupe_spill` (same clustering call).
3. **Streamed prep** (batch prep → prepared parquet, no resident prep frame) — the
   *next* ceiling; separate spec.

## Correctness gate

`run_fs_dedupe_bucketed` is partition-gated against `run_fs_dedupe_sequential`
(the in-RAM reference) on the SAME config: identical unique/dupes/golden counts AND
identical dupes-partition, on person + bibliographic fixtures, **static AND
multi_pass** blocking (per-pass bucketing is the multi-pass-specific risk;
non-key strategies are out of scope by the eligibility gate). The native
`FsExternalWcc` is separately byte-parity-gated against the Python
`external_wcc_from_shards`. Because clustering is a pure function of the edge set,
these are exact partition-equality gates.

**Oversized blocks are NOT a bucketing divergence on the scoring path.**
`score_buckets` auto-splits an oversized block in-bucket
(`_split_oversized`/`_auto_split_block`) identically to the whole-frame call — a
whole block is co-located in one bucket and the split is a deterministic pure
function of its members. The oversized parity question instead lives in the
**fused-kernel vs `score_buckets` reference equivalence** (the reference defaults to
the fused kernel; see (B)) — exactly the `fused == score_buckets` invariant
`sequential` ↔ `spill` already carry, guarded by the `bench-probabilistic` panel as
with `GOLDENMATCH_FS_EM_AGG_BLOCKS`. This design does not re-bucket EM-training block
formation (the one place `build_em_blocks_agg` does raise on oversized), so that
edge does not apply here.

## Scale gate

Extend `bench-fs-out-of-core` with a `bucketed` mode. The binding proof: a shape
whose resident frame OOMs the in-memory/`spill` path at 50M on 64 GB **completes**
under `bucketed`, at a peak bounded well under the frame floor (target: peak <
~1.2× the largest single bucket + the parent array, not ~2.5× frame). Records
`internal_path`, peak RSS, and wall per leg like the existing modes.

## Open questions / risks

- **Per-pass disk cost.** `multi_pass` buckets the frame once per pass → K× the
  bucket-write I/O. At 50M with ~6 passes that is real disk traffic; the wall trade
  vs the peak win must be measured (the prototype's single-pass run showed no wall
  penalty — multi-pass needs its own datapoint).
- **Open-writer count.** `(pass × bucket)` open `RecordBatchFileWriter`s buffer a
  batch each; cap `N_BUCKETS` (default 64) and flush per input batch so the writer
  buffers stay small. A too-large bucket count trades memory back.
- **Bucket skew.** A heavy block key (a dominant zip / null-ish sentinel) can make
  one bucket large, re-inflating the per-bucket peak. The existing null/sentinel
  block-key filter drops the degenerate keys; a secondary within-bucket cap
  (spill an over-large bucket through the existing wave loop) is the safety valve.
- **Prep still resident.** As noted, this phase does not stream prep; the 50M win
  is the scoring frame. If prep residency alone exceeds 64 GB at some scale, phase
  3 is required before that scale is reachable.

## Why not just use DuckDB
The DuckDB path already spills the frame and is the supported ≥40M tier. This is the
**DuckDB-free** single-box lane (thesis-aligned: Arrow/Rust, no DuckDB dependency),
continuous with the `spill` edge mechanism and the fused kernel — one FS engine,
progressively bounded, no second runtime.
