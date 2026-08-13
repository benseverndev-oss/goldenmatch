"""J4: measure the Spark tier's scoring paths against each other.

The arc that built this tier (P0-P6, then score-cabi/J0/J1) has never produced a
wall-clock number, and section 1 of the spec claims a differentiator --
"vectorized Rust beats Spark SQL per core" -- that has never been measured. This
is the harness that stops that being an assertion.

## What it compares

    row_python    the shipped path: `score_and_dedup`, a pandas_udf. Spark
                  serialises an Arrow batch to a FORKED PYTHON WORKER, which
                  calls the scorer and sends results back.
    batched_jvm   J1's plan through the J2 jar: pairs grouped into arrays, one
                  Java UDF call per batch, scored by the Rust kernel over JNI,
                  IN the executor JVM. No Python worker at all.

Both compute the same thing over the same pairs, so the delta is the mechanism.

## Like-for-like, at last

This bench used to carry a load-bearing caveat: J0's jar implemented `exact`
only -- deliberately, because a Java jaro-winkler would have been a fourth
implementation of a kernel that exists once in Rust -- so the arms could not run
the same scorer. It measured the CALLING MECHANISM with the work held trivial,
and every reading of it had to be hedged.

J2 removed that. Both arms now run `jaro_winkler` by default (`--scorer`), and
the difference between them is the mechanism alone:

    row_python    Arrow batch -> serialise -> FORKED PYTHON WORKER -> Rust
                  (via the pyo3 wheel, when GOLDENMATCH_NATIVE=1 and the
                  shipped executor env carries it) -> serialise back
    batched_jvm   array UDF -> Rust, in the same JVM, no hop

Same kernel at the bottom of both. `score_one` either way.

## The arm asserts what it is

The jar falls back to the `exact`-only scorer when its native library will not
load. Benching that against a native row_python arm would produce a number for
the wrong thing, which is worse than no number because it looks like one -- so
the arm asks the EXECUTOR what it resolved and refuses to run if it is not the
kernel.

## One arm per PROCESS, and why

Both arms used to run in one script against one `local[*]` session, where the
DRIVER AND EXECUTOR SHARE A JVM. The second arm therefore started with whatever
the first had left resident -- a cached DataFrame plus three 1.9M-row shuffles --
and OOM'd. Three rounds of CI went into "fixing" a batched path that a plan
bisect later ran end to end in 21 seconds (run 31649168548): the benchmark was
measuring itself.

So each arm now runs in its own process, invoked separately, and the results are
combined afterwards. Nothing survives between them. It is also the only fair
shape for a comparison: whichever arm ran second was being systematically
penalised.

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


def _row_python(df, scorer_name: str):
    """The shipped path: arrow_udf -> forked Python worker."""
    from goldenmatch.spark.scoring import score_and_dedup

    out = score_and_dedup(
        df, block_col="blk", value_col="name", id_col=_ID,
        scorer_name=scorer_name, threshold=0.0,
    )
    return out.count()


def _batched_jvm(spark, df, udf_name, scorer_name: str):
    """J1's plan through the J2 jar: one call per batch, in the executor JVM,
    scoring with the Rust kernel over JNI.

    ``scorer_name`` is ``jaro_winkler`` by default now, NOT ``exact``. J0's jar
    carried no algorithms, so the only scorer both arms could run was string
    equality -- which measured the calling mechanism with the work held trivial
    and left "like-for-like needs J2" as this bench's load-bearing caveat. J2
    removed it, so the arms can finally do the same work.
    """
    from goldenmatch.spark.batched import dedup_max, score_pairs_batched
    from goldenmatch.spark.jvm import scorer_id

    scored = score_pairs_batched(
        _candidate_pairs(df), df, id_col=_ID, value_col="name",
        scorer_id=scorer_id(scorer_name), udf_name=udf_name,
    )
    return dedup_max(scored).count()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=200_000)
    ap.add_argument("--block-size", type=int, default=20)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--out", default="spark-scoring-bench.json")
    ap.add_argument(
        "--native",
        choices=["0", "1"],
        default="0",
        help="GOLDENMATCH_NATIVE for this arm. The Rust kernel runs ON THE "
             "EXECUTOR, so the shipped env must also carry the wheel -- setting "
             "this on the driver alone changes nothing.",
    )
    ap.add_argument(
        "--scorer",
        default="jaro_winkler",
        help="Scorer BOTH arms run. Defaults to jaro_winkler now that J2 exists: "
             "J0's jar carried no algorithms, so the only scorer both arms could "
             "run was `exact`, and this bench's load-bearing caveat was that "
             "like-for-like needed J2.",
    )
    ap.add_argument(
        "--path",
        choices=["row_python", "batched_jvm"],
        required=True,
        help="ONE arm per process -- see the module docstring. Running both in "
             "one session let the first arm's residue OOM the second.",
    )
    args = ap.parse_args(argv)

    # Set BEFORE goldenmatch.spark.scorers is imported anywhere: the loader
    # reads the flag when it resolves the component, and an import that already
    # happened would have resolved it under the old value.
    os.environ["GOLDENMATCH_NATIVE"] = args.native

    from goldenmatch.spark.jvm import (
        NATIVE_IMPL,
        JvmScorerUnavailable,
        find_jar,
        implementation,
        install,
    )
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
        "path": args.path,
        "native": args.native,
    }

    results["scorer"] = args.scorer

    if args.path == "row_python":
        print(f"[row_python] arrow_udf -> forked Python worker, "
              f"scorer={args.scorer}", flush=True)
        results["timing"] = _time(lambda: _row_python(df, args.scorer),
                                  repeats=args.repeats)
    else:
        try:
            udf_name = install(spark, jar=find_jar())
        except JvmScorerUnavailable as exc:
            print(f"::error::no JVM scorer jar: {exc}")
            return 2

        # REFUSE to bench the fallback. The jar drops to the `exact`-only J0
        # scorer when its native library will not load, which keeps a job alive
        # and would leave this arm reporting a number for the wrong thing --
        # against an arm doing real jaro-winkler work. That is worse than no
        # number, because it looks like one.
        impl, diagnostics, runtime = implementation(spark)
        results["jvm_impl"] = impl
        results["jvm_runtime"] = runtime
        print(f"  executor: {impl} | {runtime}\n  {diagnostics}", flush=True)
        if impl != NATIVE_IMPL:
            print(
                f"::error::the executor resolved {impl}, not {NATIVE_IMPL}. "
                f"This arm would be measuring the J0 fallback against a native "
                f"row_python arm. Diagnostics: {diagnostics}"
            )
            return 3

        print(f"[batched_jvm] array UDF, in the executor JVM, "
              f"scorer={args.scorer}", flush=True)
        results["timing"] = _time(
            lambda: _batched_jvm(spark, df, udf_name, args.scorer),
            repeats=args.repeats,
        )

    # Report what the kernel ACTUALLY resolved to, not what was asked for. A
    # missing wheel silently yields the pure floor, and a bench that reports the
    # requested flag rather than the resolved one would label pure-Python
    # numbers as native -- which is exactly how this harness misreported its
    # first result.
    try:
        from goldenmatch.core._native_loader import native_enabled

        results["native_resolved"] = bool(native_enabled("sail_scoring"))
    except Exception:  # noqa: BLE001
        results["native_resolved"] = False
    print(f"  native requested={args.native} resolved={results['native_resolved']}",
          flush=True)

    r = results["timing"]
    print(
        f"  {args.path:14} median {r['median_s']:8.3f}s  "
        f"(min {r['min_s']:.3f} max {r['max_s']:.3f})  rows_out={r['rows_out']:,}",
        flush=True,
    )

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, indent=2)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
