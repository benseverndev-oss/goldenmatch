# Distributed (Ray) backend — roadmap & current state

**Status as of 2026-05-19:** The Ray backend exists (`backends/ray_backend.py` + Distributed Plan v1 in `goldenmatch/distributed/`) but **failed the binding 5M kill criterion** on the 2026-05-18 bench (run `26045651074`). Soft-reverted in PR #318 — the v3 planner no longer auto-picks ray. Explicit `backend="ray"` or `GOLDENMATCH_ENABLE_DISTRIBUTED_RAY=1` opts back in.

The supported path for 5M-25M today is **`backend="bucket"`** on a 16-core / 32+ GB Linux node. Measured: 5M in 9.94 min / 6.4 GB peak RSS on `large-new-64GB`. 25M extrapolates to ~50 min / ~32 GB peak RSS — comfortably inside a 64 GB box.

## Why the Ray stack isn't Splink-Spark equivalent

Splink delegates **every** pipeline stage to Spark (load, blocking, scoring, clustering, golden, write). Spark's optimizer + shuffle layer handles distribution; the Splink Python process just compiles SQL.

GoldenMatch's Ray backend distributes **one** stage (per-block pair scoring) and runs everything else in a single Polars driver process. That's the kill-criterion failure in one sentence: the driver still holds the full df during prep + clustering + golden, so worker memory doesn't help.

| Stage | Splink (Spark) | GoldenMatch (Ray, today) |
|---|---|---|
| Data load | Spark DataFrame, partitioned | Polars driver, single-node |
| Standardize / auto-fix | Spark SQL UDFs distributed | Polars driver |
| Blocking | Spark group_by distributed | Polars partition_by, single-node |
| Pair scoring | Spark UDF per partition | **Ray tasks ✓** |
| Clustering | Spark GraphFrames / iterative | Python UnionFind, single-node |
| Golden record | Spark groupBy().agg() | Polars group_by, single-node |
| Driver memory | Master coordinates only | Holds the full df |

## Roadmap (5 phases, ~5-6 months total)

1. **Partition-aware data loader** (4-6 weeks) — Ray Datasets (or Daft); driver never holds the full df during prep.
2. **Controller iteration on partitioned samples** (3-4 weeks) — `AutoConfigController._run_pipeline_sample` accepts a Ray Dataset; `compute_column_priors` and the full-df indicators rewritten for distributed exec.
3. **Distributed clustering** (6-8 weeks) — replace Python UnionFind with label propagation or graph-parallel BFS. Output shape preserved so downstream stages don't branch.
4. **Distributed golden record build** (3-4 weeks) — re-partition by `__cluster_id__` then per-partition agg. Output is a partitioned Ray Dataset, not a Python dict-of-dicts on the driver.
5. **Cross-partition pair resolution + cluster orchestration** (4-6 weeks) — Ray cluster bootstrap docs, Postgres-backed Identity Graph for cross-process state, kill-criterion bench at 25M / 50M / 100M.

Full per-phase scope + kill criteria: `docs/superpowers/specs/2026-05-19-ray-splink-spark-parity-roadmap.md` (gitignored; spec lives there until a roadmap-as-tracked-issue is opened).

## Pragmatic call

- **5M-25M:** use `backend="bucket"` on a 64 GB box. Don't touch the Ray path.
- **25M-100M:** wait for Phase 1 to land, or use Splink on Spark if you already have a Spark cluster. Today's Ray code will not beat single-node bucket on the same workload.
- **>100M:** Ray roadmap is the only goldenmatch-shaped answer, and it's ~6 months out. Bigger problem; bigger investment.

## Estimated effort vs Splink

Splink's Spark backend took **years** of work, much of it leaning on Spark's maturity. GoldenMatch is closer to "build a distributed engine on top of Polars/Ray" than "wire to an existing one". The realistic posture is:

1. Phases 1-2 first; that's the foundation.
2. Phases 3-4 to fill in the distributed parts of the pipeline.
3. Phase 5 only after a real customer workload demands it.

## Re-bench cadence

- **Today's bench** at 25M will validate (or invalidate) the linear-extrapolation projection of bucket-on-one-node. If 25M fits comfortably in 64 GB, the urgency of Phase 1 drops considerably.
- **Re-bench Ray after Phase 1** ships. If Phase 1 alone gets the kill criterion to PASS at 5M, that's a major win and Phases 2-5 can be paced against real customer pull.
