#!/usr/bin/env python3
"""Distributed FS training at scale, against a REAL Spark cluster.

`train_em_distributed` is exercised two ways today and neither answers the
question this script exists for:

* `spark_connect` runs it under ``local[*]``, where every "executor" is the
  driver's own machine;
* `spark-cluster` runs it on two real workers -- but on an EIGHT ROW fixture,
  which proves correctness and the jar-only property and nothing about scale.

So the central claim of the distributed tier -- that FS training works at a size
one box cannot hold -- has never been tested. This script is that test.

## The fixture is generated IN SPARK, deliberately

Every column comes from ``spark.range(n)`` and SQL expressions, so no row ever
crosses the client. Building it with ``createDataFrame`` over a Python list
would put the whole fixture through the driver first, and the driver is exactly
where this tier's scale wall has historically been (`project_scale_driver_
bottleneck`) -- the harness would hit the driver's ceiling and report it as a
training limit.

## What it measures

Wall per STAGE, not just a total, because the three stages fail differently:

* ``u`` -- a sampled self-join. Cost is the sample, not the table.
* ``counts`` -- one ``GROUP BY`` per blocking pass over every candidate pair.
  This is the stage that scales with the data, and the one a cluster is for.
* ``train`` -- the driver-side EM over collapsed vectors. Bounded by
  ``prod(levels + 1)``, so it should be FLAT in the row count. If it is not,
  the bound is not holding and that is the finding.

Also reported: distinct patterns per pass and pairs per pass. A pattern count
that grows with rows would mean the collapse is not collapsing.

Usage (from the repo root, against a running Connect endpoint):

    python scripts/spark_fs_train_scale.py --rows 1000000 --out scale.json
"""
from __future__ import annotations

import argparse
import json
import os
import time


