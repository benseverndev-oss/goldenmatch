# Scaling GoldenMatch to 100M+ Records: Ray vs Spark

> **✅ VERIFIED 2026-06-04 — 100M completes on Ray, distributed, driver 0.30 GB peak.**
> The distributed Phase-5 pipeline (`GOLDENMATCH_DISTRIBUTED_PIPELINE=2`) ran a full
> **100,000,000-row** dedupe on a 4-worker Ray cluster (`e2-standard-16`, 64 worker CPU)
> in **213 s wall**, producing **20,000,000 golden records** (exact, clean clustering),
> with the driver/client process peaking at **0.30 GB RSS** and the head node flat at ~5 GB
> the entire run. Output (2.4 GiB) was written distributed straight to object storage.
>
> The decision below ("ship Ray") held. The thing that actually unlocked 100M was **not**
> distributing more compute — it was removing every **driver-side `collect`/`take_all`** from
> the pipeline so nothing funnels back to a single node. The winning shape is
> `score → local-CC → join → golden → write`, every stage distributed, **zero driver collect**:
> per-partition scoring; connected components via a single per-partition local Union-Find
> (`local_cc_assignments` — components never span partitions, so no cross-node merge is needed);
> the row→cluster annotation as a distributed `Dataset.join`; golden built and **written**
> distributed (`build_golden_records_distributed(...).write_parquet`), never materialized on the
> driver. Run the head as a pure driver (`ray start --num-cpus=0`) so shuffle data never lands on it.
> See `scripts/bench_phase5_explicit.py` for the exact assembly and `goldenmatch/distributed/` for
> the implementation. Everything below is the original 2026-05-15 design evaluation, preserved.

---

Evaluation prepared 2026-05-15 against `main` post-PRs #233/#234/#235 (the 5M scale audit).

## TL;DR

**Ship Ray, not Spark.** Ray is already in-tree, language-native, and falls cleanly into the existing pipeline. Spark only earns its keep when (a) the source data already lives in a Spark-native lake (Delta, Iceberg, Snowflake-via-Spark-Connect) AND (b) the target scale is 1B+, not 100M. At 100M the bottleneck is not the per-block scoring loop that Ray/Spark would distribute — it's the **single-process chunked driver** in `core/chunked.py`. Until that's partitioned, neither backend will get below ~8h on 100M; once it is, Ray on 4–8 nodes gets us under 2h with a fraction of Spark's operational tax.

Recommended sequence:

1. Land an honest 100M synthetic benchmark on the existing `backend="ray"` + `backend="chunked"` stack. Don't speculate — measure.
2. Partition the chunked driver: turn `_match_against_index` and `_add_to_index` into Ray actors keyed by block-key prefix. This is where the real lift comes from at 100M; Spark would force the same refactor.
3. Re-benchmark. Decide on Spark only if 100M still doesn't fit on a 4–8 node Ray cluster and the org already runs Spark.

---

## What "100M+ records" actually means

Concrete numbers, working from the 5M data point:

| Tier   | Records | Verified? | Wall (best config)     | Peak RSS | Notes                                  |
| ------ | ------- | --------- | ---------------------- | -------- | -------------------------------------- |
| t-1M   | 1M      | yes       | ~43 min, polars-direct | ~10 GB   | 4c/16GB CI runner                      |
| t-5M   | 5M      | yes       | ~50 min, chunked       | ~12 GB   | 4c/16GB CI runner                      |
| t-50M  | 50M     | yes       | 295 s, distributed Ray | 2.8 GB driver | 4-worker e2-standard-16 cluster   |
| t-100M | 100M    | **yes**   | **213 s, distributed Ray** | **0.30 GB driver** | 4-worker e2-standard-16; 20M golden records |
| t-1B   | 1B      | no        | n/a                    | n/a      | Splink/Spark territory                 |

The 5M run is the load-bearing number. Linear extrapolation says 100M is 20× — but the chunked path's cross-chunk matching is O(chunks × index_size) on the slim index, so the realistic shape is super-linear. The `_index_df` grows monotonically; by the time chunk 1000 lands, every chunk re-concatenates with a 100M-row slim index. That's the failure mode to design against, not block-scoring throughput.

