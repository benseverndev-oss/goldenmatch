"""Diagnose WHERE the batched Spark plan exhausts the heap.

Three attempts to fix this by reasoning have failed:

  1. "unbounded batches"      -> bounded to 10,000. Still OOM.
  2. "pandas is heavy"        -> converted to arrow_udf. Still OOM.

Both changes were independently right and neither addressed this. So this script
stops guessing and BISECTS THE PLAN: it runs progressively more of the batched
pipeline and reports the first stage that fails. The row-shaped path completes
the same workload on the same box every time, so the failure is specific to this
plan and one of these stages owns it.

Each stage ends in `.count()` -- an action, so Spark actually executes it rather
than building a lazy plan that never runs. Stages are cumulative: stage N+1 is
stage N plus one operator, so the first failure names the operator.

    1  candidates            block self-join only
    2  + join to source      pair -> (a, b, x, y)
    3  + groupBy/collect     the batch array, NO UDF        <- exonerates the UDF
    4  + UDF                 scoring
    5  + zip/explode         back to one row per pair

If stage 3 fails, the UDF and the explode are innocent and the problem is
grouping 1.9M rows into arrays at all. If stage 5 fails alone, `arrays_zip`
building a second array of structs is the cost.

`--rows` is swept so a constant-too-large is distinguishable from something
quadratic: the pair count is quadratic in block size but linear in rows here.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import traceback

_ID = "__row_id__"
_SCHEMA = f"{_ID} long, blk string, name string"


def _rows(n_rows: int, block_size: int):
    return [
        (i, f"blk{i // block_size}", f"v{i // block_size}_{(i % block_size) // 2}")
        for i in range(n_rows)
    ]


def _candidates(df):
    from pyspark.sql import functions as F

    a, b = df.alias("a"), df.alias("b")
    return a.join(
        b,
        (F.col("a.blk") == F.col("b.blk")) & (F.col(f"a.{_ID}") < F.col(f"b.{_ID}")),
    ).select(F.col(f"a.{_ID}").alias("a"), F.col(f"b.{_ID}").alias("b"))


def _joined(df, pairs):
    from pyspark.sql import functions as F

    lhs, rhs = "__lhs__", "__rhs__"
    return (
        pairs.alias("__p__")
        .join(df.alias(lhs), F.col(f"{lhs}.{_ID}") == F.col("__p__.a"))
        .join(df.alias(rhs), F.col(f"{rhs}.{_ID}") == F.col("__p__.b"))
        .select(
            F.col("__p__.a").alias("a"),
            F.col("__p__.b").alias("b"),
            F.col(f"{lhs}.name").cast("string").alias("x"),
            F.col(f"{rhs}.name").cast("string").alias("y"),
        )
    )


def _grouped(df, pairs, batch_size):
    from goldenmatch.spark.batched import batch_key
    from pyspark.sql import functions as F

    return _joined(df, pairs).groupBy(
        batch_key("partition", batch_size).alias("__batch__")
    ).agg(F.collect_list(F.struct("a", "b", "x", "y")).alias("rows"))


def _scored(df, pairs, batch_size, udf_name, scorer_id):
    from pyspark.sql import functions as F

    g = _grouped(df, pairs, batch_size)
    return g.select(
        F.col("rows"),
        F.call_udf(
            udf_name,
            F.lit(int(scorer_id)),
            F.transform(F.col("rows"), lambda r: r["x"]),
            F.transform(F.col("rows"), lambda r: r["y"]),
        ).alias("scores"),
    )


def _scored_forced(df, pairs, batch_size, udf_name, scorer_id):
    """Stage 4 with the UDF's output actually CONSUMED.

    Stage 4 ends in ``.count()``, and counting rows does not need the `scores`
    column -- Catalyst prunes unused columns, and a deterministic UDF with no
    side effects is exactly what it is entitled to prune. So stage 4 measuring
    the same wall as stage 3 does NOT mean scoring is free; it may mean scoring
    never happened. Reporting that as "the JNI call costs nothing" would be a
    confident claim built on an optimisation.

    So reduce each batch's score array to one number. The UDF's result is now
    load-bearing and cannot be pruned, while the row count stays at one per
    batch -- no explode, so this isolates SCORING from UN-BATCHING, which is the
    split that decides whether a faster kernel could move the number at all.
    """
    from pyspark.sql import functions as F

    s = _scored(df, pairs, batch_size, udf_name, scorer_id)
    return s.select(
        F.aggregate(F.col("scores"), F.lit(0.0), lambda acc, x: acc + x).alias("total")
    ).agg(F.sum("total"))


def _exploded(df, pairs, batch_size, udf_name, scorer_id):
    from pyspark.sql import functions as F

    s = _scored(df, pairs, batch_size, udf_name, scorer_id)
    return s.select(F.explode(F.arrays_zip(F.col("rows"), F.col("scores"))).alias("z"))


def _deduped(df, pairs, batch_size, udf_name, scorer_id):
    from pyspark.sql import functions as F

    e = _exploded(df, pairs, batch_size, udf_name, scorer_id)
    flat = e.select(
        F.col("z.rows.a").alias("a"),
        F.col("z.rows.b").alias("b"),
        F.col("z.scores").alias("score"),
    )
    return flat.groupBy("a", "b").agg(F.max("score").alias("score"))


def _shipped(df, pairs, batch_size, udf_name, scorer_id):
    """Stages 1-6 rebuilt through the SHIPPED functions, not this file's copies.

    Everything above is a reimplementation. That is deliberate -- rebuilding the
    plan locally is what lets a single operator be isolated -- but it makes every
    stage above a LOOKALIKE, and this project's own lesson from the last round of
    this investigation was to measure the shipped basis rather than something
    adjacent to it.

    So if stage 6 passes and this fails, the difference is not in the plan at
    all: it is in `batched.py`, and the bisect above has been exonerating code
    that never ran.
    """
    from goldenmatch.spark.batched import dedup_max, score_pairs_batched

    scored = score_pairs_batched(
        pairs, df, id_col=_ID, value_col="name",
        scorer_id=scorer_id, udf_name=udf_name, batch_size=batch_size,
    )
    return dedup_max(scored)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=200_000)
    ap.add_argument("--block-size", type=int, default=20)
    ap.add_argument("--batch-size", type=int, default=10_000)
    ap.add_argument(
        "--scorer",
        default="exact",
        help="Scorer for the UDF stages. Use the BENCH's scorer (jaro_winkler) "
             "when the question is attribution rather than the OOM: stage 3 runs "
             "no UDF at all, so `stage 3 vs stage 7` splits the wall between the "
             "PLAN (shuffle + collect_list + explode, which Connect forces on "
             "any batched UDF) and the SCORING. With a different scorer the two "
             "halves are not comparable to the bench number.",
    )
    args = ap.parse_args(argv)

    from goldenmatch.spark.jvm import find_jar, install, scorer_id
    from pyspark.sql import SparkSession

    remote = os.environ.get("GOLDENMATCH_SPARK_REMOTE", "local[*]")
    spark = SparkSession.builder.remote(remote).getOrCreate()
    pyenv = os.environ.get("GOLDENMATCH_SPARK_PYENV")
    if pyenv:
        from goldenmatch.spark.deps import ship_python_environment

        ship_python_environment(spark, pyenv)
    udf_name = install(spark, jar=find_jar())

    df = spark.createDataFrame(_rows(args.rows, args.block_size), _SCHEMA).cache()
    pairs = _candidates(df)
    sid = scorer_id(args.scorer)

    stages = [
        ("1 candidates", lambda: pairs.count()),
        ("2 + join to source", lambda: _joined(df, pairs).count()),
        ("3 + groupBy/collect (NO UDF)",
         lambda: _grouped(df, pairs, args.batch_size).count()),
        ("4 + UDF (count only)",
         lambda: _scored(df, pairs, args.batch_size, udf_name, sid).count()),
        # THE attribution stage. 4 counts rows, which does not need the scores
        # column -- so Catalyst is free to prune the UDF and 4 can match 3 while
        # scoring nothing. 4b consumes every score, without exploding, so
        # (4b - 3) is SCORING and (5 - 4b) is UN-BATCHING. That split decides
        # whether a faster kernel could move this number at all.
        ("4b + UDF (scores CONSUMED)",
         lambda: _scored_forced(df, pairs, args.batch_size, udf_name, sid).count()),
        ("5 + zip/explode",
         lambda: _exploded(df, pairs, args.batch_size, udf_name, sid).count()),
        # The bench does this and the earlier bisect did not, so it was the one
        # untested difference between a plan that completes and a bench that
        # OOMs. Testing it here rules it in or out independently of the harness
        # isolation fix, rather than fixing two things and learning nothing.
        ("6 + dedup_max (groupBy a,b)",
         lambda: _deduped(df, pairs, args.batch_size, udf_name, sid).count()),
        # The whole thing through the SHIPPED functions. If 6 passes and this
        # fails, the plan is innocent and `batched.py` differs from the copy
        # above in some way this bisect has been blind to.
        ("7 SHIPPED score_pairs_batched + dedup_max",
         lambda: _shipped(df, pairs, args.batch_size, udf_name, sid).count()),
    ]

    # The heap ceiling, before anything else. An OOM at an unknown heap size
    # measures a configuration rather than a design, and `local[*]` takes
    # Spark's default (1g) unless SPARK_DRIVER_MEMORY was set before the JVM
    # launched -- a client-side `spark.driver.memory` is too late to change -Xmx.
    # A Connect client has no SparkContext to ask, but the jar's probe runs IN
    # the JVM that knows.
    try:
        from goldenmatch.spark.jvm import implementation

        impl, diagnostics, runtime = implementation(spark)
        print(f"\nexecutor JVM: {runtime}\nscorer: {impl}\n  {diagnostics}",
              flush=True)
    except Exception as exc:  # noqa: BLE001 - diagnostics must never be fatal
        print(f"\ncould not read the executor JVM info: {exc}", flush=True)

    print(f"\nrows={args.rows:,} block_size={args.block_size} "
          f"batch_size={args.batch_size:,}", flush=True)
    print("=" * 66, flush=True)
    first_failure = None
    for name, fn in stages:
        t0 = time.perf_counter()
        try:
            n = fn()
            print(f"  OK    {name:32} -> {n:>12,}  ({time.perf_counter()-t0:.1f}s)",
                  flush=True)
        except Exception as exc:  # noqa: BLE001 - the whole point is to report
            kind = type(exc).__name__
            oom = "OutOfMemory" in str(exc) or "OutOfMemory" in repr(exc)
            print(f"  FAIL  {name:32} -> {kind}{' (OOM)' if oom else ''}", flush=True)
            print(f"        {str(exc).strip().splitlines()[0][:160]}", flush=True)
            first_failure = name
            break

    print("=" * 66)
    if first_failure is None:
        print("  Every stage completed. The plan is not the problem at this size;")
        print("  raise --rows until it breaks, or the difference is elsewhere.")
        return 0
    print(f"  FIRST FAILING STAGE: {first_failure}")
    print()
    if first_failure.startswith("3"):
        print("  The UDF and the explode are INNOCENT. Grouping the pairs into")
        print("  arrays is itself what exhausts the heap -- the shuffle plus the")
        print("  collected structs, before any scoring happens.")
    elif first_failure.startswith("4"):
        print("  Grouping is fine; the UDF call is where it goes. The derived")
        print("  `transform` arrays duplicate every string in the batch.")
    elif first_failure.startswith("6"):
        print("  The batched plan is fine; the MAX dedup over 1.9M exploded rows")
        print("  is the cost. That is shared with the row-shaped path, which does")
        print("  the same groupBy -- so it is not what makes this plan special.")
    elif first_failure.startswith("5"):
        print("  Scoring is fine; `arrays_zip` building a second array of structs")
        print("  is the cost.")
    else:
        print("  The failure is UPSTREAM of the batching entirely -- shared with")
        print("  the row-shaped path, so it is not what makes this plan special.")
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