def build_fixture(spark, rows: int, dup: int, blocks_per_key: int):
    """A person-shaped frame with a known duplicate structure, built in-engine.

    ``dup`` rows per entity, so the true-match count is known:
    ``n_entities * C(dup, 2)``. Blocking keys are derived from the entity id so
    duplicates land in the same block -- a fixture whose duplicates never block
    together would measure an empty candidate set very quickly and prove
    nothing.
    """
    from pyspark.sql import functions as F

    n_entities = max(rows // max(dup, 1), 1)
    ent = F.col("id") % F.lit(n_entities)
    # A cheap deterministic perturbation: one row in `dup` gets a variant
    # spelling, so comparison vectors are not all "agree on everything".
    variant = (F.col("id") / F.lit(n_entities)).cast("int") % F.lit(max(dup, 1))

    return spark.range(rows).select(
        F.col("id").alias("__row_id__"),
        F.when(variant == 0, F.concat(F.lit("ann"), ent.cast("string")))
         .otherwise(F.concat(F.lit("anna"), ent.cast("string"))).alias("first"),
        F.concat(F.lit("lee"), (ent % F.lit(max(n_entities // 3, 1))).cast("string"))
         .alias("last"),
        # The blocking key: `blocks_per_key` entities share one value, so block
        # size is about `dup * blocks_per_key` and the candidate count stays
        # linear in rows rather than quadratic.
        F.concat(F.lit("k"), (ent / F.lit(max(blocks_per_key, 1))).cast("int").cast("string"))
         .alias("blk"),
        F.concat(F.lit("z"), (ent % F.lit(500)).cast("string")).alias("zip"),
    )


def make_config():
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    return GoldenMatchConfig(
        blocking=BlockingConfig(
            strategy="multi_pass",
            passes=[
                BlockingKeyConfig(fields=["blk"]),
                # A second pass keyed on a COMPARISON field, so the per-pass
                # conditioning that makes sessions necessary is exercised. A
                # single-pass run would skip the combination entirely.
                BlockingKeyConfig(fields=["last"]),
            ],
        ),
        matchkeys=[
            MatchkeyConfig(
                name="fs", type="probabilistic",
                fields=[
                    MatchkeyField(field="first", scorer="jaro_winkler",
                                  levels=2, partial_threshold=0.8),
                    MatchkeyField(field="last", scorer="jaro_winkler",
                                  levels=2, partial_threshold=0.8),
                ],
            ),
        ],
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--dup", type=int, default=3, help="rows per entity")
    ap.add_argument("--blocks-per-key", type=int, default=4,
                    help="entities sharing one blocking value")
    ap.add_argument("--remote", default=os.environ.get(
        "GOLDENMATCH_SPARK_REMOTE", "sc://localhost:15002"))
    ap.add_argument("--u-max-pairs", type=int, default=1_000_000)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    from goldenmatch.spark.config_pipeline import (
        CAND_LHS,
        CAND_RHS,
        blocking_passes,
        join_candidates_to_sources,
        pass_candidates,
    )
    from goldenmatch.spark.em import (
        agreement_pattern_counts,
        estimate_u_distributed,
    )
    from goldenmatch.spark.jvm import (
        ROW_UDF_NAME,
        TRANSFORM_UDF_NAME,
        find_jar,
        implementation,
        install,
    )
    from pyspark.sql import SparkSession

    spark = SparkSession.builder.remote(args.remote).getOrCreate()
    install(spark, jar=find_jar())
    impl, _diagnostics, runtime = implementation(spark)
    print(f"[scale] kernel impl={impl} runtime={runtime}", flush=True)

    out: dict = {
        "rows": args.rows, "dup": args.dup,
        "blocks_per_key": args.blocks_per_key,
        "kernel_impl": impl, "kernel_runtime": runtime,
        "stages": {}, "passes": [],
    }

    t0 = time.perf_counter()
    df = build_fixture(spark, args.rows, args.dup, args.blocks_per_key)
    # Materialise once so the fixture build is not re-run inside every stage
    # and charged to whichever stage happened to trigger it.
    df.cache()
    actual = df.count()
    out["stages"]["fixture_seconds"] = round(time.perf_counter() - t0, 2)
    out["actual_rows"] = actual
    print(f"[scale] fixture: {actual:,} rows in "
          f"{out['stages']['fixture_seconds']}s", flush=True)

    cfg = make_config()
    mk = cfg.get_matchkeys()[0]

    # ── u: sampled self-join. Cost tracks the SAMPLE, not the table. ──
    t = time.perf_counter()
    u_probs = estimate_u_distributed(
        df, mk, id_col="__row_id__",
        scorer_udf=ROW_UDF_NAME, transform_udf=TRANSFORM_UDF_NAME,
        max_pairs=args.u_max_pairs,
    )
    out["stages"]["u_seconds"] = round(time.perf_counter() - t, 2)
    out["u_probs"] = {k: [round(x, 6) for x in v] for k, v in u_probs.items()}
    print(f"[scale] u in {out['stages']['u_seconds']}s -> {out['u_probs']}",
          flush=True)

    # ── counts: the stage that scales with the data. ──
    from goldenmatch.core.probabilistic import (
        _combine_em_sessions,
        train_em_from_counts,
    )

    sessions = []
    count_wall = 0.0
    train_wall = 0.0
    for i, key_config in enumerate(blocking_passes(cfg)):
        fields = tuple(key_config.fields)
        cands = pass_candidates(df, key_config, id_col="__row_id__")
        joined = join_candidates_to_sources(cands, df, id_col="__row_id__")

        t = time.perf_counter()
        counts = agreement_pattern_counts(
            joined, mk, lhs=CAND_LHS, rhs=CAND_RHS,
            scorer_udf=ROW_UDF_NAME, transform_udf=TRANSFORM_UDF_NAME,
        )
        dt = time.perf_counter() - t
        count_wall += dt
        n_pairs = sum(c for _, c in counts)

        t = time.perf_counter()
        em = train_em_from_counts(mk, counts, u_probs, conditioned_fields=fields)
        train_wall += time.perf_counter() - t
        sessions.append((fields, em, float(n_pairs)))

        out["passes"].append({
            "pass": i, "blocking_fields": list(fields),
            "pairs": n_pairs, "distinct_patterns": len(counts),
            "count_seconds": round(dt, 2),
        })
        print(f"[scale] pass {i} on {list(fields)}: {n_pairs:,} pairs -> "
              f"{len(counts)} distinct patterns in {dt:.2f}s", flush=True)

    out["stages"]["counts_seconds"] = round(count_wall, 2)
    out["stages"]["train_seconds"] = round(train_wall, 2)

    model = _combine_em_sessions(mk, sessions)
    out["m_probs"] = {k: [round(x, 6) for x in v] for k, v in model.m_probs.items()}
    out["match_weights"] = {
        k: [round(x, 4) for x in v] for k, v in model.match_weights.items()
    }
    out["proportion_matched"] = round(model.proportion_matched, 6)
    out["total_seconds"] = round(sum(out["stages"].values()), 2)

    print(f"[scale] DONE rows={actual:,} total={out['total_seconds']}s "
          f"stages={out['stages']}", flush=True)
    print(f"[scale] model m={out['m_probs']} weights={out['match_weights']}",
          flush=True)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
        print(f"[scale] wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