### Where time and memory actually go at this scale

Reading `core/chunked.py::process_file` and `core/pipeline.py`:

1. **Block scoring** (`score_blocks_parallel`). Already parallel via `ThreadPoolExecutor` or `score_blocks_ray`. Per-block rapidfuzz `cdist` releases the GIL. Scales linearly with cores. **Not the bottleneck**.
2. **Cross-chunk matching** (`_match_against_index`). Single-process Polars concat + `compute_matchkeys` + `score_blocks_parallel` over the joint frame. **Bottleneck at 100M** — the slim index alone is N × ~5 cols × ~20 bytes ≈ 10 GB at 100M rows.
3. **Pair accumulator** (`self._all_pairs: list[tuple[int,int,float]]`). PR #235's `backend="duckdb"` moves this off-heap. At 100M with a 12% dupe rate that's ~150M pairs; the Python list alone is ~12 GB. **Solved if duckdb backend is on**.
4. **Final clustering** (`build_clusters`). Union-Find over all pairs. Iterative, not recursive (per CLAUDE.md), but single-process. 150M pairs × O(α(N)) per union — fits on a single box (~20 GB peak), but it's one-of-one wall-clock.
5. **Golden record build**. Per-cluster aggregation. Embarrassingly parallel; not the bottleneck.

So the binding constraint for getting under 2h on 100M is: **distribute cross-chunk matching**. That decision frames the Ray-vs-Spark choice more than any benchmark of either's RPC overhead.

---

## Option A: Ray

**Status in tree.** Already shipped: `goldenmatch/backends/ray_backend.py` (drop-in replacement for `score_blocks_parallel`), `pip install goldenmatch[ray]`, `--backend ray` CLI flag. Auto-initializes local mode; manual init for cluster mode.

### Strengths for 100M

- **Already plugged in.** Pipeline routes through `_get_block_scorer(config)`; adding `score_blocks_ray` for cross-chunk scoring is the same hook.
- **No JVM, no shuffle protocol to debug.** Python all the way down. rapidfuzz is `pip install`. Polars stays Polars.
- **Object store does the zero-copy thing well.** `mk_ref = ray.put(mk)` + `exclude_ref = ray.put(frozen_exclude)` already in `ray_backend.py:88`. Pattern extends to the slim index — `ray.put(self._index_df)` once per chunk batch.
- **Ray actors for the index.** The right shape at 100M is *N* actors each owning a shard of the cross-chunk index keyed by block-key prefix; chunk dispatch goes only to the actor(s) holding matching keys. Maps onto Ray's stateful actor model directly. No equivalent first-class abstraction in Spark.
- **GPU-adjacent.** If `embedding`/`record_embedding` scorers enter the mix (they're 400 LOC away — `core/scorer.py:378`), Ray's GPU scheduling is mature. Spark GPU scheduling exists but is gnarlier.
- **Cluster cost.** Spinning up a 4–8 node Ray cluster on EC2/GKE is one Helm chart or `ray up`. No JVM to size.

### Weaknesses for 100M

- **Shuffle is hand-rolled.** Ray doesn't give you a `groupBy(block_key).flatMap(score)` primitive. The block-key-sharded actor design is something we'd write and own. Spark hands you this for free.
- **No catalog integration.** If the source is a Delta table, Spark reads it natively. Ray reads parquet via `ray.data.read_parquet`, but Delta-on-Ray means juggling deltalake-rs or `ray-deltalake`.
- **Smaller community for ER specifically.** Splink is the canonical "ER on Spark" reference. Splink-on-Ray doesn't exist.
- **Serialization tax on `block.df`.** Already visible at `ray_backend.py:104-113` — every block's Polars frame gets `collect().lazy()` before remote dispatch (Polars LazyFrames don't pickle cleanly; eager DataFrames do). At 100M scale we'd want this to live in the object store, not be re-pickled per task.

### Implementation cost to ship 100M-ready Ray

