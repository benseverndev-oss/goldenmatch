# Lance vs Parquet for Candidate Retrieval — Evaluation Design

**Date:** 2026-06-13 · **Issue:** TBD · **Branch:** claude/iceoryx2-goldenmatch-bsmd9l · **Status:** spike (pre-decision)

## Origin

This came out of a "can iceoryx2 help GoldenMatch?" review. Conclusion there:
iceoryx2 (zero-copy *local IPC*) is a weak fit — GoldenMatch is compute-bound, not
IPC-copy-bound, and its distributed path is Ray-over-network, which iceoryx2
cannot touch. The one trending Rust project that could move an *architecture*
decision rather than tooling is **Lance** (`lance-format/lance`): an open columnar
format with **fast random access** and zero-copy reads, already name-checked in
`docs/er-vendor-comparison.md`. This spec scopes a measured bake-off before any
adoption — per the performance-audit lesson, *measure 5-run median wall on real
shapes before designing*.

## Hypothesis

Lance beats Parquet on the **scattered random-access** retrieval pattern, and is
roughly at parity on full-scan throughput. If true, the win lands specifically on
the ANN sub-blocking path and any "gather these candidate row-ids" step; it does
*not* help the streaming full-scan stages (load, transform), which are already
Parquet-friendly.

## The two retrieval patterns that matter

GoldenMatch reads its working set back off disk in two shapes:

1. **Block-key predicate retrieval.** `core/blocker.py:283` groups by
   `__block_key__`; downstream, scoring materializes the rows of a block. On disk
   this is `WHERE __block_key__ = X`. Parquet serves it via row-group min/max
   stats + (optionally) a sorted/partitioned layout; it is already reasonable when
   the file is sorted on the block key. Lance serves it via its scanner with a
   pushdown filter. **Expectation: near parity** when Parquet is sorted on the key;
   Lance may win on unsorted data because its secondary structures localize better.

2. **Scattered index `take`.** The ANN / sub-blocking path
   (`core/blocker.py:486`, `..._ann_{min(member_list)}`) produces candidate row
   *indices* that are non-contiguous across the file. Today the realistic status
   quo is "read the needed column(s) for the whole partition, then gather"
   (`pl.read_parquet(...)[indices]`) — Parquet has no true random row access; you
   pay a full column scan to fetch a 0.1% sample. Lance's `dataset.take(indices)`
   reads only the pages covering those rows. **Expectation: Lance wins large** as
   the candidate fraction shrinks (e.g. 0.01–1% of N), because Parquet's cost is
   ~O(N) regardless of how few rows you want while Lance's is ~O(rows_wanted).

## Experiment

`packages/python/goldenmatch/scripts/bench_lance_vs_parquet.py` — standalone (no
`goldenmatch` import), graceful when `lance` is absent (runs Parquet-only and
prints install hint). It:

1. Generates `--rows N` synthetic records with a realistic shape: surrogate `id`,
   a bounded-cardinality `block_key` (zip-like), two text fields (name/address),
   and a couple of numerics — i.e. the columns scoring actually touches.
2. Writes both `dataset.parquet` (sorted on `block_key`, the fair Parquet layout)
   and `dataset.lance`.
3. Times three patterns at **5-run median wall**, each in a forked child so peak
   RSS (`ru_maxrss`) is isolated from setup and from the other engine:
   - `full_scan` — read the scoring columns end-to-end (baseline / sanity).
   - `block_filter` — predicate-retrieve one randomly chosen block.
   - `scatter_take` — gather `--candidates K` non-contiguous row indices (the ANN
     pattern); swept across K = 0.01%, 0.1%, 1% of N.
4. Prints a table: pattern x engine -> median wall, x-factor, peak RSS.

Run shapes (the bench box, not CI): `N ∈ {1M, 10M, 50M}`,
`K/N ∈ {1e-4, 1e-3, 1e-2}`.

```
python packages/python/goldenmatch/scripts/bench_lance_vs_parquet.py \
    --rows 10_000_000 --candidates-frac 0.001 --runs 5
```

## Decision criteria

Adopt Lance for candidate retrieval **only if** `scatter_take` shows a
**>= 5x median-wall win at K/N <= 1e-3** on >= 10M rows *and* `full_scan` /
`block_filter` are within ~1.2x of Parquet (no regression on the streaming
stages). Below 5x it is not worth a new on-disk format, a second writer in the
loader, and the Ray-side read-path changes.

If adopted, the integration is **narrow and opt-in**: a Lance writer alongside the
existing Parquet writer in `core/ingest.py` / the distributed loader, gated by a
config/env flag, feeding only the ANN-gather step. We do **not** replace Parquet
as the interchange format (Ray, object-storage connectors, and the bench release
assets all stay Parquet).

## Results (measured 2026-06-13)

Run on a fresh sandbox: polars 1.41, pyarrow 24, lance 7.0, numpy 2.4. Two
methodology fixes landed before trusting numbers: **(a)** a BTREE scalar index on
`block_key` for the Lance `block_filter` (the first pass handicapped Lance with no
index against sorted-Parquet); **(b)** peak RSS via `/proc/self/status` `VmHWM`
instead of `resource.ru_maxrss` — a spawned child inherits the parent's
`ru_maxrss` high-water mark, so the first pass reported an identical (bogus)
3560 MB for every pattern. Both are in `scripts/bench_lance_vs_parquet.py`.

