"""J4: measure the Spark tier's scoring paths against each other.

The arc that built this tier (P0-P6, then score-cabi/J0/J1) has never produced a
wall-clock number, and section 1 of the spec claims a differentiator --
"vectorized Rust beats Spark SQL per core" -- that has never been measured. This
is the harness that stops that being an assertion.

## What it compares

    row_python    the shipped path: `score_and_dedup`, a pandas_udf. Spark
                  serialises an Arrow batch to a FORKED PYTHON WORKER, which
                  calls the scorer and sends results back.
    batched_jvm   J1's plan through J0's jar: pairs grouped into arrays, one
                  Java UDF call per batch, IN the executor JVM.

Both compute the same thing over the same pairs, so the delta is the mechanism.

## What it does NOT yet compare

Native scoring. J0's jar implements `exact` only, deliberately -- a Java
jaro-winkler would be a fourth implementation of a kernel that exists once in
Rust. So this measures the CALLING MECHANISM (Python worker vs in-JVM) with the
scoring held trivial, which is the honest question at this stage: if crossing to
a Python worker is not the bottleneck, J2's JNI work buys little, and it is
better to know that before writing it.

`exact` being cheap makes the comparison HARSHER on the JVM path, not kinder:
with almost no work per pair, per-call overhead is the whole signal.

## Reading the result

A ratio near 1.0 means the Python-worker hop is not where the time goes, and the
J-arc should be re-justified on grounds other than throughput. A large ratio
means the hop dominates and J2 is worth building. Either answer is useful; the
absence of one is not.

Run in CI (never locally -- see CLAUDE.md on scale benchmarks):
    .github/workflows/bench-spark-scoring.yml
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time

_ID = "__row_id__"
_SCHEMA = f"{_ID} long, blk string, name string"


def _rows(n_rows: int, block_size: int):
    """Rows in fixed-size blocks, half of each block sharing a value.

    Pair count is what actually drives scoring cost, and it is quadratic in
    block size -- so blocks are held to a fixed size and the row count is varied.
    Otherwise "twice the rows" would mean four times the pairs and the numbers
    would not be comparable across sizes.
    """
    out = []
    for i in range(n_rows):
        b = i // block_size
        out.append((i, f"blk{b}", f"v{b}_{(i % block_size) // 2}"))
    return out


def _time(fn, *, repeats: int) -> dict:
    """Median of `repeats` timed runs, plus the spread.

    Median rather than mean: a JVM warm-up or a stray GC pause skews a mean and
    the median is what a user experiences. The spread is reported so a reader can
    see whether the medians are separable at all.
    """
    samples = []
    for _ in range(repeats):
        t0 = time.perf_counter()
        n = fn()
        samples.append(time.perf_counter() - t0)
    return {
        "median_s": statistics.median(samples),
        "min_s": min(samples),
        "max_s": max(samples),
        "rows_out": n,
        "samples": [round(s, 4) for s in samples],
    }


def _candidate_pairs(df):
    from pyspark.sql import functions as F

    a, b = df.alias("a"), df.alias("b")
    return a.join(
        b,
        (F.col("a.blk") == F.col("b.blk")) & (F.col(f"a.{_ID}") < F.col(f"b.{_ID}")),
    ).select(F.col(f"a.{_ID}").alias("a"), F.col(f"b.{_ID}").alias("b"))


def _row_python(spark, df):
    """The shipped path: pandas_udf -> forked Python worker."""
    from goldenmatch.spark.scoring import score_and_dedup

    out = score_and_dedup(
        df, block_col="blk", value_col="name", id_col=_ID,
        scorer_name="jaro_winkler", threshold=0.0,
    )
    return out.count()


def _batched_jvm(spark, df, udf_name):
    """J1's plan through J0's jar: one call per batch, in the executor JVM."""
    from goldenmatch.spark.batched import dedup_max, score_pairs_batched
    from goldenmatch.spark.jvm import scorer_id

    scored = score_pairs_batched(
        _candidate_pairs(df), df, id_col=_ID, value_col="name",
        scorer_id=scorer_id("exact"), udf_name=udf_name,
    )
    return dedup_max(scored).count()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=200_000)
    ap.add_argument("--block-size", type=int, default=20)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default="spark-scoring-bench.json")
    args = ap.parse_args(argv)

    from goldenmatch.spark.jvm import JvmScorerUnavailable, find_jar, install
    from pyspark.sql import SparkSession

    remote = os.environ.get("GOLDENMATCH_SPARK_REMOTE", "local[*]")
    spark = SparkSession.builder.remote(remote).getOrCreate()

    # P1's executor environment, needed by the ROW_PYTHON path only. Real Spark
    # forks a Python worker with its own environment, so the client's
    # site-packages are absent -- the first run of this bench died with
    # `ModuleNotFoundError: No module named 'goldenmatch'` inside the worker,
    # which is P0's original failure rediscovered by omitting the step that
    # exists to fix it.
    #
    # Part of the result rather than a footnote: the batched_jvm path needs NONE
    # of this. Its scorer is already in the executor JVM, so there is no
    # interpreter to feed and no environment to ship -- a deployment difference
    # the timings below do not capture.
    pyenv = os.environ.get("GOLDENMATCH_SPARK_PYENV")
    if pyenv:
        from goldenmatch.spark.deps import ship_python_environment

        ship_python_environment(spark, pyenv)
        print(f"shipped executor env: {pyenv}", flush=True)
    else:
        print(
            "WARNING: no GOLDENMATCH_SPARK_PYENV -- the row_python path will "
            "fail on a real Spark backend",
            flush=True,
        )

    rows = _rows(args.rows, args.block_size)
    df = spark.createDataFrame(rows, _SCHEMA).cache()
    n_pairs = _candidate_pairs(df).count()
    print(
        f"rows={args.rows:,} block_size={args.block_size} -> {n_pairs:,} candidate pairs",
        flush=True,
    )

    results: dict = {
        "rows": args.rows,
        "block_size": args.block_size,
        "candidate_pairs": n_pairs,
        "repeats": args.repeats,
        "remote": remote,
        "paths": {},
    }

    print("\n[1/2] row_python (pandas_udf -> forked Python worker)", flush=True)
    results["paths"]["row_python"] = _time(
        lambda: _row_python(spark, df), repeats=args.repeats
    )

    try:
        udf_name = install(spark, jar=find_jar())
    except JvmScorerUnavailable as exc:
        print(f"::error::no JVM scorer jar: {exc}")
        return 2

    print("\n[2/2] batched_jvm (array UDF, in the executor JVM)", flush=True)
    results["paths"]["batched_jvm"] = _time(
        lambda: _batched_jvm(spark, df, udf_name), repeats=args.repeats
    )

    rp = results["paths"]["row_python"]["median_s"]
    bj = results["paths"]["batched_jvm"]["median_s"]
    results["speedup_row_python_over_batched_jvm"] = round(rp / bj, 3) if bj else None

    print("\n" + "=" * 68)
    print("RESULT")
    print("=" * 68)
    for name, r in results["paths"].items():
        print(
            f"  {name:14} median {r['median_s']:8.3f}s   "
            f"(min {r['min_s']:.3f} max {r['max_s']:.3f})  rows_out={r['rows_out']:,}"
        )
    ratio = results["speedup_row_python_over_batched_jvm"]
    print(f"\n  batched_jvm is {ratio}x the row_python path")
    print()
    if ratio is None:
        print("  INCONCLUSIVE.")
    elif ratio < 1.2:
        print("  The Python-worker hop is NOT where the time goes. J2's JNI work")
        print("  should be re-justified on grounds other than throughput -- the")
        print("  one-kernel argument still stands, the speed one does not.")
    else:
        print("  The Python-worker hop dominates. Removing it is worth doing, and")
        print("  J2 (JNI into score-cabi) is the way to remove the interpreter as")
        print("  well as the hop.")
    print()
    print("  CAVEAT, and it is load-bearing: the JVM path scores `exact` while")
    print("  the Python path scores jaro_winkler. This measures the CALLING")
    print("  MECHANISM, not the kernels -- and it flatters the Python path least,")
    print("  since `exact` does almost no work and leaves per-call overhead as the")
    print("  whole signal. A like-for-like kernel comparison needs J2.")

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
