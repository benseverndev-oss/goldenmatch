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

## Representative calls vs entry points

The first pass of this measured INTERNALS -- ``_transformed``, ``merge_expr``,
``derive_record_ids``. That answers "is the kernel reachable", which is not the
question a user has. A kernel routed at the internals but not threaded through
the entry point is, from outside, not routed at all: there is no argument to
pass. Both were true here until ``run_config_pipeline`` and its stages grew
``transform_udf`` / ``survivorship_udf``.

So the probes come in two halves. The originals stay because they localize a
failure to one kernel; the ``ENTRY`` probes call what a user calls, and one of
them runs a whole dedupe -- block, score, cluster, golden.

Two probes must never pass, and :data:`MUST_NOT_PASS` names them so the score
has a stated ceiling rather than an implied denominator.
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


def _consume(df, col: str) -> list:
    """Collect ``col`` and return its values, forcing whatever produced it to run.

    **A probe must never end in ``.count()``.** Counting rows does not need any
    particular column, so Catalyst prunes the ones nobody reads -- and a
    deterministic side-effect-free UDF is exactly what it is entitled to prune.
    A probe that counts therefore reports WORKS for a Python UDF that never
    executed, which is the opposite of what this inventory exists to find.

    That is not hypothetical. The first run of this script reported 6 of 7
    probes working with an empty executor env, including normalization,
    survivorship and identity -- all three of which define ``arrow_udf``s and
    cannot possibly work without a Python worker. They were pruned. (The same
    mistake had already been found and fixed one file over, in the batched-plan
    bisect, hours earlier.)

    So every probe routes through here: collect the values and hand them back,
    so the column is load-bearing and the UDF has to run.
    """
    return [r[0] for r in df.select(col).collect()]


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
    scores = _consume(out, "score")
    return f"{len(scores)} scored pairs, max={max(scores):.4f}"


def _probe_clustering(spark, df, ctx):
    """`clustering.py` defines no UDFs at all -- connected components is
    expressed in Spark SQL. If that reading is right this passes."""
    from goldenmatch.spark.clustering import connected_components

    edges = spark.createDataFrame([(0, 1), (1, 2), (3, 4)], "a long, b long")
    out = connected_components(edges, df, id_col="__row_id__")
    labels = _consume(out, out.columns[-1])
    return f"{len(labels)} labelled nodes, {len(set(labels))} components"


def _probe_normalization(spark, df, ctx):
    """`config_pipeline._transformed` through the JAR's transform kernel.

    Was an arrow_udf and therefore Python-only; now routed to
    `golden_transform`, which runs the same pyo3-free `transforms-core` the
    Python path uses."""
    from goldenmatch.spark.config_pipeline import _transformed

    out = df.select(
        _transformed(
            df["name"],
            ["lowercase", "normalize_whitespace"],
            transform_udf=ctx["transform_udf"],
        ).alias("v")
    )
    vals = _consume(out, "v")
    return f"{len(vals)} normalized values, e.g. {vals[0]!r}"


def _probe_survivorship(spark, df, ctx):
    """`golden` survivorship through the JAR's kernel.

    Was an arrow_udf and therefore Python-only; now routed to
    `golden_survivorship`, which runs the same pyo3-free `survivorship-core`
    the Python path uses."""
    from goldenmatch.spark.golden import merge_expr
    from pyspark.sql import functions as F

    grouped = df.groupBy("blk").agg(F.collect_list("city").alias("city"))
    vals = _consume(
        grouped.select(
            merge_expr(F.col("city"), "majority_vote", ctx["survivorship_udf"]).alias("v")
        ),
        "v",
    )
    return f"{len(vals)} merged groups, e.g. {vals[0]!r}"


