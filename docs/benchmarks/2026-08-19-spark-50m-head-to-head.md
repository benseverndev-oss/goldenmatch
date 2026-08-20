# Distributed FS training at 50M: GoldenMatch vs Splink on one Spark cluster

**Date:** 2026-08-19
**Commit:** `db796d951` (`feat/shuffle-partitions`)
**Cluster:** 4 x `n2-standard-16` workers + 1 master, `us-east1-c`, 750 GB local NVMe/node
**Workflow:** `bench-spark-gce-cluster.yml`, runs `32292243875` (50M) and `32290866436` (1M)
**Harnesses:** `scripts/spark_fs_train_scale.py` (GoldenMatch), `scripts/spark_splink_train_scale.py` (Splink 4.0.16)

## What this measures

Fellegi-Sunter **model training** on a real multi-node Spark cluster: estimate
`u`, then EM parameter estimation, then a scoring/predict pass. Not clustering,
not survivorship.

Both arms run on the **same cluster**, over the **same fixture** (literally the
same `build_fixture` function, same seed, same rows), and are scored by the
**same metric implementation** (`scripts/_fs_quality_metrics.py`). Where the two
engines could be given different Spark settings, they are given identical ones
from a single shared module (`scripts/_spark_tuning.py`), so a difference in the
numbers is a difference in the engines rather than in the tuning.

This is the first run of this comparison that is valid. Every earlier one was
not, for reasons documented below.

## Why every earlier Splink number in this repo was wrong

Splink failed four times at 50M before this run. Each failure was **our**
misconfiguration, and each one, if published, would have read as a Splink
limitation.

| # | Defect | What it actually did |
|---|---|---|
| 1 | `break_lineage_method="persist"` | Caches without truncating the DAG. Splink's documented default is `parquet`. Fixing it took 1M from **337s to 104s**. |
| 2 | No distributed filesystem | `parquet` writes from the executors and reads back, so it needs shared storage. This lane had none. Now stands up HDFS across the cluster nodes. |
| 3 | Executor-count races | Splink started before the master released Spark Connect's cores, and its own wait returned on the FIRST executor to register. It ran on **2 executors against GoldenMatch's 4**, and derived 160 shuffle partitions instead of 320. |
| 4 | `spark.executor.memory` unset | Splink took Spark's **1g** default while GoldenMatch's Connect server was launched with **48g**. A 48x heap disadvantage. |

Number 4 caused the 50M OOMs (`exited with code 52`), and it also produced a
result that was previously read as a property of the engines: Splink spilling
4.09 GB memory / 1.88 GB disk where GoldenMatch spilled zero. **With equal
memory both arms spill nothing**, and the GC gap that looked like 5.9x is 1.19x.

Splink's documented deployments run 100M+ on Databricks / EMR / Dataproc, where
a shared filesystem and sane executor sizing are native. Bolting a benchmark
onto bare standalone Spark is what introduced all four defects, and they are
ours, not Splink's.

## Results: 50M rows, 463,923,179 candidate pairs

Both arms: 4 executors, 48g each, 16 cores each, 320 shuffle partitions,
`break_lineage_method="parquet"` onto HDFS. Zero spill, zero failed tasks, no
executor deaths on either side. Identical pair counts.

| | GoldenMatch | Splink | ratio |
|---|---:|---:|---:|
| **wall total** | **862.25s** | **1,247.80s** | 1.45x |
| u / estimate | 98.63s | 43.29s | **0.44x** (Splink faster) |
| counts vs EM | 364.66s | 631.44s | 1.73x |
| score / predict | 387.38s | 552.60s | 1.43x |
| shuffle write | 128.46 GB | 212.07 GB | 1.65x |
| shuffle read | 128.46 GB | 223.38 GB | 1.74x |
| stages | 197 | 394 | 2.00x |
| tasks | 35,679 | 31,357 | 0.88x |
| executor CPU | 47,121s | 63,759s | 1.35x |
| JVM GC | 273s | 413s | 1.51x |
| memory spill | 0 | 0 | — |
| disk spill | 0 | 0 | — |
| average precision | 0.700397 | 0.641803 | — |
| best F1 | 0.684516 | 0.600296 | — |

## Update 2026-08-20: re-run after the `u` fix, and a scale curve

The table above was measured BEFORE the v3.13.1 `u`-estimation fix. Both arms
were re-run on the current build (run `32372957870`), same cluster, same
configuration, same 463,923,179 pairs:

| | GoldenMatch | Splink | ratio |
|---|---:|---:|---:|
| **wall** | **552.39s** | **1,054.12s** | **1.91x** |
| u / estimate | 3.51s | 30.83s | **8.78x** |
| counts vs EM | 168.53s | 538.33s | 3.19x |
| score / predict | 369.95s | 466.06s | 1.26x |
| shuffle write | 86.83 GB | 212.09 GB | 2.44x |
| stages | 119 | 394 | 3.31x |
| CPU | 31,300s | 51,572s | 1.65x |
| GC | 135s | 372s | 2.76x |
| spill | 0 / 0 | 0 / 0 | -- |
| average precision | 0.700397 | 0.641803 | -- |
| best F1 | 0.684516 | 0.600296 | -- |

The `u` stage went from **2.3x slower than Splink to 8.78x faster**.

**A predicted 2.26x was wrong, and the reason is worth keeping.** Holding
Splink's previous 1,247.8s constant and applying GoldenMatch's improvement
predicts 2.26x. The real answer is 1.91x, because Splink ALSO got faster on the
re-run -- 1,247.8s to 1,054.12s, about 16% across every stage. That is
run-to-run or cluster variance, and it cuts both ways: the earlier observation
that "the margin narrows with scale" deserves the same scepticism, since single
runs on this lane move ~16%.

