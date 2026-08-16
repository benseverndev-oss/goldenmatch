"""Fellegi-Sunter training on the cluster, with no pair sample anywhere.

## The chain, complete

EM's E-step reads only the comparison vector (a level per field), so pairs with
the same vector are interchangeable and every M-step quantity is a sum that is
linear in how many pairs share it. That makes training decomposable into:

    compute gammas distributed  ->  GROUP BY them  ->  train from the counts

:func:`agreement_pattern_counts` is the first two steps; the third is
``core.probabilistic.train_em_from_counts``. :func:`train_em_distributed` is the
caller that strings them together, one session per blocking pass, over a ``u``
that :func:`estimate_u_distributed` counts the same way from random pairs.

What this replaces is the INPUT to the iteration, not the iteration. ``train_em``
reads a driver-side sample of blocked pairs capped by ``n_sample_pairs``, and
that cap is why a bigger cluster has never bought a better-trained model.

Splink does the same decomposition -- ``count_agreement_patterns_sql`` is
``select {gammas}, count(*) group by {gammas}`` -- and then runs the iteration
itself as engine-side SQL. This does the counting in the engine and leaves the
iteration on the driver, which is a real difference and is stated rather than
glossed: the iteration's input is bounded by the number of distinct comparison
vectors, so leaving it on the driver costs nothing that grows with the data.

## Why the result is small

The number of distinct vectors is at most ``prod(levels + 1)`` -- one extra for
``-1`` unobserved -- and within a session the blocking conditioning is constant,
so the pass needs no column of its own. A five-field model with four levels each
is at most 3,125 rows no matter whether it compared a thousand pairs or ten
billion. That bound is the whole reason this is safe to ``collect()``.

## No Python on the executors

``fs_level_expr`` and the weight lookup were always Spark SQL; the only part
that needed a Python worker was the per-field similarity call, and
``scorer_udf`` routes that to the jar's row-shaped kernel. So the counting runs
jar-only, like the scoring path it borrows from.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Refuse rather than silently truncate. A model whose fields have many levels
#: can in principle exceed this, and a `collect()` of an unexpectedly large
#: frame is an OOM on the driver -- the failure mode this repo has paid for
#: repeatedly. The bound is checked against the ACTUAL row count, not the
#: theoretical one, so a sparse model is not penalised for what it could emit.
MAX_PATTERNS = 200_000


def gamma_columns(mk: Any, lhs: str, rhs: str, *,
                  scorer_udf: str | None = None,
                  transform_udf: str | None = None) -> list[Any]:
    """One ``gamma_<field>`` column per comparison field: its FS level.

    The SAME expressions the scoring path builds, from the same functions --
    ``_field_similarity_and_observed`` then ``fs_level_expr``. Restating the
    ladder here would be a second implementation of what a level MEANS, and a
    training run that disagreed with scoring about levels would produce weights
    for a partition of the data that scoring never reproduces.
    """
    from goldenmatch.core.probabilistic import fs_missing_mode
    from goldenmatch.spark.probabilistic import (
        _field_similarity_and_observed,
        fs_level_expr,
    )

    missing_mode = fs_missing_mode(mk)
    out = []
    for f in mk.fields:
        sim, observed = _field_similarity_and_observed(
            f, lhs, rhs, scorer_udf=scorer_udf, transform_udf=transform_udf
        )
        out.append(
            fs_level_expr(f, sim, observed, missing_mode=missing_mode)
            .alias(f"gamma_{f.resolved_field}")
        )
    return out


def agreement_pattern_counts(
    joined: Any,
    mk: Any,
    *,
    lhs: str,
    rhs: str,
    scorer_udf: str | None = None,
    transform_udf: str | None = None,
    max_patterns: int = MAX_PATTERNS,
) -> list[tuple[tuple[int, ...], int]]:
    """``[(comparison_vector, count)]`` over every pair in ``joined``.

    ``joined`` is a candidate frame already joined to both sides under the
    aliases ``lhs``/``rhs`` -- the same shape ``score_candidates`` builds -- so
    this counts whatever pairs the caller blocked, with no sampling anywhere.

    The returned vectors are ordered by ``mk.fields``, which is the order
    ``_build_comparison_matrix`` produces on the one-box, so a caller can hand
    them straight to a comparison matrix without re-deriving the column order.
    """
    from pyspark.sql import functions as F

    gammas = gamma_columns(
        mk, lhs, rhs, scorer_udf=scorer_udf, transform_udf=transform_udf
    )
    names = [f"gamma_{f.resolved_field}" for f in mk.fields]
    grouped = (
        joined.select(*gammas)
        .groupBy(*[F.col(n) for n in names])
        .agg(F.count(F.lit(1)).alias("agreement_pattern_count"))
    )

    # ONE evaluation, not two. This used to be `grouped.count()` followed by
    # `grouped.collect()`, on the reasoning that the count "costs a fraction of"
    # the collect. It does not: Spark caches nothing here, so `count()` re-ran
    # the ENTIRE upstream DAG -- the candidate join, the per-pair scorer UDF over
    # every pair, and the groupBy -- and then `collect()` ran all of it again.
    #
    # MEASURED: at 5M rows / 49.2M candidate pairs this stage was 443.32s, 94.0%
    # of the whole distributed training wall (fixture 2.3%, u 3.7%, driver EM
    # 0.0%). Halving a stage that IS the wall is the single biggest lever on this
    # surface.
    #
    # `limit(max_patterns + 1)` keeps the guard exactly as strong. Under the
    # bound it returns every row (the point of the +1 is that overflow is
    # detectable), and it never materialises more than max_patterns + 1 rows on
    # the driver, which is what the guard existed to prevent.
    rows = grouped.limit(max_patterns + 1).collect()
    if len(rows) > max_patterns:
        # Only on the failure path, which is about to raise anyway: pay for one
        # more pass to report the EXACT count, so the error stays as actionable
        # as it was before (it names the number the model actually produced).
        n = grouped.count()
        raise ValueError(
            f"{n} distinct agreement patterns exceeds max_patterns="
            f"{max_patterns}. The bound is prod(levels + 1) over the "
            f"matchkey's fields, so this means the model has more level "
            f"combinations than expected -- collecting it would move the "
            f"training bottleneck back onto the driver, which is what this "
            f"path exists to avoid."
        )

    out = [
        (tuple(int(r[name]) for name in names), int(r["agreement_pattern_count"]))
        for r in rows
    ]
    # Sorted, so a model trained from these counts does not depend on the order
    # Spark happened to return partitions in. Determinism here is cheap and its
    # absence would be invisible: two runs would differ only in floating-point
    # accumulation order.
    out.sort()
    total = sum(c for _, c in out)
    logger.info(
        "FS EM: %d distinct agreement patterns over %d pairs (%.4g%% of pairs)",
        len(out), total, 100.0 * len(out) / max(total, 1),
    )
    return out


# ── u: the half of the likelihood ratio blocking cannot supply ───────


def _rows_needed_for_n_pairs(n_pairs: int) -> int:
    """Rows whose self-pairing yields about ``n_pairs``.

    The inverse of ``p(r) = r(r-1)/2``, solved for ``r``. Splink's
    ``estimate_u.py`` sizes its sample the same way, for the same reason: a
    cross join is quadratic, so the knob a caller wants to turn is the pair
    budget, and the row count is derived from it rather than guessed.
    """
    return max(2, int(0.5 * ((8 * n_pairs + 1) ** 0.5 + 1)))


def random_pairs(
    source_df: Any,
    *,
    id_col: str,
    max_pairs: int = 1_000_000,
    seed: int = 42,
    lhs: str | None = None,
    rhs: str | None = None,
) -> Any:
    """A joined frame of RANDOM record pairs, for estimating ``u``.

    Sampled and then self-joined, rather than joined and then sampled: the full
    cross join is quadratic in the table and is not something to materialise on
    the way to a sample of it.

    Not ``limit``: ``limit`` takes whatever rows the scan reaches first, which
    correlates with input order. On a file sorted by name that draws every pair
    from one corner of the data, and ``u`` -- the level distribution among
    NON-matches -- would be measured on a population unusually likely to agree.
    It would look like a working estimate.

    Not ``DataFrame.sample`` either, and this one is subtler. Both sides of the
    self-join reference the same plan, so the sampler is evaluated twice; it is
    seeded per partition, so the two evaluations agree **as long as the two
    scans partition identically**. That holds in practice and is not guaranteed
    -- AQE, a differing shuffle, or one side being cached would break it. If it
    ever did break, the join would pair rows from two different samples and
    still return a complete, plausible set of probabilities.

    A hash of the id is deterministic per ROW rather than per partition, so the
    question does not arise. ``xxhash64`` is uniform enough to select on
    directly, and folding the seed in keeps the sample reproducible and
    steerable. The cost is that the size is binomial around the target rather
    than exact -- about +/-3% of the rows at the default budget, so +/-5% of the
    pairs, which is noise against a level distribution.
    """
    total = source_df.count()
    if total < 2:
        raise ValueError(
            f"u needs at least 2 records to form a pair; the source has {total}"
        )

    from pyspark.sql import functions as F

    from goldenmatch.spark.config_pipeline import CAND_LHS, CAND_RHS

    lhs = lhs or CAND_LHS
    rhs = rhs or CAND_RHS
    want = _rows_needed_for_n_pairs(max_pairs)
    if want >= total:
        sample = source_df
    else:
        # `pmod` and not `abs`: hashing can return the minimum int64, whose
        # absolute value is not representable and comes back negative -- which
        # would silently exclude those rows from every sample.
        buckets = 1 << 31
        h = F.pmod(
            F.xxhash64(F.col(id_col), F.lit(int(seed))), F.lit(buckets)
        )
        sample = source_df.where(h < F.lit(float(want) / total * buckets))

    a = sample.alias(lhs)
    b = sample.alias(rhs)
    return a.join(b, F.col(f"{lhs}.{id_col}") < F.col(f"{rhs}.{id_col}"))


def estimate_u_distributed(
    source_df: Any,
    mk: Any,
    *,
    id_col: str,
    scorer_udf: str | None = None,
    transform_udf: str | None = None,
    max_pairs: int = 1_000_000,
    seed: int = 42,
    max_patterns: int = MAX_PATTERNS,
) -> dict[str, list[float]]:
    """``u`` estimated on the cluster, from random pairs.

    The same two steps as ``m``: count agreement patterns distributed, then do
    the arithmetic on the small counted result. ``u`` needs no EM at all -- it
    is one pass of counting -- so this is ``agreement_pattern_counts`` over a
    random-pair frame handed to
    :func:`goldenmatch.core.probabilistic.estimate_u_from_counts`.

    Reusing the same counter is the point. ``u`` and ``m`` must be measured
    against the SAME definition of a level, or ``log2(m/u)`` divides two numbers
    that are not about the same partition of the data. Deriving both from
    ``gamma_columns`` makes that true by construction instead of by review.
    """
    from goldenmatch.core.probabilistic import estimate_u_from_counts

    joined = random_pairs(
        source_df, id_col=id_col, max_pairs=max_pairs, seed=seed
    )
    from goldenmatch.spark.config_pipeline import CAND_LHS, CAND_RHS

    counts = agreement_pattern_counts(
        joined, mk, lhs=CAND_LHS, rhs=CAND_RHS,
        scorer_udf=scorer_udf, transform_udf=transform_udf,
        max_patterns=max_patterns,
    )
    n_pairs = sum(c for _, c in counts)
    logger.info(
        "FS u: estimated from %d random pairs (%d distinct patterns)",
        n_pairs, len(counts),
    )
    return estimate_u_from_counts(mk, counts)



# ── term-frequency tables: a GROUP BY over VALUES, not vectors ───────

#: A TF table is one entry per DISTINCT VALUE, so unlike the agreement-pattern
#: space it has no `prod(levels + 1)` ceiling -- a surname column can carry
#: millions. Collecting one is the same driver-OOM class `MAX_PATTERNS` guards,
#: and it is worth a separate, larger bound because the one-box builds exactly
#: the same dict from its own frame: the ceiling here is the driver's memory,
#: not a property of the distributed path.
MAX_TF_VALUES = 1_000_000


def tf_value_frequencies(
    source_df: Any,
    mk: Any,
    *,
    transform_udf: str | None = None,
    max_tf_values: int = MAX_TF_VALUES,
) -> tuple[dict[str, dict[str, float]] | None, dict[str, float] | None]:
    """Per-value relative frequencies for every ``tf_adjustment`` field.

    The distributed twin of ``core.probabilistic._build_tf_tables``. Mirrors
    ``core.tf_tables.value_frequencies`` exactly: apply the field's transform
    chain, drop nulls and empties, then ``count / total`` per distinct value --
    where ``total`` counts the SURVIVING values, not the rows, so a sparse
    column's frequencies still sum to 1.

    ``tf_collision[field] = sum(freq^2)`` is the expected exact-match collision
    rate, the baseline an agreement weight is adjusted against.

    ## Why this is separable from the counted trainer

    TF is a SCORING-time adjustment in this engine -- ``_em_iterate`` contains
    no reference to it -- so the table never enters the E-step. It only has to
    reach the ``EMResult`` that scoring reads. That is what lets counted
    training and TF coexist here: the counts discard the values, and this
    recovers them from the SOURCE, where they were never discarded.

    Splink is the other way round by default (``estimate_without_term_frequencies``
    is ``False``, so TF joins its E-step through the per-pair path, and its
    agreement-pattern-counts path is the ``True`` branch). Counted training and
    TF-in-training are mutually exclusive there too; the difference is that we
    never put TF in training at all.

    Returns ``(None, None)`` when no field opts in, matching the one-box.
    """
    from pyspark.sql import functions as F

    from goldenmatch.spark.config_pipeline import _transformed

    tf_fields = [f for f in mk.fields if getattr(f, "tf_adjustment", False)]
    if not tf_fields:
        return None, None

    cols = set(source_df.columns)
    tf_freqs: dict[str, dict[str, float]] = {}
    tf_collision: dict[str, float] = {}

    for f in tf_fields:
        name = f.resolved_field if hasattr(f, "resolved_field") else f.field
        if name not in cols:
            # The one-box skips an absent column rather than raising; a matchkey
            # naming a column this frame lacks is a config problem the scorer
            # reports, not something to fail training over.
            continue
        chain = list(getattr(f, "transforms", None) or [])
        raw = F.col(name)
        val = (
            _transformed(raw, chain, transform_udf=transform_udf)
            if chain
            else raw.cast("string")
        )
        # Nulls and empties are dropped BEFORE counting, and the transform runs
        # first: a chain may map a real value to empty (null_if_empty), and the
        # one-box drops those too. Counting them would put mass on a value no
        # pair can ever agree on.
        grouped = (
            source_df.select(val.alias("__tf_val__"))
            .where(F.col("__tf_val__").isNotNull() & (F.col("__tf_val__") != F.lit("")))
            .groupBy("__tf_val__")
            .agg(F.count(F.lit(1)).alias("__n__"))
        )

        n_distinct = grouped.count()
        if n_distinct > max_tf_values:
            raise ValueError(
                f"field {name!r} has {n_distinct} distinct values, over "
                f"max_tf_values={max_tf_values}. A TF table is collected to the "
                f"driver and stored in the model, so this is a driver-memory "
                f"bound rather than a property of the field. Raise max_tf_values "
                f"if the driver can hold it, or drop tf_adjustment for this "
                f"field."
            )

        rows = grouped.collect()
        total = float(sum(int(r["__n__"]) for r in rows))
        if total <= 0:
            continue
        freqs = {str(r["__tf_val__"]): int(r["__n__"]) / total for r in rows}
        tf_freqs[name] = freqs
        tf_collision[name] = sum(p * p for p in freqs.values())
        logger.info(
            "FS TF: %s -> %d distinct values over %.0f observations "
            "(collision %.6g)",
            name, len(freqs), total, tf_collision[name],
        )

    if not tf_freqs:
        return None, None
    return tf_freqs, tf_collision

# ── the caller: every link, strung together ──────────────────────────


def train_em_distributed(
    source_df: Any,
    config: Any,
    mk: Any,
    *,
    id_col: str,
    scorer_udf: str | None = None,
    transform_udf: str | None = None,
    u_max_pairs: int = 1_000_000,
    seed: int = 42,
    max_iterations: int = 20,
    convergence: float = 0.001,
    max_patterns: int = MAX_PATTERNS,
    max_tf_values: int = MAX_TF_VALUES,
    tf_freqs: dict[str, dict[str, float]] | None = None,
    tf_collision: dict[str, float] | None = None,
) -> Any:
    """Train one matchkey's FS model with no pair sample anywhere.

    The whole chain, in the order the links were built::

        u   <- count gammas over RANDOM pairs        -> estimate_u_from_counts
        m   <- count gammas per BLOCKING PASS        -> train_em_from_counts
        one <- combine the per-pass sessions         -> _combine_em_sessions

    What this changes versus ``train_em`` is the input to the iteration, not the
    iteration: ``train_em`` reads a driver-side sample of blocked pairs, capped
    by ``n_sample_pairs``, and that cap is the reason a bigger cluster has never
    bought a better-trained model. Here every pair the blocking produces is
    counted by the engine, and what reaches the driver is one row per distinct
    comparison vector -- bounded by ``prod(levels + 1)``, so thousands of rows
    whether the passes generated a million pairs or ten billion.

    One matchkey per call, because a matchkey is the unit a comparison vector is
    defined by. A config with several gets several calls and several models,
    which is the shape ``fs_models`` already takes.

    Returns an ``EMResult``, the same type ``train_em`` returns, usable anywhere
    a model trained on one box is.
    """
    from goldenmatch.core.probabilistic import (
        _combine_em_sessions,
        train_em_from_counts,
    )
    from goldenmatch.spark.config_pipeline import (
        CAND_LHS,
        CAND_RHS,
        blocking_passes,
        join_candidates_to_sources,
        pass_candidates,
    )

    # Refuse BEFORE submitting anything. `train_em_from_counts` rejects these
    # too, but reaching that check costs a distributed count over every blocking
    # pass first -- a config error should not cost a cluster job to discover.
    _refuse_unsupported(mk)

    passes = blocking_passes(config)
    if not passes:
        raise ValueError(
            "train_em_distributed needs at least one blocking pass "
            "(config.blocking.keys, or config.blocking.passes for multi_pass); "
            "without one there are no candidate pairs to estimate m from"
        )

    u_probs = estimate_u_distributed(
        source_df, mk, id_col=id_col, scorer_udf=scorer_udf,
        transform_udf=transform_udf, max_pairs=u_max_pairs, seed=seed,
        max_patterns=max_patterns,
    )

    # A GROUP BY over VALUES, not comparison vectors -- the one thing the counts
    # threw away. Computed once and carried onto every session, because it is a
    # property of the source population and not of any blocking pass.
    tf_freqs, tf_collision = tf_value_frequencies(
        source_df, mk, transform_udf=transform_udf, max_tf_values=max_tf_values
    ) if tf_freqs is None else (tf_freqs, tf_collision)

    sessions = []
    for i, key_config in enumerate(passes):
        fields = tuple(key_config.fields)
        candidates = pass_candidates(
            source_df, key_config, id_col=id_col, transform_udf=transform_udf
        )
        joined = join_candidates_to_sources(
            candidates, source_df, id_col=id_col
        )
        counts = agreement_pattern_counts(
            joined, mk, lhs=CAND_LHS, rhs=CAND_RHS,
            scorer_udf=scorer_udf, transform_udf=transform_udf,
            max_patterns=max_patterns,
        )
        if not counts:
            # A pass whose every key was null or a missing sentinel produces no
            # candidates. Skipping is right, and saying so matters: silently
            # dropping a pass would leave the fields only IT could estimate
            # sitting on the fixed prior with nothing indicating why.
            logger.warning(
                "FS EM: blocking pass %d on %s produced no candidate pairs; "
                "it contributes no session", i, list(fields),
            )
            continue
        em = train_em_from_counts(
            mk, counts, u_probs, conditioned_fields=fields,
            tf_freqs=tf_freqs, tf_collision=tf_collision,
            max_iterations=max_iterations, convergence=convergence,
        )
        # Weighted by the EXACT pair count, which the counts already carry --
        # the one-box `train_em_per_pass` has to approximate this with block row
        # counts because its sampler decides how many pairs it actually draws.
        weight = float(sum(c for _, c in counts))
        sessions.append((fields, em, weight))
        logger.info(
            "FS EM session: pass=%s pairs=%.0f patterns=%d converged=%s",
            fields, weight, len(counts), em.converged,
        )

    if not sessions:
        raise ValueError(
            "no blocking pass produced candidate pairs, so there is nothing to "
            "train m from. Check that the blocking keys are populated -- a key "
            "that is null or a missing sentinel for every record yields no "
            "blocks."
        )
    return _combine_em_sessions(mk, sessions)


def _refuse_unsupported(mk: Any) -> None:
    """Refuse configs the counted path cannot train, before submitting a job."""
    from goldenmatch.core.probabilistic import _em_ne_fields

    if _em_ne_fields(mk):
        raise NotImplementedError(
            f"matchkey {mk.name!r} has negative-evidence fields, which need a "
            f"per-pair NE matrix that counted comparison vectors do not carry. "
            f"Train it with train_em() on sampled pairs."
        )
    # NOT refused any more: term-frequency adjustment. The counts cannot derive
    # a TF table, but `tf_value_frequencies` recovers it from the SOURCE with a
    # separate GROUP BY, and TF never enters the E-step here anyway.
