"""What of the Spark tier survives with NO Python on the executors?

## The question this answers

The J-arc's real justification is not throughput -- that was measured and the
JVM path lost (bench run 31656227516). It is deployment: a jar that
``addArtifact`` delivers, against a packed virtualenv that has to be built for
the executor platform and shipped to the cluster. "No Python on the executors"
is the claim.

That claim is currently unproven and, read against the source, partly false.
J2 put SCORING in the JVM, but the tier still defines Python UDFs for
normalization (``config_pipeline._transformed``), survivorship
(``golden.make_merge_udf``) and the identity graph (five in ``identity.py``).
Clustering needs none -- ``clustering.py`` is pure Spark SQL. So the honest
answer is somewhere between "scoring only" and "everything", and nobody has
measured which.

## Why `local[*]` cannot answer it by omission

The obvious experiment -- run the tier without shipping an executor env -- proves
nothing. Under ``local[*]`` the Python worker forks from the DRIVER's
interpreter, which has goldenmatch installed, so every UDF works and the run is
green while telling you nothing about a real cluster.

So this ships an env that is deliberately **EMPTY**: a bare virtualenv with no
goldenmatch, no pyarrow, nothing. Any operation that needs a Python worker now
fails with ``ModuleNotFoundError`` exactly as it would on a cluster with no
cluster-side install. Anything that survives is running in the JVM or in Spark
SQL, and is genuinely jar-only.

Failures here are the POINT. This is an inventory, not a gate: it turns "which
parts need Python on executors" from a reading of the source into a list.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback

_SCHEMA = "__row_id__ long, blk string, name string, city string"


def _rows():
    return [
        (0, "b0", "jonathan smith", "boston"),
        (1, "b0", "jonathon smyth", "Boston"),
        (2, "b0", "alice jones", "denver"),
        (3, "b1", "acme corporation", "reno"),
        (4, "b1", "acme corp", "Reno"),
        (5, "b1", "zeta industries", "miami"),
    ]


def _probe_pure_spark_sql(spark, df, ctx):
    """A block self-join: no UDF of any kind. The control -- if this fails, the
    session is broken and every other result here is meaningless."""
    from pyspark.sql import functions as F

    a, b = df.alias("a"), df.alias("b")
    pairs = a.join(
        b,
        (F.col("a.blk") == F.col("b.blk"))
        & (F.col("a.__row_id__") < F.col("b.__row_id__")),
    )
    return f"{pairs.count()} candidate pairs"


def _probe_jvm_scoring(spark, df, ctx):
    """J2: scoring through the jar. The thing this whole arc built."""
    from goldenmatch.spark.batched import dedup_max, score_pairs_batched
    from goldenmatch.spark.jvm import scorer_id
    from pyspark.sql import functions as F

    a, b = df.alias("a"), df.alias("b")
    pairs = a.join(
        b,
        (F.col("a.blk") == F.col("b.blk"))
        & (F.col("a.__row_id__") < F.col("b.__row_id__")),
    ).select(
        F.col("a.__row_id__").alias("a"), F.col("b.__row_id__").alias("b")
    )
    scored = score_pairs_batched(
        pairs, df, id_col="__row_id__", value_col="name",
        scorer_id=scorer_id("jaro_winkler"), udf_name=ctx["udf_name"],
    )
    rows = dedup_max(scored).collect()
    return f"{len(rows)} scored pairs, max={max(r['score'] for r in rows):.4f}"


def _probe_python_scoring(spark, df, ctx):
    """The shipped Python path, for contrast. Expected to FAIL here -- that is
    the whole point of the comparison."""
    from goldenmatch.spark.scoring import score_and_dedup

    out = score_and_dedup(
        df, block_col="blk", value_col="name", id_col="__row_id__",
        scorer_name="jaro_winkler", threshold=0.0,
    )
    return f"{out.count()} scored pairs"


def _probe_clustering(spark, df, ctx):
    """`clustering.py` defines no UDFs at all -- connected components is
    expressed in Spark SQL. If that reading is right this passes."""
    from goldenmatch.spark.clustering import connected_components

    edges = spark.createDataFrame([(0, 1), (1, 2), (3, 4)], "a long, b long")
    out = connected_components(edges, df, id_col="__row_id__")
    return f"{out.count()} labelled nodes"


def _probe_normalization(spark, df, ctx):
    """`config_pipeline._transformed`: an arrow_udf running goldenmatch's
    transform chain. Needs a Python worker."""
    from goldenmatch.spark.config_pipeline import _transformed

    out = df.select(_transformed(df["name"], ["lowercase", "strip_punctuation"]))
    return f"{out.count()} normalized values"


def _probe_survivorship(spark, df, ctx):
    """`golden.make_merge_udf`: survivorship merge, an arrow_udf."""
    from goldenmatch.spark.golden import make_merge_udf
    from pyspark.sql import functions as F

    merge = make_merge_udf("most_common")
    out = df.groupBy("blk").agg(F.collect_list("city").alias("city"))
    return f"{out.select(merge(F.col('city'))).count()} merged groups"


def _probe_identity_record_ids(spark, df, ctx):
    """`identity.derive_record_ids`: stable fingerprints, an arrow_udf."""
    from goldenmatch.spark.identity import derive_record_ids
    from pyspark.sql import functions as F

    # No source PK, so the h1 fingerprint path runs -- the arrow_udf one. With a
    # PK it is a pure column expression and would pass here, which would be a
    # true but misleading result.
    src = df.withColumn("__source__", F.lit("probe"))
    out = derive_record_ids(src, id_col="__row_id__")
    return f"{out.count()} record ids"


#: name -> (callable, what a failure MEANS). The second half matters: a failure
#: here is a fact about deployment, not a bug, and labelling it as such stops the
#: inventory being read as a broken build.
PROBES = [
    ("pure Spark SQL (block join)", _probe_pure_spark_sql,
     "the session itself is broken; ignore every result below"),
    ("clustering (connected components)", _probe_clustering,
     "clustering needs a Python worker after all"),
    ("JVM scoring (J2, via the jar)", _probe_jvm_scoring,
     "the jar path does not work without Python -- the arc's premise"),
    ("Python scoring (the shipped path)", _probe_python_scoring,
     "EXPECTED: this is the path J2 exists to replace"),
    ("normalization (transform chain)", _probe_normalization,
     "normalization needs Python on executors"),
    ("survivorship (golden merge)", _probe_survivorship,
     "survivorship needs Python on executors"),
    ("identity (record fingerprints)", _probe_identity_record_ids,
     "the identity graph needs Python on executors"),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="spark-jar-only-inventory.json")
    args = ap.parse_args(argv)

    from goldenmatch.spark.jvm import find_jar, implementation, install
    from pyspark.sql import SparkSession

    remote = os.environ.get("GOLDENMATCH_SPARK_REMOTE", "local[*]")
    spark = SparkSession.builder.remote(remote).getOrCreate()

    pyenv = os.environ.get("GOLDENMATCH_SPARK_PYENV")
    if not pyenv:
        print(
            "::error::no GOLDENMATCH_SPARK_PYENV. Under local[*] the Python "
            "worker forks from the DRIVER's interpreter, which HAS goldenmatch "
            "-- so every UDF would work and this inventory would report that "
            "nothing needs Python on executors, which is false. Ship the "
            "deliberately-empty env."
        )
        return 2
    from goldenmatch.spark.deps import ship_python_environment

    ship_python_environment(spark, pyenv)
    print(f"shipped executor env: {pyenv}", flush=True)

    udf_name = install(spark, jar=find_jar())
    impl, diagnostics, runtime = implementation(spark)
    print(f"executor JVM: {runtime}\nscorer: {impl}\n  {diagnostics}\n", flush=True)

    df = spark.createDataFrame(_rows(), _SCHEMA).cache()
    ctx = {"udf_name": udf_name}

    print("=" * 74, flush=True)
    print("  WHAT RUNS WITH NO PYTHON ON THE EXECUTORS", flush=True)
    print("=" * 74, flush=True)
    results = []
    for name, fn, meaning in PROBES:
        try:
            detail = fn(spark, df, ctx)
            print(f"  WORKS   {name:38} {detail}", flush=True)
            results.append({"probe": name, "works": True, "detail": detail})
        except Exception as exc:  # noqa: BLE001 - cataloguing failures IS the job
            first = str(exc).strip().splitlines()[0][:110] if str(exc).strip() else ""
            print(f"  NEEDS   {name:38} {type(exc).__name__}: {first}", flush=True)
            print(f"          -> {meaning}", flush=True)
            results.append({
                "probe": name, "works": False,
                "error": f"{type(exc).__name__}: {first}", "meaning": meaning,
            })

    works = [r["probe"] for r in results if r["works"]]
    needs = [r["probe"] for r in results if not r["works"]]
    print("=" * 74)
    print(f"  jar-only today: {len(works)}/{len(results)}")
    print(f"  still needs a Python worker: {', '.join(needs) if needs else 'nothing'}")

    payload = {
        "jvm_impl": impl, "jvm_runtime": runtime,
        "works": works, "needs_python": needs, "results": results,
    }
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"\nwrote {args.out}")
    # Deliberately exit 0 even with failures: this is an INVENTORY. A red build
    # would say "something broke", when what it found is a fact about the tier.
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        sys.exit(2)
