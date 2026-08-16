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


def build_fixture(spark, rows: int, dup: int, blocks_per_key: int, value_pad: int = 0):
    """A person-shaped frame with a known duplicate structure, built in-engine.

    ``dup`` rows per entity, so the true-match count is known. Blocking keys are
    derived from the entity id so duplicates land in the same block -- a fixture
    whose duplicates never block together would measure an empty candidate set
    very quickly and prove nothing.

    ## Entropy is the point, and the first version of this had none

    The original generator perturbed ONE field with ONE of two spellings, so
    9.5M candidate pairs collapsed to **3 and 2 distinct comparison vectors**.
    That measured the collapse mechanism working on data with nothing to
    collapse -- it proved `prod(levels + 1)` bounds the driver-side EM without
    ever approaching the bound.

    Every field is now perturbed INDEPENDENTLY, by a per-(row, field) hash:

      * a character dropped (a truncation typo),
      * a character appended (a fat-finger typo),
      * nulled entirely (missing data -> the `-1` unobserved level),
      * left alone.

    Independence is what produces variety: with five fields each landing on
    agree / partial / unobserved, the reachable vector space is in the hundreds,
    and which of them actually occur is a property of the data rather than of
    the generator's single knob.

    Deterministic despite being pseudo-random: `xxhash64(id, field_seed)` is a
    pure function of the row, so two runs at the same scale produce byte-
    identical fixtures and a regression is a real change rather than a reroll.
    """
    from pyspark.sql import functions as F

    n_entities = max(rows // max(dup, 1), 1)
    ent = F.col("id") % F.lit(n_entities)
    ent_s = ent.cast("string")

    def perturbed(base, seed: int):
        h = F.pmod(F.xxhash64(F.col("id"), F.lit(seed)), F.lit(10))
        # The truncation drops the LAST character, not everything after the
        # third. `substring(base, 1, 3)` collapsed every truncated value to the
        # field's shared literal prefix -- every truncated `last` became "lee",
        # so blocking pass 1 (keyed on `last`) put 20% of the table in ONE
        # block: 200k rows at 1M => ~20 BILLION pairs, and the run wedged for
        # 24 minutes before the step timeout killed it. Dropping the trailing
        # character is the typo the docstring above describes and preserves the
        # field's cardinality.
        return (
            F.when(h < F.lit(6), base)                                    # 60% clean
             .when(h < F.lit(8), F.regexp_replace(base, ".$", ""))        # 20% truncated
             .when(h < F.lit(9), F.concat(base, F.lit("x")))              # 10% typo
             .otherwise(F.lit(None).cast("string"))                       # 10% missing
        )

    first = F.concat(F.lit("ann"), ent_s)
    last = F.concat(F.lit("lee"), (ent % F.lit(max(n_entities // 3, 1))).cast("string"))
    dob = F.concat(F.lit("19"), F.lpad((ent % F.lit(80)).cast("string"), 2, "0"),
                   F.lit("-01-01"))
    zipc = F.concat(F.lit("z"), F.lpad((ent % F.lit(500)).cast("string"), 3, "0"))
    city = F.concat(F.lit("city"), (ent % F.lit(40)).cast("string"))

    # `value_pad` lengthens every COMPARISON value by a constant suffix without
    # changing cardinality, which fields exist, or which pairs block together.
    # It is the knob that separates per-CALL from per-BYTE scoring cost:
    # crossing COUNT is identical across pad settings, only the bytes marshalled
    # per crossing move. (Varying the FIELD COUNT cannot do this -- adding a
    # field adds a call AND its bytes, so both hypotheses predict the same
    # scaling. That was a design error in the first version of this experiment.)
    #
    # The pad is a constant string, so it cannot change a similarity ORDERING:
    # jaro-winkler and levenshtein both see the same suffix on each side of
    # every pair, so gammas -- and therefore the pattern counts and the trained
    # model -- are unchanged. Only the marshalled length moves.
    pad = F.lit("z" * value_pad) if value_pad > 0 else None

    def padded(col):
        if pad is None:
            return col
        # Concat only when the value is present; a padded NULL would become a
        # non-null and silently change the unobserved level.
        return F.when(col.isNull(), col).otherwise(F.concat(col, pad))

    return spark.range(rows).select(
        F.col("id").alias("__row_id__"),
        padded(perturbed(first, 101)).alias("first"),
        padded(perturbed(last, 202)).alias("last"),
        padded(perturbed(dob, 303)).alias("dob"),
        padded(perturbed(zipc, 404)).alias("zip"),
        padded(perturbed(city, 505)).alias("city"),
        # The blocking key is NEVER perturbed: it decides which pairs are
        # compared at all, so corrupting it would silently shrink the candidate
        # set and make a smaller run look like a faster one.
        F.concat(F.lit("k"),
                 (ent / F.lit(max(blocks_per_key, 1))).cast("int").cast("string"))
         .alias("blk"),
    )


def largest_block(source_df, key_config, transform_udf=None):
    """``(key, rows)`` for the biggest block one pass would build.

    Blocking skew is the failure mode this harness is least able to report on
    its own: a skewed key does not raise, it just makes the self-join quadratic
    in the size of one block and the run sits there producing nothing. The
    fixture's own truncation bug did exactly that (see `perturbed`), and the
    only evidence was a 24-minute gap between two log lines.

    Deliberately reuses the product's `_block_key_column` / `_valid_key` rather
    than grouping by the raw fields. Those privates are what `pass_candidates`
    joins on, so a guard built on anything else could measure a key the join
    never uses and clear a pass that then hangs anyway.
    """
    from goldenmatch.spark.config_pipeline import _block_key_column, _valid_key
    from pyspark.sql import functions as F

    key_col, _fields = _block_key_column(key_config, transform_udf)
    top = (
        source_df.withColumn("__block_key__", key_col)
        .where(_valid_key(F.col("__block_key__")))
        .groupBy("__block_key__")
        .agg(F.count(F.lit(1)).alias("n"))
        .orderBy(F.col("n").desc())
        .limit(1)
        .collect()
    )
    if not top:
        return None, 0
    return top[0]["__block_key__"], int(top[0]["n"])


def profile_counts(joined, mk, *, scorer_udf, transform_udf, cands):
    """Attribute the counts stage: pair generation vs join vs scoring vs shuffle.

    ## Why this exists

    At 5M rows the counts stage is 443.32s of a 471.53s wall -- 94.0%. Every
    design option for making it faster (pre-aggregating patterns in the JVM,
    pushing gammas into Catalyst, leaving Spark Connect) is a bet on WHICH part
    of those 443s is the cost, and nothing measured that. The kernel is known to
    be ~0.1s and the row-shaped UDF already beat the batched arm, so the money
    is somewhere else -- most likely the `GROUP BY` shuffle over every candidate
    pair. "Most likely" is exactly what this replaces.

    ## How

    Spark has no per-operator timer reachable over Connect, so this times
    PREFIXES of the same DAG, each forced by its own action, and reports the
    deltas. Each prefix is a full independent evaluation (nothing is cached):
    four prefixes plus the real counts run that follows, so the pass costs
    roughly FIVE times a normal one, not the "3x" a first draft of this comment
    claimed. At 5M rows that is ~35 minutes against a 25-minute step timeout,
    which is why the workflow's timeout moved with this flag.

    Profile at 1M, not 5M. The output is an ATTRIBUTION -- which fraction of the
    stage each part owns -- and that ratio is what picks the design. Paying 5x
    at 5M to learn the same ratio is waste.

    ## The trap this avoids

    A naive ``.agg(count(1))`` to "force the UDF" does NOT force it: Catalyst
    prunes columns an aggregate does not read, so the gamma expressions -- the
    scoring being measured -- get optimised away and the stage times as if
    scoring were free. So the forcing aggregate SUMS EVERY GAMMA COLUMN, which
    Catalyst cannot satisfy without evaluating all of them.
    """
    import time

    # CAND_LHS / CAND_RHS are imported HERE, not read from module scope: the
    # harness imports them lazily inside `main()`, so referencing them from this
    # function was a NameError the first time --profile-counts was used. ruff
    # (F821) caught it before the cluster did.
    from goldenmatch.spark.config_pipeline import CAND_LHS, CAND_RHS
    from goldenmatch.spark.em import gamma_columns
    from pyspark.sql import functions as F

    gammas = gamma_columns(
        mk, CAND_LHS, CAND_RHS, scorer_udf=scorer_udf, transform_udf=transform_udf
    )
    names = [f"gamma_{f.resolved_field}" for f in mk.fields]
    out = {}

    # 1. Candidate generation alone: the blocking self-join, no record columns.
    t = time.perf_counter()
    n_pairs = cands.count()
    out["candidates_seconds"] = round(time.perf_counter() - t, 2)
    out["candidate_pairs"] = int(n_pairs)

    # 2. + joining both record sides. Counting the joined frame forces the join
    #    without evaluating any gamma.
    t = time.perf_counter()
    joined.count()
    out["joined_seconds"] = round(time.perf_counter() - t, 2)

    # 3. + scoring every gamma. Global agg => a 1-row shuffle, so this adds the
    #    UDF work and almost no exchange. Summing every gamma is what stops
    #    Catalyst pruning the scoring away (see the docstring).
    t = time.perf_counter()
    joined.select(*gammas).agg(*[F.sum(F.col(n)) for n in names]).collect()
    out["scored_seconds"] = round(time.perf_counter() - t, 2)

    # 4. + the wide GROUP BY over every pair: the exchange this whole question
    #    is about.
    t = time.perf_counter()
    (joined.select(*gammas)
        .groupBy(*[F.col(n) for n in names])
        .agg(F.count(F.lit(1)))
        .collect())
    out["grouped_seconds"] = round(time.perf_counter() - t, 2)

    # Deltas. Each prefix contains the ones before it, so the attribution is the
    # difference. Reported alongside the raw prefixes so a negative delta (noise,
    # or a prefix Spark optimised differently) is visible rather than hidden.
    out["attribution"] = {
        "pair_generation": out["candidates_seconds"],
        "record_join": round(out["joined_seconds"] - out["candidates_seconds"], 2),
        "scoring_udf": round(out["scored_seconds"] - out["joined_seconds"], 2),
        "groupby_exchange": round(out["grouped_seconds"] - out["scored_seconds"], 2),
    }
    return out


def make_config(n_fields: int = 5, scorer: str | None = None):
    """The scale config. ``n_fields`` trims the COMPARISON fields only.

    ## Why this is a knob

    The counts stage is ~87% scoring, and scoring is the ONE thing that crosses
    into the kernel -- `_field_similarity_and_observed` is explicit that levels,
    the weight lookup, the sum and the posterior are all already Spark SQL. So
    the question that picks the next optimisation is whether that 87% scales
    with the NUMBER of calls or with the BYTES marshalled:

      per-CALL  -> one call per pair instead of one per field is a ~5x cut, and
                   no columnar/FFI rewrite is needed to get most of it.
      per-BYTE  -> fewer, fatter calls move nothing, and the Arrow C Data
                   Interface (or leaving Spark Connect) is the only real lever.

    That matters because the jar's own `GoldenScoreRowUdf` documents why the
    previous batching attempt LOST: Spark arrays are `ArrayData` of
    `InternalRow`, so array-shaped UDF I/O pays row-wise object churn. A fatter
    UDF would reintroduce some of that, and is only worth building if the cost
    is per-call.

    Varying the field count over the SAME pairs separates the two, and costs a
    config change instead of a JNI rewrite. Blocking is untouched, so the
    candidate set is identical across arms and only the per-pair crossing count
    moves.

    Trimming from the END keeps `first` and `last` -- the high-cardinality
    jaro-winkler fields -- in every arm, so a smaller arm is never accidentally
    cheaper because it dropped the expensive scorers.
    """
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    cfg = _build_config(GoldenMatchConfig, BlockingConfig, BlockingKeyConfig,
                        MatchkeyConfig, MatchkeyField)
    mk = cfg.get_matchkeys()[0]
    if scorer:
        # Force every comparison field onto one scorer. This exists for the
        # `--value-pad` experiment: padding is only workload-neutral under
        # `exact`, where `a+pad == b+pad` iff `a == b`, so the gammas -- and
        # therefore the pattern counts and the trained model -- are untouched
        # while the marshalled bytes move.
        #
        # It is NOT neutral under jaro_winkler or levenshtein, which was a real
        # error: a shared 40-char suffix dominates the similarity, every pair
        # clears the 0.9 threshold, and the vector space collapsed 433 -> 65
        # patterns with m_probs for `last` going to [0, 0, 1]. Those arms were
        # measuring different workloads, so their timings meant nothing.
        for f in mk.fields:
            f.scorer = scorer
    if n_fields < len(mk.fields):
        # Trim in place. Rebuilding a shorter literal risks the arms differing
        # in something other than field count, which is the one variable this
        # experiment must isolate.
        mk.fields = mk.fields[:n_fields]
    return cfg


def _build_config(GoldenMatchConfig, BlockingConfig, BlockingKeyConfig,
                  MatchkeyConfig, MatchkeyField):
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
            # FIVE fields at three levels each. The comparison-vector space is
            # `prod(levels + 1)` = 4^5 = 1,024, so the driver-side EM has a
            # bound worth testing. The previous two-field / two-level config
            # capped it at 9 -- the run reported 3 and 2 distinct patterns and
            # could not have reported more than 9 whatever the data looked like.
            MatchkeyConfig(
                name="fs", type="probabilistic",
                fields=[
                    MatchkeyField(field="first", scorer="jaro_winkler",
                                  levels=3, partial_threshold=0.7),
                    MatchkeyField(field="last", scorer="jaro_winkler",
                                  levels=3, partial_threshold=0.7),
                    MatchkeyField(field="dob", scorer="levenshtein",
                                  levels=3, partial_threshold=0.7),
                    MatchkeyField(field="zip", scorer="jaro_winkler",
                                  levels=3, partial_threshold=0.7),
                    MatchkeyField(field="city", scorer="jaro_winkler",
                                  levels=3, partial_threshold=0.7),
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
    ap.add_argument(
        "--scorer", default="",
        help="Force every comparison field onto one scorer. Use `exact` with "
             "--value-pad: padding preserves equality, so gammas are unchanged "
             "and only the marshalled bytes move. Under jaro_winkler/levenshtein "
             "padding CHANGES similarity and the arms stop being comparable.")
    ap.add_argument(
        "--value-pad", type=int, default=0,
        help="Append N filler chars to every COMPARISON value. Crossing COUNT "
             "is unchanged; only the bytes marshalled per crossing move. This "
             "is the arm that separates per-CALL from per-BYTE scoring cost -- "
             "`--fields` CANNOT, because adding a field adds a call AND its "
             "bytes, so both hypotheses predict the same scaling.")
    ap.add_argument(
        "--fields", type=int, default=5,
        help="Number of COMPARISON fields (max 5). Same blocking, same "
             "candidate pairs -- only the per-pair crossing count changes. "
             "Run 5 vs 2 and compare `scoring_udf`: linear in field count "
             "means the cost is per-CALL (fewer, fatter calls win); flat means "
             "per-BYTE (only columnar/FFI moves it).")
    ap.add_argument(
        "--profile-counts", action="store_true",
        help="Attribute the counts stage across pair generation / record "
             "join / scoring UDF / GROUP BY exchange. Times PREFIXES of the "
             "same DAG, so it costs ~3x a normal counts stage -- a "
             "diagnostic, not something to leave on. Without it, every "
             "optimisation of a stage that is 94%% of the wall is a guess "
             "about which part of it is expensive.")
    ap.add_argument(
        "--max-block-pairs", type=int, default=50_000_000,
        help="refuse a pass whose LARGEST single block would emit more pairs "
             "than this. A skewed blocking key does not fail, it hangs -- the "
             "constant-prefix truncation bug put 20%% of the table in one "
             "block and the run sat in the join for 24 minutes until the step "
             "timeout killed it, with no output naming the cause. This turns "
             "that into a fast, specific refusal.")
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
        "stages": {}, "passes": [], "n_fields": args.fields, "value_pad": args.value_pad, "scorer_override": args.scorer or None,
    }

    t0 = time.perf_counter()
    df = build_fixture(spark, args.rows, args.dup, args.blocks_per_key,
                       value_pad=args.value_pad)
    # Materialise once so the fixture build is not re-run inside every stage
    # and charged to whichever stage happened to trigger it.
    df.cache()
    actual = df.count()
    out["stages"]["fixture_seconds"] = round(time.perf_counter() - t0, 2)
    out["actual_rows"] = actual
    print(f"[scale] fixture: {actual:,} rows in "
          f"{out['stages']['fixture_seconds']}s", flush=True)

    cfg = make_config(args.fields, args.scorer or None)
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

        # Skew check BEFORE the join, because after it there is nothing to
        # report -- the join is where a skewed key spends forever.
        t = time.perf_counter()
        top_key, top_rows = largest_block(df, key_config)
        skew_dt = time.perf_counter() - t
        top_pairs = top_rows * (top_rows - 1) // 2
        print(f"[scale] pass {i} on {list(fields)}: largest block "
              f"{top_key!r} has {top_rows:,} rows -> {top_pairs:,} pairs "
              f"({skew_dt:.2f}s)", flush=True)
        if top_pairs > args.max_block_pairs:
            raise SystemExit(
                f"[scale] pass {i} on {list(fields)} REFUSED: block "
                f"{top_key!r} holds {top_rows:,} rows, which is "
                f"{top_pairs:,} pairs on its own -- over --max-block-pairs="
                f"{args.max_block_pairs:,}. This is a skewed blocking key, "
                f"not a scale limit. Raise the flag only if the skew is the "
                f"thing under test."
            )

        cands = pass_candidates(df, key_config, id_col="__row_id__")
        joined = join_candidates_to_sources(cands, df, id_col="__row_id__")

        prof = None
        if args.profile_counts:
            prof = profile_counts(
                joined, mk, scorer_udf=ROW_UDF_NAME,
                transform_udf=TRANSFORM_UDF_NAME, cands=cands,
            )
            a = prof["attribution"]
            print(f"[scale] pass {i} counts attribution: "
                  f"pairs={a['pair_generation']}s join={a['record_join']}s "
                  f"scoring={a['scoring_udf']}s groupby={a['groupby_exchange']}s",
                  flush=True)

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
            # Recorded even when it passes the guard: skew that is merely bad
            # is invisible in the totals, and this is the number that moves
            # when a fixture change quietly reshapes the candidate set.
            "largest_block_rows": top_rows,
            "largest_block_key": top_key,
            "largest_block_pairs": top_pairs,
            # None unless --profile-counts. Present as an explicit null so a
            # reader can tell "not profiled" from "profiled and found nothing".
            "counts_profile": prof,
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
