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