def _probe_identity_record_ids(spark, df, ctx):
    """`identity.derive_record_ids` through the JAR's fingerprint kernel.

    Was an arrow_udf and therefore Python-only; now routed to
    `golden_fingerprint`, which hashes `to_json(struct(...))` with the same
    pyo3-free `fingerprint-core` the Python path uses."""
    from goldenmatch.spark.identity import derive_record_ids
    from pyspark.sql import functions as F

    # No source PK, so the h1 fingerprint path runs -- the arrow_udf one. With a
    # PK it is a pure column expression and would pass here, which would be a
    # true but misleading result.
    src = df.withColumn("__source__", F.lit("probe"))
    out = derive_record_ids(
        src, id_col="__row_id__", fingerprint_udf=ctx["fingerprint_udf"]
    )
    ids = _consume(out, "record_id")
    return f"{len(ids)} record ids, e.g. {ids[0]!r}"


def _probe_entry_blocking(spark, df, ctx):
    """`config_pipeline.generate_candidates` -- the blocking ENTRY POINT, not
    the `_transformed` internal the normalization probe calls.

    Blocking is where a normalization divergence does its damage: a value
    normalized differently lands in a DIFFERENT BLOCK and is never compared to
    its own duplicate. The failure is a missing match, and nothing downstream
    can detect it."""
    from goldenmatch.spark.config_pipeline import generate_candidates

    cfg = _probe_config()
    pairs = generate_candidates(
        df, cfg, id_col="__row_id__", transform_udf=ctx["transform_udf"]
    )
    ids = _consume(pairs, "a")
    return f"{len(ids)} candidate pairs from {len(cfg.get_matchkeys())} matchkey(s)"


def _probe_entry_golden(spark, df, ctx):
    """`config_pipeline.build_golden_from_rules` -- the golden-record ENTRY
    POINT, rather than `merge_expr` directly."""
    from goldenmatch.spark.config_pipeline import build_golden_from_rules

    cfg = _probe_config()
    assignments = spark.createDataFrame(
        [(0, 0), (0, 1), (1, 3), (1, 4)], "cluster_id long, member_id long"
    )
    golden = build_golden_from_rules(
        assignments, df, cfg,
        golden_cols=["name", "city"], id_col="__row_id__",
        survivorship_udf=ctx["survivorship_udf"],
    )
    rows = golden.select("cluster_id", "name", "city").collect()
    return f"{len(rows)} golden records, e.g. {rows[0]['name']!r}"


def _probe_entry_dedupe(spark, df, ctx):
    """The whole thing: `run_config_pipeline` -- block, score, cluster, golden.

    THE question this inventory exists to answer. Every probe above is one
    stage; a user runs this. Passing the two kernel names it accepts is the
    most jar-only a real dedupe can currently be.

    Expected to FAIL, and the failure is the finding: `score_candidates` builds
    one row-shaped Python UDF per field of each matchkey, while the jar's
    scorer is a batched array-shaped UDF over a single value column. Different
    call structures, so there is no `scorer_udf` to pass -- routing that stage
    is a design change, not a parameter. Until it lands, an end-to-end dedupe
    needs a Python environment on the executors for scoring alone, however
    thoroughly the other stages are routed."""
    from goldenmatch.spark.config_pipeline import run_config_pipeline

    golden = run_config_pipeline(
        df, _probe_config(), id_col="__row_id__", wcc="label_prop",
        golden_cols=["name", "city"],
        transform_udf=ctx["transform_udf"],
        survivorship_udf=ctx["survivorship_udf"],
    )
    rows = golden.collect()
    return f"{len(rows)} golden records end to end"