## Scale curve: 50M -> 100M -> 250M (GoldenMatch only)

Runs `32372957870`, `32376766045`, `32379345610`. Same cluster shape
(4x `n2-standard-16` + master), same fixture generator, same 320 partitions.

| | 50M | 100M | 250M |
|---|---:|---:|---:|
| wall | 552.4s | 958.3s | **2,765.8s** |
| u | 3.5s | 3.5s | 5.4s |
| counts | 168.5s | 288.7s | 835.7s |
| score | 369.9s | 651.9s | 1,888.8s |
| candidate pairs | 463.9M | 927.9M | **2,319.7M** |
| shuffle write | 86.8 GB | 174.9 GB | 438.1 GB |
| memory spill | **0** | 56.4 GB | 201.3 GB |
| disk spill | **0** | 5.0 GB | 17.4 GB |
| stages | 119 | 119 | 119 |
| executors died | none | none | none |
| failed tasks | 0 | 0 | 0 |
| average precision | 0.700397 | 0.699647 | 0.704939 |
| best F1 | 0.684516 | 0.684752 | 0.686126 |

**Seconds per million pairs: 1.191 / 1.033 / 1.192.** Flat across a 5x range.
Cost is linear in PAIRS, not in rows, and the wall growing 2.89x for 2.5x rows
is that pairs grew 2.5x plus a spill tax -- not a scaling break.

Three properties hold across the whole range: `u` stays nearly flat (3.5s to
5.4s for 5x the rows, which is what the fix was for), the plan does not grow
(119 stages at every scale), and quality is stable or slightly better.

**The growing term is SPILL, and it is the real ceiling.** Zero at 50M, then
56.4 GB, then 201.3 GB -- 3.57x for 2.5x the data, so it grows faster than the
data does. Nothing failed: Spark spilled and continued, no executor died, no
task failed. But it is a throughput tax that compounds, and extrapolating puts
500M near ~700 GB of memory spill on this cluster shape. Row count is not the
wall; spill is.

**"Zero spill" is therefore a SCALE-BOUNDED claim.** It is true at 50M and
false at 100M. Any comparison quoting it must say at what size.

## Results: 1M rows (the same run configuration, for scaling)

| | GoldenMatch | Splink | ratio |
|---|---:|---:|---:|
| wall total | 35.82s | 90.89s | 2.54x |
| shuffle write | 2.71 GB | 4.15 GB | 1.53x |
| stages | 197 | 449 | 2.28x |
| GC | 31.6s | 37.6s | 1.19x |
| spill | 0 / 0 | 0 / 0 | — |
| average precision | 0.71524 | 0.703386 | — |
| best F1 | 0.685667 | 0.646975 | — |

## Verdict

On Spark, distributed, at 50M rows, with Splink configured the way its own
performance guide prescribes, **GoldenMatch is faster on wall, moves fewer bytes,
runs half the stages, and burns less CPU and less GC**.

That claim has not been available before. The existing bake-off
(`2026-06-09-splink-bakeoff.md`) records Splink as **3-19x faster single-box**
and notes it "retains distributed Fellegi-Sunter at 1B+ rows on Spark". This run
addresses the distributed half of that framing on Splink's own ground.

## Honest framing

- **Do not quote the accuracy numbers from this run as a GM-vs-Splink accuracy
  verdict.** Both harnesses say so in their own docstrings. The comparison config
  here exists to exercise the `prod(levels + 1)` EM bound for a SCALE test and was
  never chosen for accuracy; running it against Splink's comparison-library
  defaults partly measures that choice. For accuracy, cite
  `2026-06-09-splink-bakeoff.md`, which puts zero-tuning GoldenMatch against an
  expert hand-rolled Splink spec on real datasets under one shared evaluator
  (0.778 vs 0.757, 0.991 vs 0.965, 0.998 vs 0.996).
- **Splink's `u` estimation is 2.3x faster than ours** (43.29s vs 98.63s). A real
  stage-level loss, reported rather than buried, and the next thing to work on.
- **The margin narrows with scale**: 2.54x at 1M, 1.45x at 50M. Splink scales
  better relatively. **Do not extrapolate this ratio to 200M** — the trend runs
  against us.
- **Session type is an unavoidable confound.** GoldenMatch runs over Spark
  Connect (`sc://`) because `addArtifact` is Connect-only; Splink cannot, because
  it reaches for `sparkContext`, which Connect does not expose. So Splink drives a
  classic session. This is how each engine actually ships, which makes it the
  honest product comparison, but it is a difference between the arms.
- **`spark.default.parallelism` is set for neither arm.** Splink's guide
  recommends it alongside `spark.sql.shuffle.partitions`, but it is a static core
  config that cannot be set at runtime on a Connect session. Setting it for Splink
  alone would hand one arm a knob the other cannot reach, so neither gets it.
- **N=1.** One run per engine at each size. Ratios are directional. Runner and
  cluster variance are not characterised.
- The fixture is **synthetic** and one shape (5 fields, 3 levels, seeded typos and
  nulls). It is not a stand-in for customer data.

## Reproduce

```
gh workflow run bench-spark-gce-cluster.yml --ref main \
  -f rows=50000000 -f workers=4 -f disk_gb=750 \
  -f machine_type=n2-standard-16 -f zone=us-east1-c \
  -f splink_compare=true -f eval_quality=true \
  -f splink_checkpoint_fs=hdfs -f shuffle_partitions=-1
```

`shuffle_partitions=-1` derives 5x total executor cores from the cluster's own
executor list. `splink_checkpoint_fs=hdfs` stands up a namenode on the master and
a datanode on every node; `gcs` is also supported and is what a Dataproc or EMR
user would have.

Cost is roughly one hour of 5 x `n2-standard-16` on-demand per 50M run.
