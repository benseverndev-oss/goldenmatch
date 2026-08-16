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
import os
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


def build_session(master: str, driver_host: str | None, wait_s: int,
                  checkpoint_dir: str):
    from pyspark.sql import SparkSession
    from splink.backends.spark import similarity_jar_location

    b = (
        SparkSession.builder.master(master)
        .appName("splink-train-scale")
        # Splink materialises many intermediate tables; the default 200 shuffle
        # partitions on a small cluster is pure scheduling overhead.
        .config("spark.sql.shuffle.partitions", "8")
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

    # Splink's Spark backend truncates lineage with `Dataset.checkpoint()`,
    # which raises "Checkpoint directory has not been set in the SparkContext"
    # unless one is set. The path has to resolve to the SAME storage from the
    # driver (this host) and the executors (containers), so the compose file
    # bind-mounts a host directory at an identical path inside the workers.
    # Left as Splink's default `checkpoint` break-lineage method deliberately:
    # switching to `persist` would dodge the mount but would also stop measuring
    # the configuration Splink actually recommends on Spark.
    Path(checkpoint_dir).mkdir(parents=True, exist_ok=True)
    spark.sparkContext.setCheckpointDir(f"file://{checkpoint_dir}")

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
    ap.add_argument("--checkpoint-dir", default=os.environ.get(
        "SPLINK_CHECKPOINT_DIR", "/tmp/spark-checkpoint"),
        help="Shared between this driver and the container executors. Must be "
             "the SAME path inside the workers -- see the bind mount in "
             "docker/spark-cluster/docker-compose.yml.")
    ap.add_argument("--out", default="splink-train-scale.json")
    args = ap.parse_args()

    out: dict = {
        "engine": "splink", "rows_requested": args.rows, "master": args.master,
        "stages": {}, "session_type": "classic",
        "session_confound": (
            "GM runs over Spark Connect (addArtifact is Connect-only); Splink "
            "cannot (it uses sparkContext). Same cluster, different front door."
        ),
    }
    t_all = time.perf_counter()

    spark, n_exec = build_session(args.master, args.driver_host or None,
                                  args.executor_wait, args.checkpoint_dir)
    out["executors"] = n_exec
    out["checkpoint_dir"] = args.checkpoint_dir

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
    linker = Linker(df, settings, db_api=SparkAPI(spark_session=spark))

    # u, from random pairs -- the same quantity the GM harness times as `u`.
    t = time.perf_counter()
    linker.training.estimate_u_using_random_sampling(max_pairs=args.max_pairs)
    out["stages"]["u_seconds"] = round(time.perf_counter() - t, 2)
    print(f"[splink] u in {out['stages']['u_seconds']}s", flush=True)

    # m, by EM, once per blocking rule -- the GM harness's per-pass sessions.
    t = time.perf_counter()
    for rule in (block_on("blk"), block_on("last")):
        linker.training.estimate_parameters_using_expectation_maximisation(rule)
    out["stages"]["em_seconds"] = round(time.perf_counter() - t, 2)
    print(f"[splink] EM in {out['stages']['em_seconds']}s", flush=True)

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
