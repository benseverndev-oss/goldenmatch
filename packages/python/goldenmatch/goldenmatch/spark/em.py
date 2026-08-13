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

    Sampled and then cross-joined, rather than cross-joined and then sampled:
    the full cross join is quadratic in the table and is not something to
    materialise on the way to a sample of it.

    ``sample`` and not ``limit``: ``limit`` takes whatever rows the scan reaches
    first, which correlates with input order. On a file sorted by name that
    draws every pair from one corner of the data, and ``u`` -- the level
    distribution among non-matches -- would be measured on a population that is
    unusually likely to agree. It would look like a working estimate.
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
        # Oversample by 10% then cap: `sample` draws each row independently, so
        # the size is binomial around the fraction and a bare fraction lands
        # under target about half the time.
        fraction = min(1.0, (want / total) * 1.1)
        sample = source_df.sample(withReplacement=False, fraction=fraction, seed=seed)
        sample = sample.limit(want)

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
    if any(getattr(f, "tf_adjustment", False) for f in mk.fields):
        raise NotImplementedError(
            f"matchkey {mk.name!r} uses term-frequency adjustment, which needs "
            f"per-value frequencies that counted comparison vectors do not "
            f"carry. Train it with train_em() on sampled pairs."
        )
