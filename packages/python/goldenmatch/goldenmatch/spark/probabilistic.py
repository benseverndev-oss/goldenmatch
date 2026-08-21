"""P5: Fellegi-Sunter scoring on Spark.

This is what makes a Splink user's model executable at all. `from_splink` emits a
single **probabilistic** matchkey plus a trained model, and until now the Spark
tier had no `m_prob`, `u_prob` or `match_weight` anywhere in it -- so an imported
Splink config could not run distributed no matter how the rest was wired.

**Scoring is distributed; training is not, and that is the existing semantics
rather than a compromise.** `core.probabilistic.train_em` learns from a SAMPLE of
blocked pairs (`n_sample_pairs=10000` by default), so the training set is small
by construction on any dataset size. A Splink user brings weights already trained
elsewhere; a GoldenMatch user trains once and reuses via `model_path`. Neither
needs a distributed E-step, and inventing one would change the numbers rather
than reproduce them.

The math, per pair (`comparison_vector` + `fs_regular_weight_sum` +
`posterior_from_weight` are the authority, and each is mirrored here as a Spark
expression):

    sim_f     = scorer(a.f, b.f)                        # null if either missing
    level_f   = threshold assignment over sim_f
    weight_f  = match_weights[f][level_f]               # skipped when unobserved
    total     = Σ weight_f
    posterior = 1 / (1 + 2^-(prior_weight + total))

Every one of those is arithmetic over a handful of per-field constants, so the
model rides along as inlined `CASE` expressions -- no broadcast join, no lookup
table, no UDF beyond the per-field similarity kernel the tier already has.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# The weight applied to a field whose comparison is UNOBSERVED. Zero is not an
# arbitrary neutral: it is what `fs_regular_weight_sum` produces by SKIPPING the
# field, and 2^0 = 1 is a likelihood ratio of exactly one -- absence of evidence
# moving the posterior not at all.
_UNOBSERVED_WEIGHT = 0.0

# `posterior_from_weight`'s overflow clamp, verbatim.
_LOGODDS_CLAMP = 60.0


class FSSparkUnsupported(NotImplementedError):
    """A probabilistic feature the Spark tier cannot execute faithfully."""


def _validate_fs_spark_supported(mk: Any, em: Any) -> None:
    """Refuse FS configurations whose numbers this path would silently change.

    Every branch is a feature that contributes to the one-box score and has no
    expression here -- running without it would return a plausible number that
    is not the model's answer.
    """
    from goldenmatch.spark.scorers import _SUPPORTED as _SUPPORTED_SCORERS

    if getattr(mk, "negative_evidence", None):
        raise FSSparkUnsupported(
            f"matchkey {mk.name!r}: negative evidence is not supported on the "
            f"Spark tier. Its EM-learned NE weights are part of the score, so "
            f"dropping them would shift every pair that fires one."
        )
    tf_fields = [
        f.resolved_field for f in mk.fields if getattr(f, "tf_adjustment", False)
    ]
    if tf_fields:
        raise FSSparkUnsupported(
            f"matchkey {mk.name!r}: term-frequency (Winkler) adjustment on "
            f"{tf_fields} is not supported on the Spark tier. It needs the "
            f"per-value frequency table from training, applied per pair."
        )
    for f in mk.fields:
        if f.scorer in ("embedding", "record_embedding"):
            raise FSSparkUnsupported(
                f"matchkey {mk.name!r} field {f.resolved_field!r}: model-backed "
                f"scorer {f.scorer!r} is not supported on the Spark tier."
            )
        if f.scorer not in _SUPPORTED_SCORERS and f.scorer != "exact":
            raise FSSparkUnsupported(
                f"matchkey {mk.name!r} field {f.resolved_field!r}: scorer "
                f"{f.scorer!r} is not supported on the Spark tier "
                f"(supported: {(*_SUPPORTED_SCORERS, 'exact')})."
            )

    missing = [
        f.resolved_field for f in mk.fields
        if f.resolved_field not in (em.match_weights or {})
    ]
    if missing:
        raise ValueError(
            f"the trained model has no match weights for {missing}; it was "
            f"trained against a different field set than matchkey {mk.name!r} "
            f"declares. Re-train, or point model_path at the matching model."
        )


# ── level assignment ─────────────────────────────────────────────────

def level_thresholds_for(field: Any) -> list[float]:
    """The ASCENDING cut points whose satisfied-count is the comparison level.

    `comparison_vector` spells the same assignment four ways (custom list, 2
    levels, 3 levels, N evenly spaced). All four are "count the thresholds this
    similarity clears", so they collapse to one list here -- and collapsing them
    is what lets the Spark side be a sum of booleans rather than a nested CASE
    per shape.

    The 3-level case is the one worth checking against the original: it is
    `partial_threshold` then a hard-coded 0.95, NOT even spacing.
    """
    custom = getattr(field, "level_thresholds", None)
    if custom is not None:
        return sorted(float(t) for t in custom)
    n = int(getattr(field, "levels", 2) or 2)
    partial = float(getattr(field, "partial_threshold", 0.5))
    if n == 2:
        return [partial]
    if n == 3:
        return [partial, 0.95]
    return [k / n for k in range(1, n)]


def fs_pair_weight(
    levels: list[int], match_weights: dict[str, list[float]], fields: list[str]
) -> float:
    """`fs_regular_weight_sum`, stated over an already-computed level vector.

    A level of ``-1`` is UNOBSERVED and contributes nothing. That guard is the
    whole reason the one-box function exists: Python's ``weights[-1]`` would
    pick the LAST element -- the highest-agreement weight -- so a missing field
    would supply maximal evidence *for* a match.

    Pure so it can be unit-tested against the one-box without Spark; the Spark
    expression below mirrors it.
    """
    return sum(
        match_weights[name][lvl]
        for lvl, name in zip(levels, fields)
        if lvl >= 0
    )


def fs_posterior(total_weight: float, prior_w: float) -> float:
    """`posterior_from_weight`, restated (pure, for the same reason)."""
    logodds = prior_w + total_weight
    if logodds > _LOGODDS_CLAMP:
        return 1.0
    if logodds < -_LOGODDS_CLAMP:
        return 0.0
    return 1.0 / (1.0 + 2.0 ** (-logodds))


# ── Spark expressions ────────────────────────────────────────────────

def _field_similarity_and_observed(
    field: Any, lhs: str, rhs: str, *,
    scorer_udf: str | None = None,
    transform_udf: str | None = None,
) -> tuple[Any, Any]:
    """``(similarity, observed)`` for one comparison field.

    ``observed`` is read from the RAW columns, matching `score_field`'s "None if
    either value is None" -- and deliberately NOT from the similarity kernel,
    which substitutes ``""`` for a missing value and therefore reports
    null-vs-null as a perfect 1.0.

    **This is the only part of Spark FS that ever needed Python.** Everything
    above it -- levels, the weight lookup, the sum, the posterior -- is already
    Spark SQL. So passing ``scorer_udf`` (and ``transform_udf``) is the whole of
    what it takes to run Fellegi-Sunter with nothing installed on the executors:
    the similarity call moves to the jar's row-shaped kernel and the rest was
    never leaving the JVM in the first place.
    """
    from pyspark.sql import functions as F

    from goldenmatch.spark.config_pipeline import _transformed

    col = field.resolved_field
    a_raw, b_raw = F.col(f"{lhs}.{col}"), F.col(f"{rhs}.{col}")

    chain = list(getattr(field, "transforms", None) or [])
    if chain:
        a_val = _transformed(a_raw, chain, transform_udf=transform_udf)
        b_val = _transformed(b_raw, chain, transform_udf=transform_udf)
    else:
        a_val, b_val = a_raw.cast("string"), b_raw.cast("string")

    if field.scorer == "exact":
        # No kernel either way: the agreement IS the similarity, in SQL.
        sim = F.when(a_val == b_val, F.lit(1.0)).otherwise(F.lit(0.0))
    elif scorer_udf is not None:
        from goldenmatch.spark.jvm import scorer_id

        # `scorer_id` RAISES for a scorer the jar cannot run, and that is the
        # right behaviour: silently falling back to the arrow_udf would mean a
        # caller who asked for a jar-only run still needs an executor
        # virtualenv, and would find out from a ModuleNotFoundError mid-job.
        sim = F.call_udf(
            scorer_udf, F.lit(int(scorer_id(field.scorer))), a_val, b_val
        )
    else:
        from goldenmatch.spark.scorers import make_scorer_udf

        sim = make_scorer_udf(field.scorer)(a_val, b_val)

    return sim, (a_raw.isNotNull() & b_raw.isNotNull())


def fs_level_expr(field: Any, sim: Any, observed: Any, *, missing_mode: str) -> Any:
    """The comparison level for one field, as a Spark expression.

    Unobserved is ``-1`` under ``missing='unobserved'`` (textbook FS: absence of
    evidence) and ``0`` under ``missing='disagree'`` (evidence against). Which is
    correct depends on whether missingness is informative in the data, which is
    why the one-box makes it a config choice rather than a library opinion --
    so this path must honour BOTH rather than pick one.
    """
    from pyspark.sql import functions as F

    level = F.lit(0)
    for t in level_thresholds_for(field):
        level = level + F.when(sim >= F.lit(float(t)), F.lit(1)).otherwise(F.lit(0))

    unobserved = F.lit(-1) if missing_mode == "unobserved" else F.lit(0)
    return F.when(observed, level).otherwise(unobserved)


def _weight_lookup_expr(level: Any, weights: list[float]) -> Any:
    """``match_weights[field][level]`` as a CASE over the level.

    The per-field weight vector is a handful of floats, so it is inlined rather
    than broadcast-joined: the model travels inside the query plan and the
    executors need nothing shipped to them.
    """
    from pyspark.sql import functions as F

    if not weights:
        return F.lit(_UNOBSERVED_WEIGHT)
    # ONE reference to `level`, deliberately. This used to be a CASE chain --
    # `when(level == 0, w0).when(level == 1, w1)...` -- which names `level` once
    # per level, and `level` is the WHOLE gamma expression with the jar scorer
    # call inside it.
    #
    # Catalyst's subexpression elimination does NOT hoist that call out of the
    # CASE branches. MEASURED at 50M, splitting the score stage by layer
    # (`--attribute-score`):
    #
    #     join      13.47s
    #     + gammas 125.79s   (+112.32 marginal)
    #     + weight 340.75s   (+214.96 marginal)   <- 2.71x the gamma layer
    #     + truth  349.69s   (+8.94)
    #
    # 2.71x on a three-level model is the level count: the scorer ran once per
    # branch. A synthetic probe using a pure-SQL stand-in for the scorer showed
    # no such penalty, because CSE handles NATIVE expressions fine -- the UDF is
    # the case it does not. That probe is why this comment names the measurement
    # that used the real kernel.
    #
    # `get` and not `element_at`: element_at is 1-based, treats negative indices
    # as counting from the END, and under ANSI mode THROWS on an out-of-range
    # index rather than returning null. `get` is 0-based and returns null for
    # any out-of-range index, ANSI or not, which is exactly the total function
    # this needs.
    #
    # Index 0 holds the unobserved weight, so `level = -1` lands on it with no
    # branch. Levels outside the trained range index past the end and come back
    # null, and the coalesce sends them to the same place the old `otherwise`
    # did. Zero == skipping the field, which is exactly `fs_regular_weight_sum`.
    table = F.array(
        F.lit(float(_UNOBSERVED_WEIGHT)),
        *[F.lit(float(w)) for w in weights],
    )
    return F.coalesce(
        F.get(table, (level + F.lit(1)).cast("int")),
        F.lit(float(_UNOBSERVED_WEIGHT)),
    )


def fs_match_weight_expr(
    mk: Any, em: Any, lhs: str, rhs: str, *,
    scorer_udf: str | None = None,
    transform_udf: str | None = None,
) -> Any:
    """Total FS match weight in bits for a pair, as a Spark expression."""
    from goldenmatch.core.probabilistic import fs_missing_mode

    missing_mode = fs_missing_mode(mk)
    total = None
    for f in mk.fields:
        sim, observed = _field_similarity_and_observed(
            f, lhs, rhs, scorer_udf=scorer_udf, transform_udf=transform_udf
        )
        level = fs_level_expr(f, sim, observed, missing_mode=missing_mode)
        w = _weight_lookup_expr(level, em.match_weights[f.resolved_field])
        total = w if total is None else (total + w)
    return total


def fs_posterior_expr(total_weight: Any, prior_w: float) -> Any:
    """``1 / (1 + 2^-(prior + weight))`` with `posterior_from_weight`'s clamp.

    The clamp is not cosmetic: without it, `2^-logodds` overflows to inf for
    strongly-negative evidence and the row becomes NaN rather than 0.0 -- and a
    NaN compares false against every threshold, so the pair would vanish instead
    of being rejected.
    """
    from pyspark.sql import functions as F

    logodds = F.lit(float(prior_w)) + total_weight
    return (
        F.when(logodds > F.lit(_LOGODDS_CLAMP), F.lit(1.0))
        .when(logodds < F.lit(-_LOGODDS_CLAMP), F.lit(0.0))
        .otherwise(F.lit(1.0) / (F.lit(1.0) + F.pow(F.lit(2.0), -logodds)))
    )


def fs_score_expr(
    mk: Any, em: Any, lhs: str, rhs: str, *,
    scorer_udf: str | None = None,
    transform_udf: str | None = None,
) -> Any:
    """P(match) for a pair under a trained FS model, as a Spark expression.

    With ``scorer_udf`` set, every part of this runs in the executor JVM.
    """
    from goldenmatch.core.probabilistic import prior_weight

    _validate_fs_spark_supported(mk, em)
    prior_w = prior_weight(em.proportion_matched)
    logger.info(
        "Spark FS: matchkey=%s fields=%d prior_weight=%.4f bits "
        "(proportion_matched=%.6g)",
        mk.name, len(mk.fields), prior_w, em.proportion_matched,
    )
    return fs_posterior_expr(
        fs_match_weight_expr(
            mk, em, lhs, rhs, scorer_udf=scorer_udf, transform_udf=transform_udf
        ),
        prior_w,
    )


# ── model resolution ─────────────────────────────────────────────────

def resolve_fs_model(mk: Any, *, model_path: str | None = None) -> Any:
    """Load the trained model for a probabilistic matchkey.

    Distributed scoring needs weights that already exist: EM trains from a
    driver-side sample of blocked pairs, so there is nothing for the cluster to
    do here. Refuse clearly when no model is available rather than training one
    implicitly on data the caller expected to stay remote.
    """
    import json
    from pathlib import Path

    from goldenmatch.core.probabilistic import EMResult

    path = model_path or getattr(mk, "model_path", None)
    if not path:
        raise ValueError(
            f"matchkey {mk.name!r} is probabilistic, so the Spark tier needs a "
            f"TRAINED model: set matchkey.model_path (or pass model_path=). "
            f"EM trains from a driver-side sample of blocked pairs -- run it on "
            f"the one-box path, or import a Splink model with `import-splink`, "
            f"then point this at the saved file."
        )
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(
            f"FS model for matchkey {mk.name!r} not found at {path!r}. The "
            f"Spark tier does not train on demand -- it would have to pull the "
            f"sample back through the driver."
        )
    em = EMResult.from_dict(json.loads(p.read_text(encoding="utf-8")))
    logger.info("Spark FS: loaded model for %s from %s", mk.name, path)
    return em
