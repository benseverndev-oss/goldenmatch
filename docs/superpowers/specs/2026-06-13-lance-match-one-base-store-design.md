# Lance-backed base store for incremental / match_one — Spike Design + Results

**Date:** 2026-06-13 · **Branch:** claude/lance-match-one-spike · **Status:** spike (results in; pre-plan)

## Origin

Follow-up to `2026-06-13-lance-vs-parquet-candidate-retrieval-design.md`. That
batch bake-off split by access pattern: **reject Lance for batch dedup** (it
eventually full-scans every row, and writes 2.3x more disk), but Lance won big on
**sparse one-shot gathers** (5.8x at K/N=1e-4, 27x at 1e-5, ~8x less memory). The
only GoldenMatch path that is *purely* sparse one-shot gathers is
incremental / streaming / `match_one`: `core/match_one.py` queries an ANN index
for top-K candidates per probe and scores them — and today it holds the **whole
base in memory** (`_match_one_ann` does `rows[faiss_idx]`), so it is RAM-bound.

Question: can a Lance-backed base store serve per-probe candidate retrieval from
**disk** fast enough to remove the in-RAM-base constraint?

## Experiment

`packages/python/goldenmatch/scripts/bench_match_one_lance.py` — standalone,
graceful without `lance`. Models the per-probe candidate gather against a large
**skewed** base (Zipfian block sizes — the realism the prior synthetic 3-record
data lacked) across three base stores: `memory` (status quo — full base in a
polars frame), `parquet` (on disk), `lance` (on disk, BTREE scalar index on
`block_key`). Two candidate sources (`ann` = top-K scattered row-ids per probe;
`block` = all rows sharing a key) × two regimes (`stream` = one probe at a time,
the real match_one latency; `microbatch`). Per-probe median latency + peak RSS
(VmHWM, isolated per store in a spawned child).

## Results (measured 2026-06-13, 10M-row base, polars 1.41 / lance 7.0)

Base skew: block size p50=1, p99=235, **max=3.83M**, 64,162 blocks. On disk:
Parquet 165 MB, Lance 390 MB (2.3x) + BTREE index 1.7 s.

| shape / regime | memory | parquet | lance |
|---|---|---|---|
| **ann stream** (match_one latency) | 0.1 ms/p · **1233 MB** | 240 ms/p · 1367 MB | **3.6 ms/p · 182 MB** |
| block stream | 14.1 ms/p · 1234 MB | 3.4 ms/p · 94 MB | **2.3 ms/p · 190 MB** |
| ann microbatch | 1.6 ms/p · 1231 MB | 316 ms/p · 1235 MB | 101 ms/p · 205 MB |

**Read:**

1. **ANN streaming — the load-bearing case — Lance wins decisively as a base
   store.** It serves a per-probe top-50 gather in **3.6 ms holding 182 MB**,
   versus the in-memory store's 0.1 ms but **1233 MB** (the whole base). Both
   latencies are fine for streaming/interactive; the difference is **6.8x less
   memory**. Parquet-on-disk is a non-starter for streaming (240 ms/probe — no
   random access, it re-reads the whole column every probe).
2. **Exact-block retrieval — Lance's BTREE index beats BOTH** (2.3 ms) the
   in-memory polars `filter` (14.1 ms — an unindexed full-column scan) and sorted
   Parquet (3.4 ms). (Caveat: a real in-memory impl would keep a hash index,
   which would beat 14 ms — so this flatters Lance vs a *naive* memory filter. The
   ANN comparison is the clean one.)
3. Lance disk is 2.3x larger; the ANN candidate model is random scatter (FAISS
   neighbors scatter similarly, so representative).

## Verdict

**The spike confirms the hypothesis.** Lance is a genuine fit for a base store
behind `match_one` / `streaming` / `incremental`, with the win concentrated in the
**out-of-core regime**: when the base exceeds worker RAM (or you run many
concurrent matchers and want a small per-worker footprint), Lance delivers
low-single-digit-ms per-probe candidate retrieval at ~7x less memory than holding
the base in a frame — which `match_one` cannot do today at all.

**When NOT to use it:** if the base comfortably fits in RAM and there is a single
matcher, the in-memory store's 0.1 ms ANN gather is unbeatable — keep it the
default. Lance is the *opt-in, large-base* path, not a replacement.

## Proposed integration (for the plan, not this spike)

- A `LanceBaseStore` behind a small retrieval interface (`gather_candidates(ids)`,
  `gather_block(key)`), opt-in via config/env, consumed by `_match_one_ann` and
  the `incremental` CLI / `StreamProcessor`. `match_one` takes the store instead
  of an in-RAM `rows`/`row_ids` list.
- Build the Lance dataset + BTREE `block_key` index once at base-ingest; reuse the
  ANN index (FAISS) alongside it. Keep Parquet as the interchange format; Lance is
  an internal acceleration store for the base only.
- Gate behind a base-size threshold so fits-in-RAM stays on the in-memory path.

## Risks / unknowns

- **Skew tail.** max block = 3.8M rows here; a `block` gather on the giant key
  returns millions of rows regardless of store — blocking-key choice still matters
  (the existing scale-invariant-blocking work governs that).
- **FAISS + base coupling.** The ANN index and the Lance base must stay
  row-id-consistent across base updates; incremental inserts touch both.
- **Real dataset.** Numbers are on synthetic skew; validate on a real
  large base (NCVR-scale) before committing the integration.
- **Disk + write cost** (2.3x size, index build) is paid once per base; amortized
  over many incremental matches, but real for churny bases.

## Non-goals

- No production integration in this spike — numbers first (now in).
- No change to the batch pipeline or the Parquet interchange/release assets.
- Not a replacement for the in-memory path when the base fits in RAM.
