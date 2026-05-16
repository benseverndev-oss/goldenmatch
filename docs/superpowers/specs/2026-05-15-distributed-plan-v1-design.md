# GoldenMatch Distributed Plan v1 — architecture for 50M+ records

**Status:** Design (drafted 2026-05-15)
**Author:** Claude + bsevern, brainstorm from the 50M readiness conversation
**Scope:** A new execution plan, selected by the [controller v3 planner](2026-05-15-controller-v3-planner-design.md) at scale, that replaces single-process Polars-direct with a partitioned, spillable, distributed pipeline. Architectural; implementation is a multi-PR arc, not a single change.
**Related:**
- [`2026-05-15-controller-v3-planner-design.md`](2026-05-15-controller-v3-planner-design.md) — the planner that selects this plan vs the simpler ones.
- [`2026-05-15-map-elements-attack-design.md`](2026-05-15-map-elements-attack-design.md) — single-process optimizations that take us to 10M.
- PRs #233/#234/#235: chunked backend foundations + block-keyed cross-chunk index + score_blocks_duckdb out-of-core pair store. **These are partial implementations of the components here.**
- `packages/python/goldenmatch/goldenmatch/backends/ray_backend.py`: prior-art distributed scoring backend. v1 builds on it.
- 5M audit (CLAUDE.md): the chunked + explicit-personlike combo took 50 min for 5M on ubuntu-latest (4c/16GB). 50M on the same hardware shape would be 100x compute + bigger memory pressure than chunking can paper over. **Architectural change required.**

## Problem

GoldenMatch's current scale ceiling is 5-10M with the chunked backend + explicit blocking. The single-process pipeline has these hard limits:

1. **Pair generation.** O(sum(block_size²)) per blocking pass. At 50M with even tight blocking, candidate pairs grow into the billions.
2. **Pair storage.** All pairs currently land in a Python list/set in memory before clustering. PR #235 added `score_blocks_duckdb` as an out-of-core pair store, but the in-memory polars-direct path is unchanged.
3. **Clustering.** `core/cluster.py::build_clusters` uses a Python `UnionFind` over the **full** pair set in one process. At 50M with 100M+ pairs, this single-driver step OOMs before it starts.
4. **No partitioning.** All workers in `score_blocks_parallel` operate on the same parent DataFrame. There's no "this worker owns these blocks; this other worker owns those" boundary.
5. **No retry / no failure isolation.** A single block's scoring error fails the whole pipeline.

The intuition from PR #239's improvements: "make Python faster" pays off cleanly up to 10M, then the asymptote bends. **50M is a distributed graph processing problem wearing a fuzzy-matching jacket.**

## Goals

1. **`gm.dedupe_df(big_df)` zero-config at 50M+** without manual backend selection. Planner picks Distributed Plan automatically per [controller v3 Rule 6](2026-05-15-controller-v3-planner-design.md).
2. **No correctness regression vs single-process.** Same clusters (modulo ordering), same F1, same lineage.
3. **Bounded peak memory per worker.** Distributed plan must run on commodity hardware (a few dozen GB per worker), not require fat instances.
4. **Observability.** Per-partition stage timings, pair counts, spill volumes, and clustering merge boundary stats land on `PostflightReport`.
5. **Quality gate.** Every released scale tier (10M, 25M, 50M, ...) verified via `eval-er-evaluation` pairwise + B-cubed + cluster F1 within bootstrap noise of the 100K baseline.

## Non-goals (v1)

- Cross-data-center sharding. Single-cluster only.
- Replicated scoring (running each block on multiple workers for fault tolerance). Workers retry on failure; replication is v2.
- GPU scoring. The rapidfuzz CPU path is well-served by parallelism alone at the rec/s rates we need.
- Spark backend. Documented as a future enterprise/Databricks lane; not the zero-config default. Different code path, different team.
- Streaming live ingestion. The plan operates on a bounded input frame, not a Kafka topic.

## The six architectural components

This is the load-bearing list. Five are well-trodden; component #5 is the hard one and the one most likely to determine v1's success.

### Component 1 — Prepared-record store

