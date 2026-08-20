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

**These are the numbers as of this curve. They have since been beaten by 4.1x
-- see "The scorer was being evaluated several times per pair" below, which
takes 250M from 2,765.8s to 669.96s and the per-million-pairs figure from 1.192
to 0.289.** The curve is kept as measured rather than restated, because the
three defects that closed that gap are only legible against it.

Three properties hold across the whole range: `u` stays nearly flat (3.5s to
5.4s for 5x the rows, which is what the fix was for), the plan does not grow
(119 stages at every scale), and quality is stable or slightly better.

**The growing term is SPILL** -- zero at 50M, then 56.4 GB, then 201.3 GB, so
3.57x for 2.5x the data, growing faster than the data does. Nothing failed:
Spark spilled and continued, no executor died, no task failed.

**Whether it is the CEILING was tested, and the answer is "not linearly".**
This section first asserted flatly that spill was the ceiling. It then, on one
run's evidence, retracted that just as flatly. Both statements were too strong,
and the sequence is left visible here because the correction is the finding.

The test: an engine change that removes the pair-sized shuffles feeding the
spill (branch `perf/fuse-block-join-counts`, PR #2698 -- not in `main` at the
time of writing), run on the same cluster shape at two sizes.

| | 100M (`32393456488`) | 250M (`32400918792`) |
|---|---|---|
| memory spill | 56.4 -> 4.29 GB | 201.3 GB -> **0** |
| disk spill | 5.0 -> 0.23 GB | 17.4 GB -> **0** |
| shuffle write | 174.9 -> 99.0 GB | 438.1 -> 113.8 GB |
| stages | 119 -> 74 | 119 -> 52 |
| **total wall** | 958.31 -> 975.30s (**+1.8%**) | 2,765.8 -> **2,189.3s** (**-20.8%**) |
| score stage | 651.9 -> 722.4s (**+10.8%**) | 1,888.8 -> **1,574.6s** (**-16.6%**) |
| average precision / best F1 | unchanged | unchanged |

**The score stage changes SIGN between the two sizes.** Same code, same cluster
shape, 11% slower at 100M and 17% faster at 250M. That is not run-to-run
variance -- this lane moves ~2% between runs of the same code -- it is a real
scale threshold. Below it the cluster absorbs the spill and paying to avoid it
is a net loss; above it the spill dominates and avoiding it wins.

So the honest statement is neither of the first two. Spill is **a** ceiling
above a threshold that sits between 100M and 250M on this cluster shape, and
below that threshold it is close to free. Any claim about it has to name a
size, exactly as the "zero spill" claim does.

**One attribution this does NOT settle.** The same change removes 74% of the
shuffle write as well as all of the spill, so the 250M win cannot be assigned
to spill alone from these runs. Separating them needs a variant that removes
one without the other, which has not been run.

**And a methodological note worth more than the numbers.** Every comparison
above is one arm against a baseline banked from a DIFFERENT job -- different
VMs, different JVM, different shuffle files. That is worth ~2% run-to-run and
up to ~16% across a re-run, which is the same order as several of the effects
being measured. `scripts/spark_fs_train_scale.py --ab N` now runs both arms in
one session over one cached fixture, alternating so warm-up drift cancels, and
reports each arm's spread as the noise floor. Comparisons made the old way --
including the 100M row above -- carry that confound.

**"Zero spill" is therefore a SCALE-BOUNDED claim.** It is true at 50M and
false at 100M. Any comparison quoting it must say at what size.

## The scorer was being evaluated several times per pair

Everything above measures a build that called the similarity kernel more times
than it needed to, in two places, on the SHIPPED path. Both were found by
splitting the score stage into layers (`--attribute-score`) rather than by
reading the plan -- three attempts at reasoning from plan shape had already
failed.

**The weight lookup named the level once per level.** `_weight_lookup_expr`
built `when(level == 0, w0).when(level == 1, w1)...`, and `level` is the whole
gamma expression with the jar scorer call inside it. Catalyst's subexpression
elimination does not hoist a UDF out of conditionally-evaluated CASE branches.

**The level ladder named the similarity once per threshold.** `fs_level_expr`
sums `when(sim >= t, 1)` per threshold. Same class, different position --
always-evaluated conditions of separate CASEs rather than conditional branches
of one -- so it was measured beside a projected variant instead of being assumed
to cost the same.

Layer split at 50M, each layer adding one thing to the one before:

| layer | before | after |
|---|---:|---:|
| join | 13.47s | 12.64s |
| + raw similarities | -- | 73.30s |
| + level bucketing | 125.79s | 75.16s |
| + weight lookup | **340.75s** | **~81s** |
| **score stage** | **347.07s** | **82.20s** |

### 250M, both fixes, same cluster shape (run `32423691696`)

| | legacy | fused (#2698) | + scorer fixes |
|---|---:|---:|---:|
| **wall** | **2,765.8s** | 2,189.3s | **669.96s** |
| counts | 835.7s | 575.4s | **317.04s** |
| score | 1,888.8s | 1,574.6s | **319.31s** |
| u | 5.4s | 4.7s | 4.06s |
| shuffle write | 438.1 GB | 113.8 GB | 113.8 GB |
| memory spill | 201.3 GB | **0** | **0** |
| stages | 119 | 52 | 52 |
| executor CPU | -- | 133,584s | **42,503s** |
| average precision | 0.704939 | 0.704939 | 0.704939 |
| best F1 | 0.686126 | 0.686126 | 0.686126 |

**4.13x on wall, and 3.1x less executor CPU.** CPU falling in step with wall is
the tell that this removes work rather than moving it: the fused join (#2698)
cut bytes without cutting CPU, and cut wall only above the spill threshold.
These cut the work itself, so they pay at every size.

**Seconds per million pairs: 1.192 -> 0.289.**

Correctness is checked rather than asserted: trained `match_weights` and
`m_probs` are **byte-identical** to the run above, the score-group count matches
(372), pair counts match, and average precision and best F1 agree to six
decimals. The group count differs between 50M (399) and 250M (372) because
different data sizes reach different level combinations -- the comparison that
matters is 250M against 250M, and it is exact.

### What is left

The 82.2s score stage at 50M is now the join (12.6s) plus the kernel itself
(60.7s, about **25ns per scorer call**). Everything layered above the kernel has
been removed. Going further means fewer pairs, a faster kernel, or fewer calls
per pair -- and batching calls into one vector UDF was already measured **2.3x
SLOWER** (`GOLDENMATCH_SPARK_VECTOR_SCORER`, kept off as a recorded negative
result).

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
