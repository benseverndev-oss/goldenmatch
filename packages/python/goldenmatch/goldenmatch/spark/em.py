"""Count agreement patterns on the cluster, so FS training need not sample.

## The chain this is one link of

EM's E-step reads only the comparison vector (a level per field), so pairs with
the same vector are interchangeable and every M-step quantity is a sum that is
linear in how many pairs share it. That makes training decomposable into:

    compute gammas distributed  ->  GROUP BY them  ->  train_em(pair_weights=counts)

This module is the first two. The third is ``core.probabilistic.train_em``'s
``pair_weights``, and the per-pass split it runs under is
``train_em_per_pass``.

Splink does exactly this -- ``count_agreement_patterns_sql`` is
``select {gammas}, count(*) group by {gammas}`` -- and then runs the iteration
itself as engine-side SQL. This does the counting in the engine and leaves the
iteration on the driver, which is a real difference and is stated rather than
glossed: what it buys is that the iteration's input stops being a sample and
stops being proportional to the pair count.

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

    # Bound BEFORE collecting. `count()` on the grouped frame is one more
    # distributed pass and costs a fraction of what materialising an
    # unexpectedly large result on the driver would.
    n = grouped.count()
    if n > max_patterns:
        raise ValueError(
            f"{n} distinct agreement patterns exceeds max_patterns="
            f"{max_patterns}. The bound is prod(levels + 1) over the "
            f"matchkey's fields, so this means the model has more level "
            f"combinations than expected -- collecting it would move the "
            f"training bottleneck back onto the driver, which is what this "
            f"path exists to avoid."
        )

    rows = grouped.collect()
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