Where: new module `goldenmatch/distributed/record_store.py` (or extension of `backends/score_duckdb.py`).

**Function:** materialize the post-`compute_matchkeys` + post-`precompute_matchkey_transforms` DataFrame once to a partitioned Arrow/Parquet store on disk. All downstream stages read from this store; the raw input frame is forgotten.

**Why it matters:** the controller iterates 5x. Today each iteration re-transforms the (sampled) data. At 50M scale, even one transform pass is the bottleneck if it touches every row. Persisting prepared records eliminates the re-do.

**Choice point:** Arrow IPC files vs Parquet vs DuckDB tables.
- **Arrow IPC:** zero-copy load via mmap. Fast for repeated full-frame access. Polars + DuckDB both consume natively.
- **Parquet:** more compact (compressed), but read costs decompression on every access.
- **DuckDB tables:** the data plane handles spill/parallelism natively; SQL-queryable; pairs naturally with component #2.

**Recommendation:** DuckDB tables for the prepared-record store. Already validated in PR #235's `score_blocks_duckdb`. The data plane becomes "DuckDB owns records and pairs; Python owns scoring UDFs and clustering logic." Mirrors Splink.

### Component 2 — Block-key partitioned execution

Where: extension to `core/blocker.py` + new `goldenmatch/distributed/partitioner.py`.

**Function:** instead of treating blocks as units of work passed to a thread pool over a shared parent DataFrame, *partition* the prepared-record store by block key. Each partition becomes the unit of work for one worker. Workers operate on their own slice; no shared global frame.

