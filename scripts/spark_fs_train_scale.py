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
from pathlib import Path


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
            F.when(h < F.lit(6), base)  # 60% clean
            .when(h < F.lit(8), F.regexp_replace(base, ".$", ""))  # 20% truncated
            .when(h < F.lit(9), F.concat(base, F.lit("x")))  # 10% typo
            .otherwise(F.lit(None).cast("string"))  # 10% missing
        )

    first = F.concat(F.lit("ann"), ent_s)
    last = F.concat(F.lit("lee"), (ent % F.lit(max(n_entities // 3, 1))).cast("string"))
    dob = F.concat(F.lit("19"), F.lpad((ent % F.lit(80)).cast("string"), 2, "0"), F.lit("-01-01"))
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
        F.concat(
            F.lit("k"), (ent / F.lit(max(blocks_per_key, 1))).cast("int").cast("string")
        ).alias("blk"),
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
    (joined.select(*gammas).groupBy(*[F.col(n) for n in names]).agg(F.count(F.lit(1))).collect())
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


def make_config(n_fields: int = 5, scorer: str | None = None, match_splink_levels: bool = False):
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

    cfg = _build_config(
        GoldenMatchConfig, BlockingConfig, BlockingKeyConfig, MatchkeyConfig, MatchkeyField
    )
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
    if match_splink_levels:
        # Mirror Splink's comparison ladder EXACTLY, because the accuracy
        # comparison was otherwise measuring a config difference and calling it
        # an engine difference.
        #
        # Splink's `JaroWinklerAtThresholds(f, [0.9, 0.7])` yields five states:
        # null, EXACT, jw >= 0.9, jw >= 0.7, else. This harness's default is
        # three -- agree / partial >= 0.7 / disagree -- which lumps "identical",
        # "jw 0.95" and "jw 0.75" together and is strictly less discriminating.
        # Measured at 200k rows on an identical pair population (1,854,038
        # pairs, 200,004 true): GM 0.7557 average precision against Splink's
        # 0.7911. A coarser ladder is also a cheaper one, so the speed advantage
        # was partly bought with it.
        #
        # `level_thresholds` are DESCENDING cutoffs and the level is the count
        # cleared, so [1.0, 0.9, 0.7] gives exact -> 3, >= 0.9 -> 2, >= 0.7 -> 1,
        # else 0, plus -1 unobserved: the same four informative levels Splink
        # has.
        #
        # `dob` is the odd one out. Splink compares it with
        # `DamerauLevenshteinAtThresholds([1, 2])` -- an EDIT DISTANCE -- while
        # this field is scored by a similarity ratio. The dates are a fixed ten
        # characters ("19xx-01-01"), so one edit is 0.9 and two is 0.8; the
        # thresholds below are that conversion, and they are only equivalent
        # because the width is fixed.
        for f in mk.fields:
            f.levels = 4
            f.level_thresholds = [1.0, 0.9, 0.8] if f.field == "dob" else [1.0, 0.9, 0.7]
    if n_fields < len(mk.fields):
        # Trim in place. Rebuilding a shorter literal risks the arms differing
        # in something other than field count, which is the one variable this
        # experiment must isolate.
        mk.fields = mk.fields[:n_fields]
    return cfg


def _build_config(
    GoldenMatchConfig, BlockingConfig, BlockingKeyConfig, MatchkeyConfig, MatchkeyField
):
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
                name="fs",
                type="probabilistic",
                fields=[
                    MatchkeyField(
                        field="first", scorer="jaro_winkler", levels=3, partial_threshold=0.7
                    ),
                    MatchkeyField(
                        field="last", scorer="jaro_winkler", levels=3, partial_threshold=0.7
                    ),
                    MatchkeyField(
                        field="dob", scorer="levenshtein", levels=3, partial_threshold=0.7
                    ),
                    MatchkeyField(
                        field="zip", scorer="jaro_winkler", levels=3, partial_threshold=0.7
                    ),
                    MatchkeyField(
                        field="city", scorer="jaro_winkler", levels=3, partial_threshold=0.7
                    ),
                ],
            ),
        ],
    )


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

    Both arms of the comparison score with this one implementation. Two
    hand-written average precisions that disagreed by a percent would look
    exactly like a model that is a percent better, which is the distinction the
    comparison exists to make.
    """
    import importlib.util

    path = Path(__file__).resolve().parent / "_fs_quality_metrics.py"
    spec = importlib.util.spec_from_file_location("_fs_quality_metrics", path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def score_narrow(df, cfg, mk, model, n_entities: int):
    """The ``(weight, is_true)`` frame the quality arm groups, both routes.

    ONE implementation, used by the real run and by ``--ab``. A separate copy
    for the A/B would measure a lookalike of the scoring path rather than the
    scoring path, which is the failure mode this benchmark exists to avoid.

    Which route builds the pairs is `GOLDENMATCH_SPARK_FUSED_BLOCK_JOIN`:

    * fused -- `fused_pass_frames` returns one joined frame per blocking pass,
      already disjoint by pass priority, so the candidate frame is never built.
      No `(a, b)` projection, no join back to either side, no cross-pass dedup.
    * legacy -- `generate_candidates` then `join_candidates_to_sources`, the
      three-join shape.

    The scoring expressions are identical either way and resolve against
    CAND_LHS/CAND_RHS, so the flag changes where the pairs come from and
    nothing about what is computed on them.
    """
    from goldenmatch.spark.config_pipeline import (
        CAND_LHS,
        CAND_RHS,
        fused_block_join_enabled,
        fused_pass_frames,
        generate_candidates,
        join_candidates_to_sources,
    )
    from goldenmatch.spark.em import gamma_columns
    from goldenmatch.spark.jvm import ROW_UDF_NAME, TRANSFORM_UDF_NAME
    from pyspark.sql import functions as F

    gammas = gamma_columns(
        mk, CAND_LHS, CAND_RHS,
        scorer_udf=ROW_UDF_NAME, transform_udf=TRANSFORM_UDF_NAME,
    )
    # Match weight = the per-field log2(m/u) for the level the pair landed on,
    # summed. `match_weights[field][level]` IS that table, so this is a lookup
    # rather than a second derivation of the weight.
    weight = F.lit(0.0)
    for col, f in zip(gammas, mk.fields):
        per_level = model.match_weights[f.resolved_field]
        expr = F.lit(0.0)
        for level, val in enumerate(per_level):
            expr = F.when(col == F.lit(level), F.lit(float(val))).otherwise(expr)
        weight = weight + expr
    truth = F.col(f"{CAND_LHS}.__row_id__") % F.lit(n_entities) == F.col(
        f"{CAND_RHS}.__row_id__"
    ) % F.lit(n_entities)

    if fused_block_join_enabled():
        # Union the NARROW per-pass projections -- two columns, not two whole
        # records. Pass priority already made the passes disjoint, so no dedup.
        narrow = None
        for frame in fused_pass_frames(df, cfg, id_col="__row_id__"):
            part = frame.select(weight.alias("w"), truth.alias("is_true"))
            narrow = part if narrow is None else narrow.unionByName(part)
        return narrow

    cands = generate_candidates(df, cfg, id_col="__row_id__")
    joined = join_candidates_to_sources(cands, df, id_col="__row_id__")
    return joined.select(weight.alias("w"), truth.alias("is_true"))


def run_ab(df, cfg, mk, model, n_entities: int, *, repeats: int):
    """PAIRED A/B of the fused vs legacy candidate route, in ONE session.

    ## Why paired, and why in one session

    Every comparison in this arc until now was a run against a BANKED baseline
    from a different job: different VMs, different JVM, different shuffle
    files. On this lane that is worth ~2% between two runs of the same code and
    up to ~16% across a re-run, which is the same order as the effects being
    measured -- so a 2% difference meant nothing and a 24% one had to be argued
    for. Running both arms over the same cached fixture in the same session
    removes that entirely: the only difference left IS the code path.

    ## Why the order alternates

    A first arm pays for whatever the session has not warmed -- JIT, the
    broadcast of the model, page cache on the shuffle dirs. Running
    ``A B B A`` and pairing (A1,B1) with (A2,B2) makes any monotone drift
    cancel rather than land on whichever arm went first. With ``repeats=1``
    the order is still ``A B B A``; repeats multiplies the palindrome.

    ## What it does NOT do

    It does not rebuild the fixture, re-estimate ``u`` or re-train. Those are
    identical across arms by construction -- the parity tests pin it -- so
    timing them again would add variance without adding information.
    """
    import os

    from goldenmatch.spark.config_pipeline import CAND_LHS, CAND_RHS, blocking_passes, pass_joined
    from goldenmatch.spark.em import agreement_pattern_counts
    from goldenmatch.spark.jvm import ROW_UDF_NAME, TRANSFORM_UDF_NAME

    flag = "GOLDENMATCH_SPARK_FUSED_BLOCK_JOIN"
    prior = os.environ.get(flag)
    # A palindrome per repeat: legacy, fused, fused, legacy.
    order = []
    for _ in range(max(repeats, 1)):
        order += ["legacy", "fused", "fused", "legacy"]

    arms: dict[str, list[dict]] = {"legacy": [], "fused": []}
    try:
        for arm in order:
            os.environ[flag] = "0" if arm == "legacy" else "1"

            t = time.perf_counter()
            n_pairs = 0
            for key_config in blocking_passes(cfg):
                joined = pass_joined(df, key_config, id_col="__row_id__")
                counts = agreement_pattern_counts(
                    joined, mk, lhs=CAND_LHS, rhs=CAND_RHS,
                    scorer_udf=ROW_UDF_NAME, transform_udf=TRANSFORM_UDF_NAME,
                )
                n_pairs += sum(c for _, c in counts)
            counts_s = time.perf_counter() - t

            t = time.perf_counter()
            groups = score_groups(df, cfg, mk, model, n_entities)
            score_s = time.perf_counter() - t

            arms[arm].append(
                {
                    "counts_seconds": round(counts_s, 2),
                    "score_seconds": round(score_s, 2),
                    "pairs": n_pairs,
                    # Carried so a reader can confirm the two arms scored the
                    # SAME population. Identical group counts and identical
                    # pair counts are what make the timing comparable at all.
                    "score_groups": len(groups),
                }
            )
            print(
                f"[ab] {arm:6s} counts={counts_s:8.2f}s score={score_s:8.2f}s "
                f"pairs={n_pairs:,} groups={len(groups)}",
                flush=True,
            )
    finally:
        if prior is None:
            os.environ.pop(flag, None)
        else:
            os.environ[flag] = prior

    def _med(vals):
        v = sorted(vals)
        n = len(v)
        return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0

    out = {"order": order, "arms": arms, "verdict": {}}
    for stage in ("counts_seconds", "score_seconds"):
        a = _med([r[stage] for r in arms["legacy"]])
        b = _med([r[stage] for r in arms["fused"]])
        out["verdict"][stage] = {
            "legacy_median": round(a, 2),
            "fused_median": round(b, 2),
            "speedup": round(a / b, 3) if b else None,
            # The spread WITHIN an arm is the noise floor. A speedup smaller
            # than this is not a result, and reporting it without this number
            # would invite exactly the over-reading this mode exists to stop.
            "legacy_spread_pct": round(
                100.0 * (max(r[stage] for r in arms["legacy"])
                         - min(r[stage] for r in arms["legacy"])) / a, 1
            ) if a else None,
            "fused_spread_pct": round(
                100.0 * (max(r[stage] for r in arms["fused"])
                         - min(r[stage] for r in arms["fused"])) / b, 1
            ) if b else None,
        }
    # Same pairs on both arms or the comparison is void, not merely noisy.
    pairs = {r["pairs"] for rs in arms.values() for r in rs}
    groups = {r["score_groups"] for rs in arms.values() for r in rs}
    out["same_population"] = len(pairs) == 1 and len(groups) == 1
    if not out["same_population"]:
        print(
            f"[ab] WARNING arms scored DIFFERENT populations: pairs={pairs} "
            f"groups={groups}. The timings are not comparable.",
            flush=True,
        )
    return out


def score_groups(df, cfg, mk, model, n_entities: int):
    """``[(weight, n_true, n_all)]`` -- the quality arm's exact input.

    GROUP BY the weight in the ENGINE and collect the groups. This used to
    collect one `(score, is_true)` tuple PER CANDIDATE PAIR: 5.5M at the 1M
    scale this harness was written for and merely wasteful, but 275M at 50M
    rows -- tens of GB of Python objects on a driver inside a container -- so
    the quality arm could not run at the scale the comparison is about, and a
    head-to-head with no accuracy number is not a head-to-head.

    Grouping is EXACT, not a sample. `ranking_metrics` already consumes its
    input in tie-groups: it advances to the next distinct score, admits every
    pair at that score, and only then computes precision/recall, so the
    per-pair identity inside a group is never used, only the counts.
    `ranking_metrics_grouped` takes exactly those counts and
    `test_fs_quality_metrics_grouped.py` pins the two to agree on shared
    inputs, including ties that span both classes.

    The group count is small BY THE PROPERTY THIS BENCHMARK IS ABOUT: a pair's
    weight is a sum of per-field match weights over bounded gamma levels, so
    the reachable score set is bounded by `prod(levels + 1)` -- the same bound
    that keeps the counting GROUP BY small. 275M pairs collapse to a few
    hundred rows.
    """
    from pyspark.sql import functions as F

    return [
        (float(r["w"]), int(r["n_true"]), int(r["n_all"]))
        for r in (
            score_narrow(df, cfg, mk, model, n_entities)
            .groupBy("w")
            .agg(
                F.sum(F.col("is_true").cast("long")).alias("n_true"),
                F.count(F.lit(1)).alias("n_all"),
            )
            .collect()
        )
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=1_000_000)
    ap.add_argument("--dup", type=int, default=3, help="rows per entity")
    ap.add_argument(
        "--blocks-per-key", type=int, default=4, help="entities sharing one blocking value"
    )
    ap.add_argument(
        "--remote", default=os.environ.get("GOLDENMATCH_SPARK_REMOTE", "sc://localhost:15002")
    )
    ap.add_argument("--u-max-pairs", type=int, default=1_000_000)
    ap.add_argument(
        "--scorer",
        default="",
        help="Force every comparison field onto one scorer. Use `exact` with "
        "--value-pad: padding preserves equality, so gammas are unchanged "
        "and only the marshalled bytes move. Under jaro_winkler/levenshtein "
        "padding CHANGES similarity and the arms stop being comparable.",
    )
    ap.add_argument(
        "--value-pad",
        type=int,
        default=0,
        help="Append N filler chars to every COMPARISON value. Crossing COUNT "
        "is unchanged; only the bytes marshalled per crossing move. This "
        "is the arm that separates per-CALL from per-BYTE scoring cost -- "
        "`--fields` CANNOT, because adding a field adds a call AND its "
        "bytes, so both hypotheses predict the same scaling.",
    )
    ap.add_argument(
        "--fields",
        type=int,
        default=5,
        help="Number of COMPARISON fields (max 5). Same blocking, same "
        "candidate pairs -- only the per-pair crossing count changes. "
        "Run 5 vs 2 and compare `scoring_udf`: linear in field count "
        "means the cost is per-CALL (fewer, fatter calls win); flat means "
        "per-BYTE (only columnar/FFI moves it).",
    )
    ap.add_argument(
        "--ab",
        type=int,
        default=0,
        metavar="REPEATS",
        help="PAIRED A/B of the fused vs legacy candidate route in one "
        "session over the same cached fixture, alternating legacy/fused/"
        "fused/legacy per repeat. Removes the run-to-run variance that made "
        "single-arm comparisons against a banked baseline unreadable, so it "
        "discriminates at a size small enough to iterate on. 0 disables.",
    )
    ap.add_argument(
        "--profile-counts",
        action="store_true",
        help="Attribute the counts stage across pair generation / record "
        "join / scoring UDF / GROUP BY exchange. Times PREFIXES of the "
        "same DAG, so it costs ~3x a normal counts stage -- a "
        "diagnostic, not something to leave on. Without it, every "
        "optimisation of a stage that is 94%% of the wall is a guess "
        "about which part of it is expensive.",
    )
    ap.add_argument(
        "--max-block-pairs",
        type=int,
        default=50_000_000,
        help="refuse a pass whose LARGEST single block would emit more pairs "
        "than this. A skewed blocking key does not fail, it hangs -- the "
        "constant-prefix truncation bug put 20%% of the table in one "
        "block and the run sat in the join for 24 minutes until the step "
        "timeout killed it, with no output naming the cause. This turns "
        "that into a fast, specific refusal.",
    )
    ap.add_argument(
        "--match-splink-levels",
        action="store_true",
        help="Give every comparison field the same four informative "
        "levels Splink uses (exact / 0.9 / 0.7 / else) instead "
        "of this harness's default three. Without it an "
        "accuracy comparison measures a ladder difference and "
        "reports it as an engine difference.",
    )
    ap.add_argument(
        "--eval-quality",
        action="store_true",
        help="After training, score the SAME candidate pairs with "
        "the trained model and report ranking quality against "
        "the fixture's known entity structure. The speed "
        "number means nothing without it: the bar is "
        "match-or-better accuracy, not a faster arrival at a "
        "worse model.",
    )
    ap.add_argument(
        "--spark-ui",
        default="http://localhost:4041",
        help="Spark UI of the CONNECT driver (compose maps its 4040 "
        "to 4041, because Splink's classic driver binds 4040 on "
        "the host). Read for stage-level shuffle bytes.",
    )
    ap.add_argument(
        "--shuffle-partitions",
        type=int,
        default=0,
        help="spark.sql.shuffle.partitions. 0 leaves Spark's default, "
        "-1 derives 5x total executor cores per Splink's guide. "
        "Applied IDENTICALLY to both arms via scripts/_spark_tuning.py.",
    )
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    from goldenmatch.spark.config_pipeline import (
        CAND_LHS,
        CAND_RHS,
        blocking_passes,
        fused_block_join_enabled,
        join_candidates_to_sources,
        pass_candidates,
        pass_joined,
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
    _applied_partitions = _tuning_module().apply_shuffle_partitions(
        spark, args.shuffle_partitions, spark_ui=args.spark_ui
    )
    install(spark, jar=find_jar())
    impl, _diagnostics, runtime = implementation(spark)
    print(f"[scale] kernel impl={impl} runtime={runtime}", flush=True)

    out: dict = {
        "rows": args.rows,
        "dup": args.dup,
        "blocks_per_key": args.blocks_per_key,
        "kernel_impl": impl,
        "kernel_runtime": runtime,
        # None means Spark's default was left alone. Recorded so an artifact
        # can never be read without knowing which partition count produced it.
        "shuffle_partitions": _applied_partitions,
        "u_max_pairs": args.u_max_pairs,
        "stages": {},
        "passes": [],
        "n_fields": args.fields,
        "value_pad": args.value_pad,
        "scorer_override": args.scorer or None,
        # Which candidate-join route ran. Recorded rather than inferred: the
        # two produce identical counts, so nothing else in the artifact says
        # which one produced them, and an A/B whose arms are indistinguishable
        # after the fact is not an A/B.
        "fused_block_join": fused_block_join_enabled(),
    }

    t0 = time.perf_counter()
    df = build_fixture(spark, args.rows, args.dup, args.blocks_per_key, value_pad=args.value_pad)
    # Materialise once so the fixture build is not re-run inside every stage
    # and charged to whichever stage happened to trigger it.
    df.cache()
    actual = df.count()
    out["stages"]["fixture_seconds"] = round(time.perf_counter() - t0, 2)
    out["actual_rows"] = actual
    print(f"[scale] fixture: {actual:,} rows in {out['stages']['fixture_seconds']}s", flush=True)

    cfg = make_config(
        args.fields, args.scorer or None, match_splink_levels=args.match_splink_levels
    )
    mk = cfg.get_matchkeys()[0]

    # ── u: sampled self-join. Cost tracks the SAMPLE, not the table. ──
    t = time.perf_counter()
    u_probs = estimate_u_distributed(
        df,
        mk,
        id_col="__row_id__",
        scorer_udf=ROW_UDF_NAME,
        transform_udf=TRANSFORM_UDF_NAME,
        max_pairs=args.u_max_pairs,
        # The harness counted the fixture moments ago; re-counting 50M rows to
        # derive a sampling fraction is a full pass for arithmetic we have.
        total_rows=actual,
    )
    out["stages"]["u_seconds"] = round(time.perf_counter() - t, 2)
    out["u_probs"] = {k: [round(x, 6) for x in v] for k, v in u_probs.items()}
    print(f"[scale] u in {out['stages']['u_seconds']}s -> {out['u_probs']}", flush=True)

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
        print(
            f"[scale] pass {i} on {list(fields)}: largest block "
            f"{top_key!r} has {top_rows:,} rows -> {top_pairs:,} pairs "
            f"({skew_dt:.2f}s)",
            flush=True,
        )
        if top_pairs > args.max_block_pairs:
            raise SystemExit(
                f"[scale] pass {i} on {list(fields)} REFUSED: block "
                f"{top_key!r} holds {top_rows:,} rows, which is "
                f"{top_pairs:,} pairs on its own -- over --max-block-pairs="
                f"{args.max_block_pairs:,}. This is a skewed blocking key, "
                f"not a scale limit. Raise the flag only if the skew is the "
                f"thing under test."
            )

        # ONE join, not three: the block self-join already holds both records
        # of every pair, so `pass_joined` keeps them instead of projecting them
        # away and paying two more pair-sized shuffles to fetch them back.
        joined = pass_joined(df, key_config, id_col="__row_id__")

        prof = None
        if args.profile_counts:
            # Profiles the LEGACY frame on purpose. The attribution's whole
            # shape -- pair generation, THEN the record join, then scoring -- is
            # a property of the three-join path; the fused path has no separate
            # record join to attribute time to, so running it here would report
            # a negative number for a step that no longer exists. What this arm
            # measures is therefore the path it names, and the fused wall is the
            # `count_seconds` below rather than anything in here.
            cands = pass_candidates(df, key_config, id_col="__row_id__")
            prof = profile_counts(
                join_candidates_to_sources(cands, df, id_col="__row_id__"),
                mk,
                scorer_udf=ROW_UDF_NAME,
                transform_udf=TRANSFORM_UDF_NAME,
                cands=cands,
            )
            a = prof["attribution"]
            print(
                f"[scale] pass {i} counts attribution: "
                f"pairs={a['pair_generation']}s join={a['record_join']}s "
                f"scoring={a['scoring_udf']}s groupby={a['groupby_exchange']}s",
                flush=True,
            )

        t = time.perf_counter()
        counts = agreement_pattern_counts(
            joined,
            mk,
            lhs=CAND_LHS,
            rhs=CAND_RHS,
            scorer_udf=ROW_UDF_NAME,
            transform_udf=TRANSFORM_UDF_NAME,
        )
        dt = time.perf_counter() - t
        count_wall += dt
        n_pairs = sum(c for _, c in counts)

        t = time.perf_counter()
        em = train_em_from_counts(mk, counts, u_probs, conditioned_fields=fields)
        train_wall += time.perf_counter() - t
        sessions.append((fields, em, float(n_pairs)))

        out["passes"].append(
            {
                "pass": i,
                "blocking_fields": list(fields),
                "pairs": n_pairs,
                "distinct_patterns": len(counts),
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
            }
        )
        print(
            f"[scale] pass {i} on {list(fields)}: {n_pairs:,} pairs -> "
            f"{len(counts)} distinct patterns in {dt:.2f}s",
            flush=True,
        )

    out["stages"]["counts_seconds"] = round(count_wall, 2)
    out["stages"]["train_seconds"] = round(train_wall, 2)

    model = _combine_em_sessions(mk, sessions)
    out["m_probs"] = {k: [round(x, 6) for x in v] for k, v in model.m_probs.items()}
    out["match_weights"] = {k: [round(x, 4) for x in v] for k, v in model.match_weights.items()}
    out["proportion_matched"] = round(model.proportion_matched, 6)

    if args.eval_quality:
        # Score the SAME candidate pairs the training used, with the model just
        # trained, and rank them against the fixture's known entity structure.
        #
        # Truth needs no label column: `build_fixture` assigns
        # entity = id % n_entities with n_entities = rows // dup, so two rows
        # are a true pair exactly when their ids are congruent. Carrying a label
        # column instead would change the frame and make the quality fixture
        # differ from the one the speed runs measure.
        #
        # Weights come from `gamma_columns` -- the same expressions the SCORING
        # path builds -- so this measures the shipped model on the shipped
        # ladder, not a re-derivation of what a level means.
        n_entities = max(args.rows // max(args.dup, 1), 1)
        t = time.perf_counter()
        groups = score_groups(df, cfg, mk, model, n_entities)
        out["stages"]["score_seconds"] = round(time.perf_counter() - t, 2)

        if args.ab:
            out["ab"] = run_ab(
                df, cfg, mk, model, n_entities, repeats=args.ab
            )
        # Recorded so a reader can see the collect stayed bounded, rather than
        # trusting the argument above.
        out["quality_score_groups"] = len(groups)
        q = _metrics_module().ranking_metrics_grouped(groups)
        # `dup` rows per entity give dup*(dup-1)/2 true pairs each, and every
        # entity's rows share a blocking key by construction, so the candidate
        # set must contain ALL of them and no duplicates. Checked rather than
        # assumed: the un-deduplicated first version reported 282,247 true pairs
        # against an expected 200,004 and the inflated base rate raised average
        # precision, which is exactly the kind of error that reads as a result.
        # Exact, including the remainder. `rows` is rarely a multiple of
        # n_entities, so a few entities carry one extra row and contribute
        # C(q+1, 2) rather than C(q, 2). The naive n_entities*C(dup,2) gives
        # 199,998 at 200k/dup=3 where the truth is 200,004 -- and a guard that
        # is wrong by six fires on every correct run, gets read as noise, and
        # then gets deleted.
        _n_ent = max(args.rows // max(args.dup, 1), 1)
        _q, _r = divmod(args.rows, _n_ent)
        expected_true = _r * ((_q + 1) * _q // 2) + (_n_ent - _r) * (_q * (_q - 1) // 2)
        q["expected_true_pairs"] = expected_true
        q["true_pairs_match_expected"] = q["n_true"] == expected_true
        if not q["true_pairs_match_expected"]:
            print(
                f"[scale] WARNING quality population is wrong: found "
                f"{q['n_true']:,} true pairs, fixture contains "
                f"{expected_true:,}. The metric below is NOT comparable to "
                f"the other engine's.",
                flush=True,
            )
        out["quality"] = q
        print(f"[scale] quality {out['quality']}", flush=True)

    # Shuffle BYTES, the topology-independent half of the multi-node question.
    # A wall measured on two containers sharing one host says nothing about a
    # real cluster, because the exchange never crosses a network here. What
    # crosses it does transfer: network cost on any topology is a function of
    # bytes. The prediction under test is that GM's counting stage moves
    # ~partitions x distinct patterns regardless of pair count, because the
    # GROUP BY output is bounded by prod(levels + 1) and Spark combines
    # map-side. Never fatal -- a metrics endpoint that is down must not take the
    # measurement with it, so a failure is RECORDED rather than raised.
    out["shuffle"] = _shuffle_module().fetch(args.spark_ui)
    print(f"[scale] shuffle {out['shuffle']}", flush=True)

    out["total_seconds"] = round(sum(out["stages"].values()), 2)

    print(
        f"[scale] DONE rows={actual:,} total={out['total_seconds']}s stages={out['stages']}",
        flush=True,
    )
    print(f"[scale] model m={out['m_probs']} weights={out['match_weights']}", flush=True)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=2)
        print(f"[scale] wrote {args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