1. Extend `_match_against_index` to dispatch to Ray actors keyed by block-key prefix. ~300 LOC + tests.
2. Move the slim index into Ray's object store; per-actor shard owns one prefix. ~150 LOC.
3. Distributed Union-Find: either chunk-then-merge (run local Union-Find per actor, then merge final pairs at the driver) or pull in `networkx` + `ray.data` connected-components. ~200 LOC.
4. End-to-end 100M synthetic benchmark, CI-gated for regressions. ~1 week.

**Total estimate: ~2 engineer-weeks** to a measurably-working 100M Ray path.

---

## Option B: Spark

**Status in tree.** Zero. Referenced in `docs/wiki/Comparison.md:69` as the tool for "billions of records, Spark cluster" — and that's correct, that's Splink's job. No goldenmatch backend exists.

### Strengths for 100M

- **Distributed shuffle for free.** `df.groupBy("block_key").applyInPandas(score_block, schema=...)` is the entire blocking-and-scoring stage. No actors to write, no object-store dance. The reduce side handles the cross-chunk merge implicitly.
- **Lakehouse integration.** Reads Delta, Iceberg, Hudi, BigQuery, Snowflake-via-Spark-Connect natively. If 100M records live in any of those, the input side is solved.
- **Operational maturity.** Databricks, EMR, GCP Dataproc all run this for you. JVM tuning is a known quantity; OOMs surface as task failures not silent process kills.
- **Catalyst optimizer on the blocking joins.** If we ever express blocking as a join (multi-pass: `df.join(df, on="block_key_1").union(df.join(df, on="block_key_2"))`), Catalyst optimizes the resulting plan. Spark is genuinely better than Ray here.
- **Splink coexistence story.** If the org adopts Splink for the 1B tier, having goldenmatch speak Spark means seamless handoff and shared infra. Today there's a hard cliff.

### Weaknesses for 100M