def _probe_config():
    """A minimal config the probe rows actually match under.

    Built in code rather than loaded from a fixture so the probes stay readable
    next to what they assert, and so a config-schema change breaks this loudly
    here rather than silently changing what is being measured."""
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        GoldenRulesConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    return GoldenMatchConfig(
        # `lowercase` on the block key is the point: it is the only transform
        # chain in this config, so the blocking probe genuinely exercises
        # `golden_transform` rather than passing because there was nothing to
        # normalize.
        blocking=BlockingConfig(
            keys=[BlockingKeyConfig(fields=["blk"], transforms=["lowercase"])]
        ),
        matchkeys=[
            MatchkeyConfig(
                name="mk_name",
                type="weighted",
                fields=[MatchkeyField(field="name", scorer="jaro_winkler", weight=1.0)],
                threshold=0.85,
            )
        ],
        golden_rules=GoldenRulesConfig(default_strategy="most_complete"),
    )


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
    # Everything above calls an INTERNAL -- `_transformed`, `merge_expr`,
    # `derive_record_ids`. That measures whether a kernel is reachable, which is
    # not the same as whether a user can reach it: a kernel routed at the
    # internals but not threaded through the entry point is, from outside, not
    # routed at all. Both were true here until the entry points grew
    # `transform_udf` / `survivorship_udf` arguments.
    ("ENTRY blocking (generate_candidates)", _probe_entry_blocking,
     "blocking needs Python on executors -- a differently-normalized value lands "
     "in a different block and is never compared"),
    ("ENTRY golden (build_golden_from_rules)", _probe_entry_golden,
     "golden records need Python on executors"),
    ("ENTRY dedupe (run_config_pipeline)", _probe_entry_dedupe,
     "EXPECTED: scoring is per-field and row-shaped here while the jar's scorer "
     "is batched and array-shaped, so there is no scorer_udf to pass. An "
     "end-to-end dedupe still needs Python on the executors for scoring alone"),
]

#: Probes that must NEVER pass, so the score has an honest ceiling.
#:
#: One is a negative control -- the Python scoring path is precisely what J2
#: replaces, and it working with an empty executor env would mean the env was
#: not actually empty and every other result here is worthless. The other is a
#: real, named gap: the end-to-end dedupe cannot be jar-only while its scoring
#: stage has no jar-shaped call. It is listed rather than omitted because a
#: missing probe reads as "not thought about" while a failing one reads as
#: "measured, and here is what it costs".
MUST_NOT_PASS = frozenset({
    "Python scoring (the shipped path)",
    "ENTRY dedupe (run_config_pipeline)",
})

# A typo here would not raise -- it would silently raise the ceiling by one and
# reclassify a real gap as a genuine pass. Bind it to the actual names.
_unknown = MUST_NOT_PASS - {name for name, _, _ in PROBES}
if _unknown:
    raise AssertionError(
        f"MUST_NOT_PASS names no such probe: {sorted(_unknown)}. Renaming a "
        f"probe without updating this set silently changes the ceiling."
    )


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
    from goldenmatch.spark.jvm import (
        FINGERPRINT_UDF_NAME,
        SURVIVORSHIP_UDF_NAME,
        TRANSFORM_UDF_NAME,
    )

    ctx = {
        "udf_name": udf_name,
        "fingerprint_udf": FINGERPRINT_UDF_NAME,
        "transform_udf": TRANSFORM_UDF_NAME,
        "survivorship_udf": SURVIVORSHIP_UDF_NAME,
    }

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
    unexpected = [p for p in needs if p not in MUST_NOT_PASS]
    print("=" * 74)
    # The ceiling, stated. Two probes must NEVER pass: the Python-scoring
    # negative control, and the end-to-end dedupe whose scoring stage has no
    # jar-only shape yet. Printing a bare "8/10" invites reading it as 80% of
    # the way there, when 8 IS the top of the scale.
    print(f"  jar-only today: {len(works)}/{len(PROBES) - len(MUST_NOT_PASS)}"
          f"  (of {len(PROBES)} probes; {len(MUST_NOT_PASS)} must never pass)")
    if unexpected:
        print(f"  still needs a Python worker: {', '.join(unexpected)}")
    else:
        print("  still needs a Python worker: nothing that could be routed today")
    for p in needs:
        if p in MUST_NOT_PASS:
            print(f"  (expected failure, not a gap in the jar: {p})")

    payload = {
        "jvm_impl": impl, "jvm_runtime": runtime,
        "ceiling": len(PROBES) - len(MUST_NOT_PASS),
        "works": works, "needs_python": needs,
        "expected_failures": sorted(MUST_NOT_PASS),
        "unexpected_failures": unexpected,
        "results": results,
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
