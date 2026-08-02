# DuckDB-free out-of-core FS: spillable edge stream + external WCC

**Status:** design + phase-1 implementation
**Predecessor:** `2026-07-20-fs-frame-residency-bucket-streaming-design.md` (the DuckDB-spilled `run_fs_dedupe_streaming` + the in-RAM `run_fs_dedupe_sequential`).

## Problem

There are two single-box out-of-core-ish FS paths today, and **both hold the entire
emitted-pair table in RAM before clustering**:

- `run_fs_dedupe_streaming` (DuckDB): spills the *frame* to a DuckDB file and streams
  blocks, but `score_fs_out_of_core(emit="arrow")` accumulates one `PAIR_STREAM` table
  per wave into `pair_tables` and concatenates; `_cluster_arrow_native` then dedups +
  runs `build_clusters_arrow_native` over the **whole** table. Needs DuckDB.
- `run_fs_dedupe_sequential` (in-RAM): holds the frame resident AND the full pair table.
  Its own docstring calls it "the fast path for frames that FIT in RAM" — not out-of-core.

So the only genuine out-of-core path is DuckDB-spilled, and even it holds all edges in
RAM (~20 B/pair Arrow; ~1.3 GB at 66M pairs, but super-linear in dup rate / loose
blocking / ≥100M). There is **no DuckDB-free out-of-core path**, and **no path with a
bounded edge term.**

## Key insight

**WCC state is O(N records), not O(edges).** A union-find parent array is bounded by the
row count; edges can be applied one at a time and then discarded. Two corollaries:

1. **Edges can be spilled to disk** as they are scored (bounded write buffer) and read
   back one shard at a time for clustering — the full pair table never lives in RAM.
2. **The global max-score dedup can be skipped entirely.** It exists only to shrink the
   in-RAM pair table before the in-RAM WCC; union-find membership is invariant to which
   duplicate survives. With streaming union we apply every edge as it arrives.
   - The `link_threshold` filter stays correct under streaming: filter each edge
     (`score >= link_threshold`) *before* union. A pair scored 0.4 in pass A and 0.6 in
     pass B unions when the 0.6 edge arrives — i.e. iff **any** occurrence clears the cut,
     which equals "best cross-pass score >= cut" because `max` is monotone. This is
     byte-equivalent to the current filter-after-max-dedup.

Net: an **external union-find** streaming spilled edge shards over an O(N) parent array is
memory O(N records) + streamed I/O, with **no full edge table in RAM** and **no DuckDB.**

## Design

Three bounded mechanisms, mirroring the predecessor's A/B/C decomposition:

### (A) Spillable edge producer
Score blocks in bounded waves (the existing `score_fs_out_of_core` / sequential wave
loop), and instead of appending each wave's `PAIR_STREAM` `pa.Table` to an in-RAM list,
**write it to an Arrow IPC shard on disk** (`edges-<pass>-<wave>.arrow`) via a bounded
`RecordBatchFileWriter`. The resident scoring working set is unchanged (one wave of
gathered blocks); the edge term drops from "sum of all emitted pairs" to "one wave's
pairs + the OS write buffer".

### (B) External WCC over spilled shards — `external_wcc_from_shards`
Stream the shard files one at a time; for each `(id_a, id_b, score)` with
`score >= link_threshold`, `union(id_a, id_b)` over a union-find keyed by row id (only ids
that appear in an edge get an entry — bounded by `min(2·edges, N)`). After all shards,
fold in the singleton `all_ids` and assign a stable `__cluster_id__` per component.
Returns the same `(assignments pa.Table {__row_id__, __cluster_id__}, n_pairs)` shape
`_cluster_arrow_native` returns, so the existing `_stream_fs_dedupe_output_arrow` output
streamer consumes it unchanged.

*Phase 1* uses the Python `core.cluster.UnionFind` (dict-backed) — correctness-first,
proves the bounded-edge architecture. *Phase 2* kernelizes it: a stateful native
`FsExternalWcc(n_rows)` with `.union_arrow(shard)` (applies a shard's edges over an O(N)
`Vec<i64>` parent array in Rust) + `.finalize() -> assignments` — array UF is ~10× tighter
than the dict and keeps the per-edge union off the Python interpreter at 66M+ edges.

### (C) End-to-end — `run_fs_dedupe_spill`
`prep frame → score in waves (spill shards) → external_wcc_from_shards → stream O(N)
output` (the existing pure-pyarrow `_stream_fs_dedupe_output_arrow`). DuckDB-free
(no `duckdb.connect`), polars-free except the bounded golden builder. Routed via a new
`resolve_fs_block_source()` value **`spill`** (alongside `eager`/`frame`/`duckdb`),
reachable through `gm.dedupe_to_parquet(*files, out_dir=…)`.

**Remaining after phase 1** (staged, not in the first slice):
- Frame-residency streaming during prep (input parquet → arrow batches → score without a
  single resident frame) — the predecessor's open "load-peak below ~1× frame" item. The
  spill path keeps the frame resident for now; the edge term is what this phase unbinds.
- The native `FsExternalWcc` kernel (phase 2).
- CI proof that a shape whose *edge* count OOMs the in-RAM/DuckDB pair table completes
  under `spill`.

## Correctness gate

`external_wcc_from_shards` is parity-gated against `_cluster_arrow_native` on the SAME
`PAIR_STREAM` table (partitioned into shards): identical component partition + identical
`link_threshold` semantics, on adversarial shapes (a pair split across shards with
different scores straddling the threshold; chains split across shards; singletons folded
from `all_ids`). Because clustering is a pure function of the edge set, this is an exact
partition-equality gate, not a fuzzy one.

## Why not just reuse the distributed WCC
`distributed/clustering.two_phase_wcc` / `distributed_wcc` are Ray-based (collect-to-driver
or Ray-streaming-executor bound) — a different substrate. This is a single-box, disk-spill
external UF: no Ray, no cluster, thesis-aligned (Arrow/Rust, DuckDB-free).
