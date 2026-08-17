#!/usr/bin/env python3
"""Splink FS training on the SAME Spark cluster, for a like-for-like wall.

## Why this exists

Every performance decision on the Spark tier has been sized against "we are ~5x
slower than Splink", and that number does not exist anywhere in this repo. What
does exist is a different comparison (the JVM path measured 2.4x slower than the
PYTHON-WORKER path) and a single-box panel whose Splink lane runs on **DuckDB**,
not Spark. Choosing between a columnar UDF, a Catalyst extension and an Arrow
C-Data-Interface rewrite on the strength of an unmeasured gap is how a month
goes into the wrong thing.

This runs Splink's Fellegi-Sunter TRAINING against the same standalone cluster,
on the same fixture, so the two numbers are comparable.

## What is matched, and what is not

MATCHED: the cluster (one master, two workers, whatever sizing the workflow
gave them), the fixture (`build_fixture` from `spark_fs_train_scale.py` --
literally the same function, same seed, same row count), and the workload
(estimate u, then EM parameter estimation -- model training, not scoring or
clustering).

NOT MATCHED, deliberately and unavoidably: the SESSION TYPE. GoldenMatch's tier
runs over Spark **Connect** because `addArtifact` is Connect-only and that is
the deployment story the jar exists for. Splink cannot run over Connect -- it
reaches for `sparkContext`, which Connect does not expose. So Splink gets a
CLASSIC session against `spark://...:7077` while GM gets Connect against
`sc://...:15002`.

That confound is real and is reported in the output rather than smoothed over.
It is also the honest product comparison: this is how each engine actually
ships. A reader wanting to isolate "is our kernel slower" needs a different
experiment; a reader asking "should I use GM or Splink on my cluster" wants
exactly this one.

## What --eval-quality is FOR, and what it is not

It is a REGRESSION GUARD on the distributed path: does Spark-trained FS rank
pairs about as well as it did last time. It is NOT a GoldenMatch-vs-Splink
accuracy verdict, and a run of it must not be quoted as one.

That question is already answered, better, elsewhere.
`docs/benchmarks/2026-06-09-splink-bakeoff.md` puts GM's ZERO-TUNING auto-config
against an expert hand-rolled Splink spec on real datasets under one shared
evaluator, and GM matches or beats it everywhere Splink scores:

    historical_50k    GM 0.778  Splink 0.757   +0.021
    febrl3            GM 0.991  Splink 0.965   +0.026
    synthetic_person  GM 0.998  Splink 0.996   +0.001

This harness measured the opposite (Splink +0.021 average precision) and the
difference is the CONFIG, not the engines. The comparison config here exists to
exercise the `prod(levels + 1)` bound for a SCALE test -- its own comment says
"FIVE fields at three levels each ... so the driver-side EM has a bound worth
testing" -- and was never chosen for accuracy. Running it against Splink's
comparison-library defaults measures that choice.

The tell was that the two comparisons inverted on BOTH axes: the bakeoff has
Splink 3-19x FASTER single-box and less accurate; this lane has Splink ~22x
slower and more accurate. When both flip, the harness is the variable.

So: use this to catch a regression in the distributed trainer. Use the bakeoff
for accuracy claims.

## Read the cluster sizing before reading the number

The default rig is two workers at one core each on a 4-CPU runner -- two
executor cores, one host. That is a TOPOLOGY rig, and a wall measured on it is
not throughput for either engine. The workflow's `runner` / `worker_cores` /
`worker_memory` inputs exist to size it up; this script records the executor
count it actually saw so an artifact cannot be read out of context.

## The driver has to be reachable

A classic session driven from the runner puts the DRIVER on the host while the
executors are containers. The workers must connect BACK to the driver, so
`spark.driver.host` has to be an address they can route to -- the docker bridge
gateway, not `localhost`. Get it wrong and the symptom is not an error: the job
sits at "Initial job has not accepted any resources" until something times out.
`--driver-host` is therefore explicit, and the script fails loudly with that
diagnosis if no executor registers inside `--executor-wait`.

Usage:
    python scripts/spark_splink_train_scale.py --rows 1000000 \\
        --master spark://localhost:7077 --driver-host 172.17.0.1 \\
        --out splink-train-scale.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import time
from pathlib import Path


def _fixture_module():
    """Import `build_fixture` from the GM harness, so the data is identical.

    Re-implementing the generator here would be the classic benchmark lie: two
    "same" fixtures that drift apart, and a comparison that quietly measures the
    difference between them instead of the difference between the engines.
    """
    import importlib.util

    path = Path(__file__).resolve().parent / "spark_fs_train_scale.py"
    spec = importlib.util.spec_from_file_location("spark_fs_train_scale", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _metrics_module():
    """The SHARED ranking metric, imported by file path.

    Both arms score with this one implementation. Two hand-written average
    precisions that disagree by a percent would look exactly like a model that
    is a percent better, which is the distinction the whole comparison exists
    to make.
    """
    import importlib.util

    path = Path(__file__).resolve().parent / "_fs_quality_metrics.py"
    spec = importlib.util.spec_from_file_location("_fs_quality_metrics", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_session(master: str, driver_host: str | None, wait_s: int):
    """A session tuned NO differently from the one GM gets.

    This used to set `spark.sql.shuffle.partitions=8`, with the rationale that
    "the default 200 on a small cluster is pure scheduling overhead". That was
    written for the original 2-executor-core topology rig, where it was true.
    When the lane grew a `worker_cores` input and started running on 16 cores,
    the constant did not grow with it and quietly became a HANDICAP that GM
    never paid: the GM harness sets no partition count at all, so it ran on
    Spark's default 200 while Splink was pinned to 8. Eight partitions on
    sixteen cores leaves most of the cluster idle through every shuffle, and
    makes each partition large enough to push an executor over -- which is how
    the 5M run died, with `MetadataFetchFailedException: Missing an output
    location for shuffle 13 partition 3` after an executor was lost.

    Nothing is tuned here now. Both engines get Spark's defaults, which is the
    only setting that needs no justification to a reader who suspects the
    benchmark was rigged.
    """
    from pyspark.sql import SparkSession
    from splink.backends.spark import similarity_jar_location

    b = (
        SparkSession.builder.master(master)
        .appName("splink-train-scale")
        .config("spark.driver.bindAddress", "0.0.0.0")
        # Splink ships its own similarity UDFs (jaro_winkler, damerau
        # levenshtein) as a JVM jar, and WITHOUT IT THE COMPARISON IS INVALID
        # rather than merely degraded: Splink warns "Unable to load custom Spark
        # SQL functions such as jaro_winkler ... You will not be able to use
        # these functions in your linkage" and carries on, so the run completes
        # having compared something other than what GM compares. `spark.jars`
        # also ships it to the executors, which is where the UDFs evaluate.
        .config("spark.jars", similarity_jar_location())
    )
    if driver_host:
        b = b.config("spark.driver.host", driver_host)
    spark = b.getOrCreate()

    # Splink's Spark backend breaks lineage with `Dataset.checkpoint()`, which
    # needs a checkpoint dir, and a checkpoint dir on THIS topology has nowhere
    # to live. Spark says so itself:
    #
    #   WARN SparkContext: Spark is not running in local mode, therefore the
    #   checkpoint directory must not be on the local filesystem.
    #
    # A bind-mounted host path gets the driver and executors to the same inode
    # but not to the same identity: the driver (runner user) creates the
    # per-application subdirectory at 755 and the executors (non-root user in
    # the container) then fail with "Mkdirs failed to create
    # file:/tmp/spark-checkpoint/<uuid>/.../_temporary/...". chmod on the base
    # directory does not reach the subdirectories Spark makes later. Fixing it
    # properly means HDFS/S3, which this lane does not have.
    #
    # So `persist` instead, and RECORDED as a deviation rather than quietly
    # taken -- see `break_lineage_method` in the output. It does not
    # disadvantage Splink: persist materialises to executor memory/disk and
    # skips the distributed-filesystem write that checkpointing would pay, so
    # if it biases the wall at all it biases it in Splink's favour.

    # Refuse to measure a cluster that never gave us executors. Without this the
    # run "works" -- it just runs everything on the driver and reports a wall
    # that has nothing to do with the cluster, which is a worse outcome than a
    # failure because it looks like data.
    deadline = time.time() + wait_s
    n = 0
    while time.time() < deadline:
        try:
            n = spark.sparkContext._jsc.sc().getExecutorMemoryStatus().size() - 1
        except Exception:  # noqa: BLE001 - probe only
            n = 0
        if n > 0:
            break
        time.sleep(2)
    if n <= 0:
        raise SystemExit(
            f"::error::no executor registered within {wait_s}s on {master}. "
            f"The usual cause is driver reachability: the driver runs on this "
            f"host and the workers are containers, so `spark.driver.host` must "
            f"be an address they can route to (the docker bridge gateway), not "
            f"localhost. Pass --driver-host."
        )
    print(f"[splink] {n} executor(s) registered on {master}", flush=True)
    return spark, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--dup", type=int, default=3, help="rows per entity")
    ap.add_argument("--blocks-per-key", type=int, default=4)
    ap.add_argument("--master", default=os.environ.get(
        "SPLINK_SPARK_MASTER", "spark://localhost:7077"))
    ap.add_argument("--driver-host", default=os.environ.get("SPLINK_DRIVER_HOST", ""))
    ap.add_argument("--executor-wait", type=int, default=120)
    ap.add_argument("--max-pairs", type=int, default=1_000_000,
                    help="Splink's u-estimation sample. Matches the GM harness's "
                         "--u-max-pairs so the u stage is comparable.")
    ap.add_argument("--seed", type=int, default=42,
                    help="Seeds Splink's u random sampling. Unseeded, the EM "
                         "iteration count -- and therefore the wall -- varies "
                         "by more than half between identical runs.")
    ap.add_argument("--eval-quality", action="store_true",
                    help="REGRESSION GUARD on the distributed trainer: score "
                         "the candidate pairs and report ranking quality "
                         "against the fixture's known entity structure. NOT a "
                         "GM-vs-Splink accuracy verdict -- this fixture's "
                         "config was chosen to stress prod(levels+1) for a "
                         "scale test. See the bakeoff for accuracy.")
    ap.add_argument("--out", default="splink-train-scale.json")
    args = ap.parse_args()

    out: dict = {
        "engine": "splink", "rows_requested": args.rows, "master": args.master,
        "stages": {}, "session_type": "classic",
        "session_confound": (
            "GM runs over Spark Connect (addArtifact is Connect-only); Splink "
            "cannot (it uses sparkContext). Same cluster, different front door."
        ),
        # Recorded, not buried. Splink's default lineage break is `checkpoint`,
        # which needs a distributed filesystem this lane has no way to provide.
        # `persist` skips that write, so if it moves the wall it moves it in
        # SPLINK's favour -- the deviation cannot manufacture a GM win.
        "break_lineage_method": "persist",
        "break_lineage_deviation": (
            "Splink's Spark default is `checkpoint`; it requires a checkpoint "
            "dir on a non-local filesystem (Spark refuses local paths outside "
            "local mode) and this cluster has only container-local disk. "
            "`persist` materialises to executor memory/disk instead."
        ),
    }
    t_all = time.perf_counter()

    spark, n_exec = build_session(args.master, args.driver_host or None,
                                  args.executor_wait)
    out["executors"] = n_exec

    fx = _fixture_module()
    t = time.perf_counter()
    df = fx.build_fixture(spark, args.rows, args.dup, args.blocks_per_key)
    df = df.withColumnRenamed("__row_id__", "record_id")
    actual = df.count()
    out["stages"]["fixture_seconds"] = round(time.perf_counter() - t, 2)
    out["actual_rows"] = int(actual)
    print(f"[splink] fixture: {actual:,} rows in "
          f"{out['stages']['fixture_seconds']}s", flush=True)

    from splink import Linker, SettingsCreator, block_on
    from splink import comparison_library as cl
    from splink.backends.spark import SparkAPI

    # Count EM iterations, because the wall is roughly linear in them and a
    # seed only makes the count REPRODUCIBLE, not equal across configurations.
    # Reporting seconds without iterations invites reading a convergence
    # difference as a speed difference -- which is exactly the mistake the
    # unseeded runs produced. Splink does not expose the count as an attribute,
    # so it is taken from its own log records; `iterations` staying 0 in the
    # artifact means the log format moved and the number should not be trusted
    # rather than silently reading as "no work".
    class _EMIterationCounter(logging.Handler):
        _PAT = re.compile(r"^Iteration (\d+):")

        def __init__(self) -> None:
            super().__init__()
            self.per_session: list[int] = []
            self._current = 0

        def emit(self, record: logging.LogRecord) -> None:
            m = self._PAT.match(str(record.getMessage()))
            if not m:
                return
            n = int(m.group(1))
            if n <= self._current and self._current:
                self.per_session.append(self._current)
            self._current = n

        def finish(self) -> list[int]:
            if self._current:
                self.per_session.append(self._current)
                self._current = 0
            return self.per_session

    em_counter = _EMIterationCounter()
    logging.getLogger("splink").addHandler(em_counter)

    # The SAME five fields the GM harness compares, at the same shape: two
    # jaro-winkler name fields, a levenshtein date, and two more jaro-winkler.
    # Splink expresses levels as thresholds and GM as `levels=3` with a partial
    # cut; both give three informative levels per field.
    settings = SettingsCreator(
        link_type="dedupe_only",
        # The fixture's id column. Without this Splink looks for a literal
        # `unique_id`, does not find it, and prints "SETTINGS VALIDATION:
        # Errors were identified in your settings dictionary ... Missing
        # column(s) from input dataframe(s): `unique_id`" -- then continues.
        # A validation error it recovers from is exactly the kind of defect
        # that produces a number nobody can trust.
        unique_id_column_name="record_id",
        blocking_rules_to_generate_predictions=[block_on("blk"), block_on("last")],
        comparisons=[
            cl.JaroWinklerAtThresholds("first", [0.9, 0.7]),
            cl.JaroWinklerAtThresholds("last", [0.9, 0.7]),
            cl.DamerauLevenshteinAtThresholds("dob", [1, 2]),
            cl.JaroWinklerAtThresholds("zip", [0.9, 0.7]),
            cl.JaroWinklerAtThresholds("city", [0.9, 0.7]),
        ],
    )
    # See the note in build_session: `checkpoint` needs a distributed
    # filesystem this lane does not have. Declared here, and echoed into the
    # artifact below, so no reader has to infer it from the absence of a
    # checkpoint dir.
    linker = Linker(
        df, settings,
        db_api=SparkAPI(spark_session=spark, break_lineage_method="persist"),
    )

    # u, from random pairs -- the same quantity the GM harness times as `u`.
    t = time.perf_counter()
    # SEEDED, and this is the difference between a measurement and a lottery
    # ticket. Unseeded, each run draws a different random pair sample, gets
    # different u estimates, starts EM somewhere else and converges in a
    # different number of iterations -- and every iteration is a distributed
    # Spark job, so the wall follows. Two runs of the IDENTICAL configuration
    # (31983859191, 31983866350) logged 33 and 16 iterations and came out at
    # 321.12s and 197.29s: a 63% spread, against GM's 1.4% across the same pair
    # of runs. Any single unseeded Splink number is drawn from that.
    linker.training.estimate_u_using_random_sampling(
        max_pairs=args.max_pairs, seed=args.seed)
    out["stages"]["u_seconds"] = round(time.perf_counter() - t, 2)
    print(f"[splink] u in {out['stages']['u_seconds']}s", flush=True)

    # m, by EM, once per blocking rule -- the GM harness's per-pass sessions.
    t = time.perf_counter()
    for rule in (block_on("blk"), block_on("last")):
        linker.training.estimate_parameters_using_expectation_maximisation(rule)
    out["stages"]["em_seconds"] = round(time.perf_counter() - t, 2)
    iters = em_counter.finish()
    out["em_iterations_per_session"] = iters
    out["em_iterations_total"] = sum(iters)
    out["em_seconds_per_iteration"] = (
        round(out["stages"]["em_seconds"] / sum(iters), 3) if sum(iters) else None
    )
    out["seed"] = args.seed
    print(f"[splink] EM in {out['stages']['em_seconds']}s over {sum(iters)} "
          f"iteration(s) {iters}", flush=True)

    # The trained model itself, not just how long it took to train. GM records
    # m_probs / u_probs / match_weights and this side recorded neither, so when
    # the two engines disagreed on accuracy there was no way to ask WHERE --
    # which field, which level. A per-level m/u table makes the next question
    # answerable from the artifact instead of another run.
    try:
        model = linker.misc.save_model_to_json()
        out["model"] = {
            c["output_column_name"]: [
                {"label": lv.get("comparison_vector_value"),
                 "sql": lv.get("sql_condition"),
                 "m": lv.get("m_probability"),
                 "u": lv.get("u_probability")}
                for lv in c.get("comparison_levels", [])
            ]
            for c in model.get("comparisons", [])
        }
        out["probability_two_random_records_match"] = model.get(
            "probability_two_random_records_match")
    except Exception as e:  # noqa: BLE001 - diagnostic, never fatal
        # Recorded rather than swallowed: an absent `model` key must read as
        # "the export broke", not as "the model was empty".
        out["model_export_error"] = f"{type(e).__name__}: {str(e)[:200]}"

    if args.eval_quality:
        # Ground truth is a pure function of the row id and needs no extra
        # column: `build_fixture` assigns entity = id % n_entities with
        # n_entities = rows // dup, so two rows are a true pair exactly when
        # their ids are congruent. Deriving it here rather than carrying a
        # label column means the fixture the engines TRAIN on is byte-identical
        # to the one the speed runs use -- a label column would change the
        # frame and quietly make the two experiments different.
        from pyspark.sql import functions as F

        n_entities = max(args.rows // max(args.dup, 1), 1)
        t = time.perf_counter()
        preds = linker.inference.predict().as_spark_dataframe()
        truth = (F.col("record_id_l") % F.lit(n_entities)
                 == F.col("record_id_r") % F.lit(n_entities))
        rows_ = (preds.select(F.col("match_weight").alias("w"),
                              truth.alias("is_true"))
                      .collect())
        out["stages"]["predict_seconds"] = round(time.perf_counter() - t, 2)
        out["quality"] = _metrics_module().ranking_metrics(
            [(float(r["w"]), bool(r["is_true"])) for r in rows_]
        )
        print(f"[splink] quality {out['quality']}", flush=True)

    out["stages"]["total_seconds"] = round(time.perf_counter() - t_all, 2)
    # `train_total` is the number to put beside GM's u + counts + train. The
    # fixture build is excluded from BOTH: it is the harness, not the engine.
    out["train_total_seconds"] = round(
        out["stages"]["u_seconds"] + out["stages"]["em_seconds"], 2
    )
    print(f"[splink] DONE rows={actual:,} executors={n_exec} train_total="
          f"{out['train_total_seconds']}s stages={out['stages']}", flush=True)

    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[splink] wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
