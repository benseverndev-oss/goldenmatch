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
from typing import Any


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


def _shuffle_module():
    """The SHARED shuffle-metrics reader, imported by file path.

    Same reasoning as the ranking metric: one implementation, so a difference
    between the two engines' numbers is a difference between the ENGINES.
    """
    import importlib.util

    path = Path(__file__).resolve().parent / "_spark_shuffle_metrics.py"
    spec = importlib.util.spec_from_file_location("_spark_shuffle_metrics", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _tuning_module():
    """The SHARED Spark tuning, imported by file path.

    Same reasoning as the shuffle reader and the ranking metric: both arms have
    to be tuned by ONE implementation, or the benchmark measures the tuning
    rather than the engines.
    """
    import importlib.util

    path = Path(__file__).resolve().parent / "_spark_tuning.py"
    spec = importlib.util.spec_from_file_location("_spark_tuning", path)
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


_SIZE_SUFFIX = re.compile(r"^\s*(\d+)\s*([kKmMgGtT])[bB]?\s*$")


def _normalize_gs_size_props(spark: Any) -> None:
    """Rewrite suffixed `fs.gs.*` sizes to plain bytes, and say what it found.

    Runs 32269692605 and 32277069612 both died initializing the filesystem with

        java.lang.NumberFormatException: For input string: "64m"

    under gcs-connector `hadoop3-2.2.21` AND `3.1.17`. Two unrelated connector
    versions failing identically means the value is not a connector default: it
    is in this environment's Hadoop configuration, and the connector reads it
    with `getInt`/`getLong`, which reject unit suffixes.

    Nothing in this repo sets it, checked before writing this. So it arrives
    from the image or from Spark itself -- exactly the situation where guessing
    a key costs a five-VM cluster per guess. Enumerate instead of guessing.

    Every `fs.gs.*` value shaped like `64m` is rewritten to its byte count,
    which is what the connector expects and what the suffix means. Suffixed
    values under OTHER prefixes are PRINTED but not touched: they belong to
    components that may well accept suffixes, and silently rewriting config
    this script does not own would be a worse bug than the one it fixes.
    """
    try:
        hconf = spark.sparkContext._jsc.hadoopConfiguration()  # noqa: SLF001
        it = hconf.iterator()
    except Exception as exc:  # pragma: no cover - diagnostic path only
        print(f"[splink] could not inspect hadoop conf: {exc}", flush=True)
        return

    fixed: list[str] = []
    seen: list[str] = []
    while it.hasNext():
        entry = it.next()
        key, val = str(entry.getKey()), str(entry.getValue())
        m = _SIZE_SUFFIX.match(val)
        if not m:
            continue
        seen.append(f"{key}={val}")
        if key.startswith("fs.gs."):
            mult = {"k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}[m.group(2).lower()]
            as_bytes = int(m.group(1)) * mult
            hconf.set(key, str(as_bytes))
            fixed.append(f"{key}: {val} -> {as_bytes}")

    if seen:
        print(f"[splink] suffixed size props: {', '.join(sorted(seen))}", flush=True)
    print(
        f"[splink] normalized for the GCS connector: {', '.join(fixed)}"
        if fixed
        else "[splink] no suffixed fs.gs.* props found",
        flush=True,
    )


def build_session(
    master: str,
    driver_host: str | None,
    wait_s: int,
    checkpoint_dir: str | None = None,
    executor_memory: str | None = None,
    executor_cores: int | None = None,
    expect_executors: int = 0,
):
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
    # EXECUTOR SIZING, matched to GoldenMatch's. This session set neither, so it
    # took Spark's DEFAULT `spark.executor.memory=1g` while GM's Connect server
    # is launched by the workflow with
    #
    #     --conf spark.executor.cores=16 --conf spark.executor.memory=48g
    #
    # A 48x memory disadvantage is not a comparison. It explains the OOM
    # (`exited with code 52`) at 50M, the executors lost and relaunched into the
    # teens, and -- at 1M, where Splink completed -- the 4.09 GB memory / 1.88 GB
    # disk spill against GoldenMatch's zero. That spill was previously read as a
    # structural property of the engines; on this evidence it is an artefact of
    # how much heap each arm was given.
    if executor_memory:
        b = b.config("spark.executor.memory", executor_memory)
    if executor_cores:
        b = b.config("spark.executor.cores", str(executor_cores))
    if driver_host:
        b = b.config("spark.driver.host", driver_host)
    if checkpoint_dir and checkpoint_dir.startswith("gs://"):
        # The GCS Hadoop connector, so `gs://` resolves at all. The Spark image
        # does not bundle it, and without it a gs:// checkpoint dir fails with
        # "No FileSystem for scheme: gs".
        #
        # Auth comes from the VM's metadata server: the nodes are created with
        # `--scopes=cloud-platform` and run as the default compute service
        # account, which holds roles/editor. No key material is passed here.
        #
        # **The 3.x line, not `hadoop3-2.2.x`.** Run 32269692605 pinned
        # `hadoop3-2.2.21` and died initializing the filesystem:
        #
        #     WARN FileSystem: Failed to initialize filesystem gs://...:
        #     java.lang.NumberFormatException: For input string: "64m"
        #
        # The 2.2.x connector reads its size properties through Hadoop's
        # `getLong`, which rejects unit suffixes; the 3.x line reads them with
        # `getLongBytes`, which accepts them. 2.2.x also predates Spark 4 (it
        # targets the Hadoop 3.3 era), so pinning it against this image was
        # asking a Hadoop-3.3 connector to parse Hadoop-3.4 defaults.
        #
        # 3.x also REPLACED the auth switch: `google.cloud.auth.service.account
        # .enable=true` is gone, and `fs.gs.auth.type=APPLICATION_DEFAULT` is
        # the equivalent. It resolves through the metadata server exactly as
        # before, so this is the same credential path under a new key, not a
        # new grant.
        # **The SHADED jar, shipped as a file, not `spark.jars.packages`.**
        # Run 32274795307 used the Maven coordinate and got further, then died:
        #
        #     NoClassDefFoundError:
        #     com/google/cloud/hadoop/util/interceptors/LoggingInterceptor
        #
        # `spark.jars.packages` resolves the PLAIN artifact (155 KB, classes
        # only) and leaves Ivy to find its transitive deps, which it did not do
        # compatibly against this image. The `-shaded` classifier is the
        # self-contained 38 MB jar that carries them. Ivy cannot express a
        # classifier through `spark.jars.packages` at all, so the coordinate
        # form CANNOT reach the artifact that works -- shipping the file is not
        # a workaround here, it is the only route.
        #
        # Shipping it also removes Maven resolution from the critical path of a
        # paid cluster: the earlier run was fetching grpc-xds and friends at
        # session build.
        gcs_jar = os.environ.get("GCS_CONNECTOR_JAR", "/w/gcs-connector-shaded.jar")
        if not os.path.exists(gcs_jar):
            # Loudly, rather than falling back to a session that cannot reach
            # gs:// -- a silent fallback here would restate itself minutes later
            # as an unreadable checkpoint failure on a five-VM cluster.
            raise SystemExit(
                f"gs:// checkpoint dir requested but the GCS connector jar is missing at {gcs_jar}. "
                "Set GCS_CONNECTOR_JAR, or the workflow's jar download did not land."
            )
        b = (
            b.config("spark.jars", f"{similarity_jar_location()},{gcs_jar}")
            .config(
                "spark.hadoop.fs.gs.impl", "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFileSystem"
            )
            .config(
                "spark.hadoop.fs.AbstractFileSystem.gs.impl",
                "com.google.cloud.hadoop.fs.gcs.GoogleHadoopFS",
            )
            .config("spark.hadoop.fs.gs.auth.type", "APPLICATION_DEFAULT")
        )
    spark = b.getOrCreate()

    # `parquet` -- Splink's ACTUAL default -- reads its write path from
    # `sc.setCheckpointDir`, so the dir has to be set even though nothing calls
    # `.checkpoint()`. See `_get_checkpoint_dir_path` in
    # splink/internals/spark/database_api.py.
    if checkpoint_dir:
        _normalize_gs_size_props(spark)
        spark.sparkContext.setCheckpointDir(checkpoint_dir)
        print(f"[splink] checkpoint dir {checkpoint_dir}", flush=True)

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
    # Waits for the EXPECTED count, not merely for one. `n > 0` returned on the
    # first executor to check in, so run 32287895819 reported "2 executor(s)
    # registered" -- a SNAPSHOT of a cluster still filling up, not its size.
    # That number is not cosmetic: it is recorded in the artifact as the
    # cluster Splink ran on, and the shuffle-partition tuning derives from the
    # cores behind it, which is how that run tuned to 160 partitions instead of
    # 320. A count taken too early under-provisions the arm and then documents
    # the wrong reason for it.
    deadline = time.time() + wait_s
    n = 0
    while time.time() < deadline:
        try:
            n = spark.sparkContext._jsc.sc().getExecutorMemoryStatus().size() - 1
        except Exception:  # noqa: BLE001 - probe only
            n = 0
        if n >= expect_executors > 0:
            break
        if expect_executors <= 0 and n > 0:
            break
        time.sleep(2)
    if 0 < n < expect_executors:
        # Not fatal: a short cluster is still measurable, and refusing would
        # throw away the GoldenMatch arm that already ran. But it must be LOUD,
        # because a quietly under-provisioned arm reads as an engine limit.
        print(
            f"::warning::only {n}/{expect_executors} executors registered within "
            f"{wait_s}s; this arm is under-provisioned and its wall is not "
            f"comparable to the other's",
            flush=True,
        )
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
    ap.add_argument(
        "--master", default=os.environ.get("SPLINK_SPARK_MASTER", "spark://localhost:7077")
    )
    ap.add_argument("--driver-host", default=os.environ.get("SPLINK_DRIVER_HOST", ""))
    ap.add_argument("--executor-wait", type=int, default=120)
    ap.add_argument("--expect-executors", type=int, default=0,
                    help="Wait for this many executors before proceeding. 0 waits for one. "
                         "A snapshot taken while the cluster is still filling up both "
                         "under-provisions this arm and mis-derives the partition count.")
    ap.add_argument("--executor-memory", default="",
                    help="spark.executor.memory. MUST match what the workflow gives the "
                         "Connect server for GoldenMatch, or the arms differ by heap size "
                         "rather than by engine. Empty leaves Spark's 1g default.")
    ap.add_argument("--executor-cores", type=int, default=0,
                    help="spark.executor.cores. Same symmetry requirement as memory.")
    ap.add_argument(
        "--max-pairs",
        type=int,
        default=1_000_000,
        help="Splink's u-estimation sample. Matches the GM harness's "
        "--u-max-pairs so the u stage is comparable.",
    )
    ap.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Seeds Splink's u random sampling. Unseeded, the EM "
        "iteration count -- and therefore the wall -- varies "
        "by more than half between identical runs.",
    )
    ap.add_argument(
        "--eval-quality",
        action="store_true",
        help="REGRESSION GUARD on the distributed trainer: score "
        "the candidate pairs and report ranking quality "
        "against the fixture's known entity structure. NOT a "
        "GM-vs-Splink accuracy verdict -- this fixture's "
        "config was chosen to stress prod(levels+1) for a "
        "scale test. See the bakeoff for accuracy.",
    )
    ap.add_argument(
        "--spark-ui",
        default="http://localhost:4040",
        help="Spark UI of THIS classic driver (it runs on the host "
        "and binds 4040). Read for stage-level shuffle bytes.",
    )
    ap.add_argument(
        "--checkpoint-dir",
        default=os.environ.get("SPLINK_CHECKPOINT_DIR", ""),
        help="Distributed-filesystem path for Splink's lineage "
        "break (e.g. gs://bucket/prefix). REQUIRED for the "
        "documented `parquet` default; without one this falls "
        "back to `persist`, which does NOT truncate lineage "
        "and OOMs at scale.",
    )
    ap.add_argument(
        "--shuffle-partitions",
        type=int,
        default=0,
        help="spark.sql.shuffle.partitions. 0 leaves Spark's default, "
        "-1 derives 5x total executor cores per Splink's guide. "
        "Applied IDENTICALLY to both arms via scripts/_spark_tuning.py.",
    )
    ap.add_argument("--out", default="splink-train-scale.json")
    args = ap.parse_args()

    out: dict = {
        "engine": "splink",
        "rows_requested": args.rows,
        "master": args.master,
        "stages": {},
        "session_type": "classic",
        "session_confound": (
            "GM runs over Spark Connect (addArtifact is Connect-only); Splink "
            "cannot (it uses sparkContext). Same cluster, different front door."
        ),
        # `parquet` is Splink's ACTUAL default -- verified in
        # splink/internals/spark/database_api.py:
        #     elif not self.break_lineage_method:
        #         self.break_lineage_method = "parquet"
        #
        # An earlier version of this harness ran `persist` and recorded that
        # Splink's default was `checkpoint`. Both were wrong, and the second
        # error hid the first: `persist()` CACHES but keeps the lineage for
        # fault recovery, while `checkpoint`/`parquet` materialise and CUT it.
        # Splink's performance guide is explicit that without breaking lineage
        # "big jobs fail to complete", and at 50M that is exactly what happened
        # -- executors died with exit 52 (JVM OOM) after 33 EM iterations built
        # an unbounded DAG.
        #
        # The old note argued the deviation "biases the wall in Splink's
        # favour". True of wall-clock, false of MEMORY, which is the axis it
        # died on. A configuration that cannot finish is not a favour.
        "break_lineage_method": ("parquet" if args.checkpoint_dir else "persist"),
        "break_lineage_note": (
            "parquet == Splink's own default, backed by a distributed "
            "filesystem, which is what its docs assume. Splink pays real "
            "GCS write+read per lineage break; GoldenMatch pays none because "
            "its counting stage has no iterative DAG to truncate. That "
            "asymmetry is a property of the engines, not of this harness."
            if args.checkpoint_dir
            else "NO --checkpoint-dir given, so this fell back to `persist`, which "
            "does NOT truncate lineage. Expect OOM at scale. This is a "
            "misconfiguration, not a Splink limit."
        ),
        "checkpoint_dir": args.checkpoint_dir or None,
    }
    t_all = time.perf_counter()

    spark, n_exec = build_session(
        args.master,
        args.driver_host or None,
        args.executor_wait,
        args.checkpoint_dir or None,
        executor_memory=args.executor_memory or None,
        executor_cores=args.executor_cores or None,
        expect_executors=args.expect_executors,
    )
    out["executors"] = n_exec
    out["expected_executors"] = args.expect_executors or None
    out["executor_memory"] = args.executor_memory or "(spark default 1g)"
    out["executor_cores"] = args.executor_cores or None
    # Applied AFTER build_session, which waits for executors to register: the
    # core count is read from the running cluster, and reading it before
    # registration would derive the partition count from zero cores.
    out["shuffle_partitions"] = _tuning_module().apply_shuffle_partitions(
        spark, args.shuffle_partitions, spark_ui=args.spark_ui
    )

    fx = _fixture_module()
    t = time.perf_counter()
    df = fx.build_fixture(spark, args.rows, args.dup, args.blocks_per_key)
    df = df.withColumnRenamed("__row_id__", "record_id")
    actual = df.count()
    out["stages"]["fixture_seconds"] = round(time.perf_counter() - t, 2)
    out["actual_rows"] = int(actual)
    print(f"[splink] fixture: {actual:,} rows in {out['stages']['fixture_seconds']}s", flush=True)

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
        df,
        settings,
        db_api=SparkAPI(spark_session=spark, break_lineage_method=out["break_lineage_method"]),
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
    linker.training.estimate_u_using_random_sampling(max_pairs=args.max_pairs, seed=args.seed)
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
    print(
        f"[splink] EM in {out['stages']['em_seconds']}s over {sum(iters)} iteration(s) {iters}",
        flush=True,
    )

    # The trained model itself, not just how long it took to train. GM records
    # m_probs / u_probs / match_weights and this side recorded neither, so when
    # the two engines disagreed on accuracy there was no way to ask WHERE --
    # which field, which level. A per-level m/u table makes the next question
    # answerable from the artifact instead of another run.
    try:
        model = linker.misc.save_model_to_json()
        out["model"] = {
            c["output_column_name"]: [
                {
                    "label": lv.get("comparison_vector_value"),
                    "sql": lv.get("sql_condition"),
                    "m": lv.get("m_probability"),
                    "u": lv.get("u_probability"),
                }
                for lv in c.get("comparison_levels", [])
            ]
            for c in model.get("comparisons", [])
        }
        out["probability_two_random_records_match"] = model.get(
            "probability_two_random_records_match"
        )
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
        truth = F.col("record_id_l") % F.lit(n_entities) == F.col("record_id_r") % F.lit(n_entities)
        # GROUP BY in the engine, exactly as the GM arm does. Both sides must
        # use the same reduction or the comparison measures the harness: this
        # collected one tuple per PREDICTED PAIR, which at 50M is hundreds of
        # millions of Python objects on a driver in a container.
        #
        # Splink's match_weight is a real-valued log-odds sum, so its distinct
        # count is larger than the GM arm's bounded-gamma weight -- grouping
        # buys less here. It is still exact and still strictly smaller, and
        # `quality_score_groups` below records how much it actually collapsed,
        # so an unbounded group count shows up as a number rather than as an
        # OOM. See `ranking_metrics_grouped` for why grouping is exact.
        grouped = (
            preds.select(F.col("match_weight").alias("w"), truth.alias("is_true"))
            .groupBy("w")
            .agg(
                F.sum(F.col("is_true").cast("long")).alias("n_true"),
                F.count(F.lit(1)).alias("n_all"),
            )
            .collect()
        )
        out["stages"]["predict_seconds"] = round(time.perf_counter() - t, 2)
        out["quality_score_groups"] = len(grouped)
        out["quality"] = _metrics_module().ranking_metrics_grouped(
            [(float(r["w"]), int(r["n_true"]), int(r["n_all"])) for r in grouped]
        )
        print(f"[splink] quality {out['quality']}", flush=True)

    # Same instrument, same reasoning as the GM arm -- see there. Splink
    # re-scans pairs once per EM iteration, so its exchange should scale with
    # PAIRS and repeat ~26 times, against GM's bounded pattern output.
    out["shuffle"] = _shuffle_module().fetch(args.spark_ui)
    print(f"[splink] shuffle {out['shuffle']}", flush=True)

    out["stages"]["total_seconds"] = round(time.perf_counter() - t_all, 2)
    # `train_total` is the number to put beside GM's u + counts + train. The
    # fixture build is excluded from BOTH: it is the harness, not the engine.
    out["train_total_seconds"] = round(out["stages"]["u_seconds"] + out["stages"]["em_seconds"], 2)
    print(
        f"[splink] DONE rows={actual:,} executors={n_exec} train_total="
        f"{out['train_total_seconds']}s stages={out['stages']}",
        flush=True,
    )

    Path(args.out).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[splink] wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