- **Operational tax is real.** JVM heap tuning, cluster sizing, Spark Connect setup, Python ↔ JVM serialization quirks (Arrow helps but doesn't eliminate). For a one-off 100M run you're not amortizing over 1B runs, this overhead dwarfs the implementation.
- **Python UDF performance.** Per-block scoring is a `pandas_udf` calling `rapidfuzz.process.cdist`. The Python interpreter inside the executor is still GIL-bound for non-cdist work. Arrow ser/deser per partition is a real tax (~10–30% in practice).
- **Two pipelines forever.** Spark backend means maintaining both the polars-direct/chunked path AND the Spark path. Two test matrices, two CI lanes, two sets of edge cases (empty partition, skewed block, etc.).
- **JVM dep in `goldenmatch[spark]`.** Worst optional dep we'd ship — `pyspark` itself is 300MB, requires Java 11/17, and the wheel doesn't pin Java cleanly. Friction for anyone trying it.
- **Splink already exists.** If you're going to build "GoldenMatch on Spark," you're competing with Splink, which has 5+ years of Fellegi-Sunter-on-Spark maturity. Hard sell.

### Implementation cost to ship 100M-ready Spark

1. New `backends/spark_backend.py`: read source via Spark, project matchkey columns, build blocking-key column, `groupBy(block_key).applyInPandas(score_pandas_block, schema)`. ~600 LOC.
2. Translate `MatchkeyConfig` + transforms into pandas_udf-safe code. Most are already pandas-ish; the embedding scorer needs broadcast variables. ~400 LOC.
3. Distributed clustering: GraphFrames `connectedComponents()` or roll our own via Spark's `Pregel`. ~300 LOC.
4. Postgres/DuckDB sink integration — Spark writes parquet, then a separate ingest step. ~100 LOC.
5. Spark in CI. ~1 week to get reliable.
6. End-to-end 100M benchmark on a real cluster (EMR/Databricks). Requires AWS/Databricks budget approval.

**Total estimate: ~6–8 engineer-weeks** + ongoing JVM maintenance + cluster compute spend during dev.

---

## Decision matrix

| Dimension                                    | Ray                   | Spark                       | Winner   |
| -------------------------------------------- | --------------------- | --------------------------- | -------- |
| Already in tree                              | yes                   | no                          | **Ray**  |
| LOC to first 100M run                        | ~650                  | ~1400                       | **Ray**  |
| Wall-time at 100M (projected, 8c × 8 nodes)  | ~90 min               | ~70 min                     | Spark    |
| Op tax (cluster setup, JVM, tuning)          | low                   | high                        | **Ray**  |
| Lakehouse-native reads                       | indirect              | yes                         | Spark    |
| Path to 1B records                           | rewrite               | reuses                      | Spark    |
| Reuses existing rapidfuzz / Polars / pydantic | yes (direct)          | yes (via pandas_udf)        | tie      |
| GPU scoring path                             | clean                 | doable but fiddly           | **Ray**  |
| Competes with Splink                         | no (different niche)  | yes (same niche)            | **Ray**  |
| 2026 hiring market expertise                 | growing               | mature                      | Spark    |

**Net:** Ray wins on 8 of 10. The two Spark wins (lakehouse, path-to-1B) are real but matter only if those are committed product directions. Today they're not.

---

## Recommended path forward

### Phase 1 — Measure before designing (1 week)

Write `tests/bench_100m.py`. Use the existing synthetic generator. Run on:

- 4c/16GB (CI baseline) — expect chunked to OOM or run out of disk; document the failure mode.
- 16c/64GB (largest single box) — establish the "do nothing, just rent a bigger box" baseline.
- 8 × 4c/16GB Ray cluster on EC2/GKE — `backend="ray"`, current code, no chunked refactor.

The third number is the one that justifies (or kills) Phase 2.

### Phase 2 — Partition the chunked driver (2 weeks)

The cross-chunk index is the load-bearing data structure at 100M. Shard it by block-key prefix across Ray actors. Spec lives in `docs/superpowers/specs/` (gitignored, local-only). Key invariants to preserve:

- Pair canonicalization `(min(a,b), max(a,b))` across shards.
- Idempotent re-resolution for identity graph (each shard emits to a queue, driver dedupes).
- `__row_id__` global uniqueness (use Ray's actor-local offsets + driver-assigned range).

Drop the `_index_df` single global frame; replace with `IndexShard` actors.

### Phase 3 — Re-benchmark and decide (1 week)

After Phase 2, the same 100M run on the same 8 × 4c/16GB cluster. If wall-time is under 2h and peak per-node RSS is under 12 GB, **ship Ray and stop**. If not, the remaining gap is either:

- Block scoring throughput → throw cores at it; still Ray, no Spark.
- Shuffle inefficiency → re-evaluate Spark, but plan for the 6–8 week port and the dual-maintenance tax.

### Phase 4 (only if Phase 3 says so) — Spark backend

Build it as a thin shim that targets the Spark Connect API specifically — avoids the JVM-in-CI problem. Treat it as a 1B-tier offering, not a 100M one. Update the comparison wiki to say "GoldenMatch handles up to 100M on Ray; for 1B+ on existing Spark infra, the Spark backend (or Splink) is the route."

---

## What this evaluation deliberately does NOT cover

- **Dask.** Skipped because Ray is already in-tree; adding a third distributed primitive isn't on the table.
- **Polars-native distributed mode** (announced for Polars Cloud). Track for the next round; not GA at this writing.
- **DuckDB scoring extension** (referenced in `backends/score_duckdb.py:25-31` as "v2 investment"). Orthogonal to Ray-vs-Spark — it's about pushing scoring into SQL, not about which orchestrator runs SQL. Worth doing independently for the 10–50M tier on a single box.
- **Cost-per-run math.** Depends on cluster shape and cloud. The Ray-vs-Spark choice doesn't move this number much; the per-record scoring work dominates either way.

---

## References in tree

- `goldenmatch/backends/ray_backend.py` — existing Ray backend (block scoring only).
- `goldenmatch/backends/score_duckdb.py:9-31` — out-of-core pair accumulator rationale.
- `goldenmatch/core/chunked.py:28-172` — single-process chunked driver; this is the file Phase 2 refactors.
- `goldenmatch/core/scorer.py:790` — `score_blocks_parallel`; both Ray and a hypothetical Spark backend slot in as siblings.
- `CLAUDE.md` lines 90–92 — recorded 5M run data; the empirical baseline this evaluation extrapolates from.
- `docs/wiki/Comparison.md:69` — Splink positioning; what we want to *not* compete with at 1B.