**Bench, N = 10M, 5-run median wall (x = Lance vs Parquet, RSS = VmHWM peak):**

| Pattern | Parquet | Lance | x-factor | Parquet RSS | Lance RSS |
|---|---:|---:|---|---:|---:|
| `full_scan` | 267 ms | 674 ms | 0.4x (slower) | 1379 MB | 1497 MB |
| `block_filter` (no index) | 4.3 ms | 159 ms | 0.03x | 78 MB | 379 MB |
| `block_filter` (BTREE idx) | 4.3 ms | **8.2 ms** | 0.5x | 78 MB | 190 MB |
| `scatter_take` K/N=1e-5 (K=100) | 248 ms | **9.1 ms** | **27.3x** | 1381 MB | **178 MB** |
| `scatter_take` K/N=1e-4 (K=1k) | 239 ms | **41 ms** | **5.8x** | 1362 MB | 192 MB |
| `scatter_take` K/N=1e-3 (K=10k) | 246 ms | 186 ms | 1.3x | 1375 MB | 321 MB |
| `scatter_take` K/N=1e-2 (K=100k) | 265 ms | 482 ms | 0.5x | 1374 MB | 686 MB |

Write/disk: Parquet 1.2 s / 183 MB; Lance 1.2 s / 412 MB (**2.3x larger**); BTREE
index build 1.7 s.

Two findings the fixes surfaced: the scalar index turns `block_filter` from ~37x
slower into a single-digit-ms near-tie (the earlier "disaster" was the missing
index); and on sparse gathers Lance uses **~8x less memory** (178 MB vs 1381 MB at
K/N=1e-5) because Parquet must scan the whole column to gather a handful of rows
while Lance reads only the covering pages. Lance's `scatter_take` advantage grows
without bound as the gather gets sparser (1.3x → 5.8x → 27x as K/N → 1e-5).

**Real gather sparsity (`scripts/measure_ann_gather_sparsity.py`, auto-configured
blocker on `realistic_person_df`):** member-weighted median K/N = **3e-5 at 100K
rows, 3e-6 at 1M** — both deep in Lance's win zone. At 100K (a coarse `first_name`
pass creates a tail: p99=70, max=419) the gathered-row split is win 50% /
marginal 43% / loss 7%; at 1M the synthetic 3-record identities give uniform
3-row blocks (100% win). Caveat: synthetic identities understate the real
heavy-tail — production surname/zip skew would push more gathered rows toward the
loss zone.

### Verdict — the gate splits by access pattern

The original gate (`scatter_take >= 5x at K/N <= 1e-3` **and** `full_scan` /
`block_filter` within 1.2x) is **not met for the batch dedup pipeline**:
`full_scan` is 0.4x and Lance writes 2.3x more disk. Crucially, batch dedup
*eventually gathers every row* (every block is scored), so cumulatively it is a
full scan — Lance's per-row-address `take` cannot help, and its bigger files +
slower scan make it a net loss. **Do not put Lance on the batch pipeline.**

But the same numbers make Lance a **strong fit for the sparse one-shot retrieval
path** — `core/match_one.py`, `core/streaming.py`, the `incremental` CLI, and the
Postgres ANN-index path — where you hold a large base on disk and fetch only the
ANN candidate rows per query (K/N ≈ 1e-5..1e-4 in the measured blocking): **6–27x
faster and ~8x less memory**, with `full_scan` simply never on the critical path
there. That is a different, smaller, opt-in integration than the spec first
scoped (a Lance-backed base store for incremental matching, not a Lance writer in
the batch loader).

**Recommendation:** reject Lance for batch dedup; open a focused follow-up spike
for a Lance-backed base store behind `match_one`/`streaming`/`incremental`,
measured on a real skewed dataset (not synthetic 3-record identities) before any
adoption.

## Risks / unknowns

- **Format lock-in & ops.** Lance is younger than Parquet; the bench-dataset
  release assets and `connectors/object_storage.py` (`scan_parquet`) assume
  Parquet. Keep Parquet as the portable interchange; Lance stays an internal
  acceleration cache.
- **Ray integration.** `distributed/` reads via `ray.data.read_parquet`. Lance has
  a Ray reader but it is a separate code path to validate; out of scope for the
  first spike (single-node only).
- **Sorted-Parquet is a strong baseline.** If we sort Parquet on `block_key` and
  the real access is predicate-only (pattern 1), Lance's edge may be marginal —
  which is exactly why pattern 2 (scatter `take`) is the load-bearing measurement.
- **Apples-to-apples writes.** Lance write time and on-disk size must be reported
  too; a read win that costs 3x write time / 2x disk may not pay off for one-shot
  pipelines.

## Non-goals

- No production adoption in this spike — numbers first.
- No change to the Parquet-based distributed path or release assets.
- Not a general "Parquet is slow" claim — this targets one access pattern.