**Implementation:** `df.partition_by("__block_key__", ...)` in Polars, or `PARTITION BY` in DuckDB. The chunked backend (#233) already has the partitioning primitive; v1 promotes it to first-class.

**Why it matters:** removes the shared-state coordination overhead in `score_blocks_parallel`. Workers can be on different machines. Boundary edges (pairs whose two records are in different block-key partitions) require handling — that's component #5.

### Component 3 — Distributed scoring

Where: extension of `backends/ray_backend.py`, possibly renamed `goldenmatch/distributed/scorer.py`.

**Function:** each worker scores its assigned partition's blocks. rapidfuzz is Python/Rust native, releases the GIL, parallelizes well via Ray remote tasks. The existing Ray backend already does block-level distribution; v1 extends it to operate on the prepared-record store from component #1 rather than the in-memory parent frame.

**Existing prior art:** `score_blocks_ray` in `backends/ray_backend.py`. Tested on small block counts; falls back to the threaded scorer below ~4 blocks per CLAUDE.md.

**Why it matters:** parallelism by worker count rather than by thread pool size. Scales with cluster.

### Component 4 — Streaming pair store

Where: extension of PR #235's DuckDB pair store, or new `goldenmatch/distributed/pair_store.py`.

**Function:** scored pairs land in a partitioned on-disk store (DuckDB table or Parquet, partitioned by `__block_key__` of one of the records). The store accepts streaming writes from workers and supports range/key reads for the clustering step.

**Why it matters:** at 50M+ the pair count blows past Python-process memory. Pairs must spill. DuckDB's INSERT + Arrow bulk import (proven in PR #235) is the right tool.

**Schema:**
```sql
CREATE TABLE pairs (
    record_a_id BIGINT,
    record_b_id BIGINT,
    block_key TEXT,    -- partition column
    score DOUBLE,
    matchkey_name TEXT
);
CREATE INDEX pairs_a ON pairs (record_a_id);
CREATE INDEX pairs_b ON pairs (record_b_id);
```

### Component 5 — Distributed clustering (the hard one)

**This is THE architectural decision.** Everything else is solved patterns; clustering at distributed scale is where v1 lives or dies.

Three viable approaches:

#### Approach A — Boundary-aware per-partition union-find (recommended for v1)

1. Each worker runs `UnionFind` over the pairs in its partition. Produces local clusters keyed by partition.
2. Boundary edges (pairs where the two records' canonical partition assignments differ) get written to a dedicated `boundary_pairs` table.
3. A single coordination pass (driver-side, but operating only on boundary pairs — orders of magnitude smaller) merges clusters that share boundary records.

**Pros:**
- 95% of clustering work is partition-local and parallel.
- Driver pass handles only boundary edges; size is bounded by blocking coverage, not total pair count.
- No iterative messaging framework needed.

**Cons:**
- Correctness depends on **the boundary-edge tracking layer being complete** — every cross-partition pair must be captured. A bug there silently splits clusters.
- Worst case: very coarse blocking produces many boundary pairs; degrades toward Approach B.

**Pre-existing foundation:** PR #234's block-keyed cross-chunk index lookup is already a primitive form of this for the chunked backend. v1 generalizes the pattern.

#### Approach B — GraphX / Pregel-style iterative messaging

Workers exchange messages until cluster IDs converge. Standard Spark/GraphX pattern. Heavy. Requires actual cluster orchestration (Ray is borderline; Spark is natural).

**Verdict for v1:** rejected. Drags in Spark or hand-rolled iterative coordination on Ray. Doesn't pay back at our scale tier.

#### Approach C — DuckDB recursive CTE for connected components

DuckDB 0.10+ supports recursive CTEs. A single SQL query could compute CC over the pair table.

**Pros:** simple to write, data plane handles everything.
**Cons:** untested at 100M+ rows. DuckDB's CTE optimizer may not handle the join volume gracefully. Worth a 1M+ smoke test, but not the v1 default.

**Decision:** Approach A (boundary-aware per-partition union-find) is the v1 plan. Approach C is a parallel experiment worth tracking as a possibly-simpler v2 alternative.

### Component 6 — Planner integration

Where: [`controller v3`](2026-05-15-controller-v3-planner-design.md) Rule 6.

**Function:** the planner selects Distributed Plan when `n_rows >= 50_000_000` AND `ray_available`. Falls back to the DuckDB single-box plan (Rule 5) if Ray isn't installed or fails to initialize.

**Knobs the plan exposes to the planner:**
- `n_partitions` (= number of unique block keys, capped at `cluster_total_cores * 4`)
- `pair_spill_path` (where the DuckDB pair store lives)
- `max_workers` (Ray actor count)
- `clustering_strategy = "partitioned_union_find"`

The plan is invisible to the user. `gm.dedupe_df(big_df)` is the only API surface.

## Failure modes and recovery

| Failure | Detection | Recovery |
|---|---|---|
| Worker dies mid-scoring | Ray task heartbeat timeout | Retry the partition on another worker. Idempotent because each partition writes to its own pair-store key range. |
| DuckDB pair store full | INSERT raises | Surface `OutOfStorageError` immediately. Disk overflow is a hardware problem, not a software one — fail loudly with the spill path and disk free for the user to act on. |
| Boundary-edge merge produces giant cluster | Driver-side check: post-merge, any cluster with `size > 0.1 * n_rows` is suspect | Run the existing `split_oversized_cluster` MST-cut routine per oversized cluster. Logged + surfaced on `PostflightReport`. |
| One partition has pathological skew (one block has 90% of rows) | Pre-scoring: `block_sizes_p99 / block_sizes_p50 > 50` per partition | Apply `_auto_split_block` (PR #239) before scoring. Auto-splits the hot block into sub-blocks via a secondary blocking key. |
| Network partition splits the Ray cluster | Ray scheduler raises | Fail the run. v1 doesn't do partial-cluster degradation. |

## Quality gate

Every scale-tier milestone (10M, 25M, 50M, 100M) must pass:

1. **er-evaluation pairwise F1** within bootstrap noise of the 100K Febrl3 baseline (currently 0.9097 ± 0.002).
2. **er-evaluation B-cubed F1** within bootstrap noise (currently 0.9935 ± 0.0001).
3. **er-evaluation cluster F1** within bootstrap noise (currently 0.9647 ± 0.001).
4. **Internal scale-audit F1** within ±0.005 of the comparable tier on chunked + explicit-personlike (the current 5M audit shape).
5. **Wall time** recorded. Not gated for the first milestone hit. Target curves added in subsequent revisions.
6. **Peak RSS per worker** recorded. Distributed plan's whole point is to keep this bounded.

No "passes" without all five quality numbers.

## Implementation arc

Spec → multiple PRs over multiple sessions. Sequencing:

1. **Component 1** (prepared-record store, DuckDB-backed). 1-2 PRs. Lands the persistence layer. **Gate:** existing 5M chunked audit reads from prepared store instead of re-transforming. Wall ≤ existing 50 min.
2. **Component 4** (streaming pair store). 1 PR. Extends PR #235 with the partition-by-block-key column + indexes. **Gate:** 5M audit with on-disk pair store. RSS reduction visible.
3. **Component 2** (block-key partitioning). 1-2 PRs. Threads partition-by through blocker + scorer. **Gate:** 5M with parallel partitions on the threaded backend (no Ray yet). Wall ≤ existing.
4. **Component 5A** (boundary-aware per-partition union-find). The hard one. 2-3 PRs. **Gate:** 5M end-to-end same F1 as chunked baseline. **This is the load-bearing milestone for the architecture.**
5. **Component 3** (distributed scoring on Ray). 1-2 PRs. Migrates the partition-aware scorer to Ray remote tasks. **Gate:** 10M zero-config on a 2-node Ray cluster.
6. **Component 6** (planner integration). 1 PR. Controller v3 Rule 6 activates. **Gate:** `gm.dedupe_df(df)` zero-config at 10M auto-selects Distributed Plan, same F1 as chunked-explicit at the same N.
7. **Milestone runs.** 25M, 50M, 100M with the quality gate. Each is its own run + spec amendment with the numbers.

Estimated effort: components 1-4 are 2-3 weeks of focused work each; component 5 is the open-ended one (could be 4-8 weeks depending on what boundary-edge tracking turns into).

## Open architectural questions

1. **Worker pool sizing.** Auto-scale or fixed? v1 picks a fixed count based on the planner's signal; v2 could auto-scale based on per-partition wall time observed mid-run.
2. **Pair store sharing across runs.** If the user calls `dedupe_df` on overlapping fixtures, can the pair store cache hits? Probably yes for v2; out of scope for v1 (every call starts with an empty store).
3. **Backpressure on the scoring stage.** If workers produce pairs faster than the pair store can ingest, RAM grows. v1 uses bounded queues; v2 could use credit-based flow control.
4. **Ray vs Dask vs raw `concurrent.futures.ProcessPoolExecutor`.** Ray is the v1 pick (CLAUDE.md cites the existing `backends/ray_backend.py` as 50M-targeted). Dask is a serious alternative if the team finds Ray ops-heavy. Raw multiprocessing fails on the cross-worker memory sharing required for the boundary-edge step.
5. **DuckDB version pinning.** Recursive CTE behavior changes across DuckDB versions. The chosen pair-store + cluster approach must pin a tested DuckDB version range.

## Acceptance criteria

- v1 ships when:
  1. Components 1-6 implemented and individually tested.
  2. `gm.dedupe_df(df)` zero-config at 10M produces the same F1 as the explicit chunked baseline (within ±0.005 internal F1; within bootstrap noise on er-evaluation).
  3. 50M zero-config run completes on a Ray cluster of size ≥ 4 workers × 16 cores. Quality gate (er-evaluation Tier 1) passes. Wall + RSS + spill volume recorded.
  4. `PostflightReport.execution_plan` reflects the auto-selected Distributed Plan.
  5. Documentation in `packages/python/goldenmatch/docs/scale-100m-ray-vs-spark.md` (already present, drafted in PR #239) updated with the realized numbers.

## What this spec is NOT promising

- That 50M will be cheap. It will not. Distributed plans cost cluster time.
- That the chunked backend goes away. It's the right choice up to ~10M on a single big box and stays the planner's choice in that range.
- That this lands by any specific date. It's a multi-month arc; the milestone is 10M zero-config first, then everything else.
- That Spark won't matter eventually. It will for Databricks customers. Not the v1 default; documented as a future lane.

The whole point of this spec is: **stop trying to make 50M happen via Polars optimization, design the new path explicitly, then ship it.**
