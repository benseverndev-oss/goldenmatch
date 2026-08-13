"""P4: run the Spark tier from a ``GoldenMatchConfig`` instead of scalars.

``run_sail_pipeline`` takes one ``block_col``, one ``value_col``, one scorer and
one threshold. A real config carries **many** blocking passes, **many**
matchkeys, each with **many** weighted fields, and per-field survivorship rules.
This module is the config-driven entry: ``run_config_pipeline(df, config)``.

Design notes worth keeping in view:

**Parity comes from reuse, not re-derivation.** Block keys apply
``utils.transforms.apply_transforms`` (the same call the one-box blocker makes)
and join with ``"||"`` under any-null -> null, matching
``arrow_derive.block_key``. Survivorship calls ``core.golden.merge_field``. The
one place a formula is restated is the weighted combine, because it must be a
Spark expression to stay vectorized -- so ``weighted_pair_score`` below is a
pure-Python statement of ``core.scorer.score_pair``'s math, unit-tested against
it directly, and the Spark expression mirrors it under a parity test.

**Nulls are load-bearing here.** ``core.scorer.score_field`` returns ``None``
when either value is missing and ``score_pair`` then drops that field from BOTH
the numerator and the denominator -- the denominator is per-pair, not the
matchkey's total weight. Getting that wrong does not shift a score slightly; it
silently reweights every pair with a missing field. See ``_field_score_parts``.

Out of scope for P4 and gated LOUDLY (never silently ignored) by
``_validate_spark_config_supported``: probabilistic matchkeys (P5 -- Fellegi-
Sunter does not exist in this tier at all), negative evidence, guards, rerank,
LLM, domain extraction, and every non-static blocking strategy.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Blocking strategies the config pipeline can execute. Everything else in
# BlockingConfig.strategy's Literal is a candidate-generation algorithm with no
# Spark expression here; running one silently as `static` would change recall.
#
# `multi_pass` is here because it is what AUTO-CONFIG EMITS, and because a union
# of independent key sets is exactly what `generate_candidates` already does.
# The catch is WHERE the key sets live: a multi_pass config carries them in
# `blocking.passes`, and its `blocking.keys` holds only one of them. Reading
# `keys` on such a config generates candidates from one pass out of N and
# silently drops the rest -- observed on a real auto-config output with 1 key
# and 5 passes. `_blocking_passes` is the single place that resolves this.
_SUPPORTED_BLOCKING = ("static", "multi_pass")

# Matchkey types the tier executes. "probabilistic" arrived in P5 and needs a
# TRAINED model (see spark/probabilistic.py: scoring distributes, EM does not).
_SUPPORTED_MATCHKEY_TYPES = ("weighted", "exact", "probabilistic")

_BLOCK_KEY_SEP = "||"

# blocker.py's sentinel guard, verbatim: these stringified-missing values are
# dropped from block keys. "" is NOT a sentinel -- it is a real value (#390).
_KEY_SENTINELS = ("nan", "null", "none")


# ── feature gate ─────────────────────────────────────────────────────

def _validate_spark_config_supported(config: Any) -> None:
    """Raise on config the Spark tier cannot execute FAITHFULLY.

    The scale-mode posture (R3), mirroring
    ``datafusion_spine._validate_scale_mode_supported``: a customer must never
    receive a silently-degraded result. Every branch here is a feature that
    would otherwise be ignored rather than executed.
    """
    from goldenmatch.spark.scorers import _SUPPORTED as _SUPPORTED_SCORERS

    if getattr(config, "llm_boost", False) or getattr(config, "llm_auto", False):
        raise NotImplementedError(
            "The Spark tier does not support LLM boosting (llm_boost / "
            "llm_auto). LLM scoring is a per-pair network call; it does not "
            "belong in a distributed scan. Run it on the one-box pipeline."
        )
    llm_scorer = getattr(config, "llm_scorer", None)
    if llm_scorer is not None and getattr(llm_scorer, "enabled", False):
        raise NotImplementedError(
            "The Spark tier does not support the LLM scorer "
            "(llm_scorer.enabled=True)."
        )
    domain = getattr(config, "domain", None)
    if domain is not None and getattr(domain, "enabled", False):
        raise NotImplementedError(
            "The Spark tier does not support domain feature extraction "
            "(domain.enabled=True)."
        )
    semantic = getattr(config, "semantic_blocking", None)
    if semantic is not None and getattr(semantic, "enabled", False):
        raise NotImplementedError(
            "The Spark tier does not support semantic blocking "
            "(semantic_blocking.enabled=True); its ANN keys are built in "
            "process on the driver."
        )

    blocking = getattr(config, "blocking", None)
    if blocking is None or not (
        getattr(blocking, "keys", None) or getattr(blocking, "passes", None)
    ):
        raise ValueError(
            "The Spark tier requires explicit blocking (config.blocking.keys "
            "or, for multi_pass, config.blocking.passes). Without candidate "
            "generation the tier would self-join every record against every "
            "other -- refused rather than attempted."
        )
    strategy = getattr(blocking, "strategy", "static")
    if strategy not in _SUPPORTED_BLOCKING:
        raise NotImplementedError(
            f"The Spark tier supports blocking strategies "
            f"{_SUPPORTED_BLOCKING}; got {strategy!r}. Running a "
            f"{strategy!r} config as 'static' would silently change which "
            f"pairs are generated, so it is refused."
        )
    if getattr(blocking, "auto_suggest", False):
        raise NotImplementedError(
            "The Spark tier does not support blocking auto_suggest "
            "(keys are discovered on the driver from a local sample)."
        )

    matchkeys = config.get_matchkeys()
    if not matchkeys:
        raise ValueError("config has no matchkeys; nothing to score.")

    for mk in matchkeys:
        mk_type = getattr(mk, "type", None) or getattr(mk, "comparison", None)
        if mk_type not in _SUPPORTED_MATCHKEY_TYPES:
            raise NotImplementedError(
                f"The Spark tier supports matchkey types "
                f"{_SUPPORTED_MATCHKEY_TYPES}; matchkey {mk.name!r} has "
                f"type={mk_type!r}."
            )
        if getattr(mk, "negative_evidence", None):
            raise NotImplementedError(
                f"The Spark tier does not support negative evidence "
                f"(matchkey {mk.name!r})."
            )
        if getattr(mk, "guard", None):
            raise NotImplementedError(
                f"The Spark tier does not support guarded matchkeys "
                f"(matchkey {mk.name!r} sets guard=...)."
            )
        if getattr(mk, "rerank", False):
            raise NotImplementedError(
                f"The Spark tier does not support cross-encoder rerank "
                f"(matchkey {mk.name!r})."
            )
        if getattr(mk, "auto_threshold", False):
            raise NotImplementedError(
                f"The Spark tier does not support auto_threshold (matchkey "
                f"{mk.name!r}); it needs the full score distribution on the "
                f"driver. Set an explicit threshold."
            )
        if mk_type == "probabilistic":
            # Field-shape and model-compatibility checks live in
            # spark/probabilistic.py, which needs the trained model to make
            # them; here only the scorer-presence invariant is checkable.
            for f in mk.fields:
                if f.scorer is None:
                    raise ValueError(
                        f"probabilistic matchkey {mk.name!r}: every field needs "
                        f"a scorer; {f.resolved_field!r} has none."
                    )
        if mk_type == "weighted":
            if mk.threshold is None:
                raise ValueError(
                    f"weighted matchkey {mk.name!r} requires a threshold."
                )
            for f in mk.fields:
                if f.scorer is None or f.weight is None:
                    raise ValueError(
                        f"weighted matchkey {mk.name!r}: every field needs "
                        f"scorer and weight; {f.resolved_field!r} is missing "
                        f"one."
                    )
                if f.scorer not in _SUPPORTED_SCORERS and f.scorer != "exact":
                    raise NotImplementedError(
                        f"The Spark tier supports scorers "
                        f"{(*_SUPPORTED_SCORERS, 'exact')}; matchkey "
                        f"{mk.name!r} field {f.resolved_field!r} uses "
                        f"{f.scorer!r}."
                    )


# ── the weighted combine (pure reference; the Spark expression mirrors it) ──

def weighted_pair_score(
    field_scores: list[float | None], weights: list[float]
) -> float:
    """``core.scorer.score_pair``'s aggregation, stated over already-computed
    per-field scores.

    ``None`` means "this field could not be scored" (either side missing) and
    is excluded from the numerator AND the denominator -- so the denominator is
    the weight of the fields actually compared on THIS pair, not the matchkey's
    total weight. Returns 0.0 when nothing was comparable.

    This exists as a pure function so it can be unit-tested against
    ``score_pair`` without Spark; ``_matchkey_score_expr`` builds the same
    arithmetic as a Spark expression, and a parity test binds the two.
    """
    num = 0.0
    den = 0.0
    for s, w in zip(field_scores, weights):
        if s is not None:
            num += s * w
            den += w
    return num / den if den else 0.0


# ── block keys ───────────────────────────────────────────────────────

def _block_key_column(
    key_config: Any, transform_udf: str | None = None
) -> tuple[Any, list[str]]:
    """``(column_expression, fields)`` for one blocking key.

    Mirrors ``arrow_derive.block_key``: each field takes its own transform chain
    (the per-field slot when set, else the key-level chain), then the parts join
    with ``"||"`` where ANY null makes the whole key null.

    Composed from SINGLE-column UDFs plus Spark expressions rather than one
    variadic UDF: pandas UDFs resolve their eval type from the function
    signature, and a ``*args`` form is not part of the documented contract.
    ``concat_ws`` cannot express this on its own either -- it SKIPS nulls, so
    ``("a", null)`` and ``("a", "")`` would collide into the same key. The
    any-null guard is therefore explicit.
    """
    from pyspark.sql import functions as F

    fields = list(key_config.fields)
    shared = list(getattr(key_config, "transforms", None) or [])
    per_field = getattr(key_config, "field_transforms", None) or {}

    parts, any_null = [], None
    for f in fields:
        chain = list(per_field.get(f, shared))
        raw = F.col(f)
        part = (
            _transformed(raw, chain, transform_udf=transform_udf)
            if chain
            else raw.cast("string")
        )
        parts.append(part)
        # Null-ness is judged AFTER the transform: a chain may map a real value
        # to null (null_if_empty), and the one-box drops that key too.
        is_null = part.isNull()
        any_null = is_null if any_null is None else (any_null | is_null)

    joined = parts[0] if len(parts) == 1 else F.concat_ws(_BLOCK_KEY_SEP, *parts)
    return F.when(any_null, F.lit(None).cast("string")).otherwise(joined), fields


def _valid_key(col: Any) -> Any:
    """``frame.filter_valid_key``'s predicate: non-null and not a stringified
    missing sentinel. Empty string is KEPT -- it is a real value (#390)."""
    from pyspark.sql import functions as F

    return col.isNotNull() & (~F.lower(F.trim(col)).isin(list(_KEY_SENTINELS)))


def blocking_passes(config: Any) -> list[Any]:
    """Every key set whose blocks must be unioned into the candidate set.

    A ``static`` config expresses its passes as ``blocking.keys``. A
    ``multi_pass`` config expresses them as ``blocking.passes`` and leaves
    ``keys`` holding a single one -- so reading ``keys`` on a multi_pass config
    silently generates candidates from ONE pass out of N. Auto-config emits
    exactly that shape (measured: 1 key, 5 passes), which is a recall loss that
    looks like a clean run.

    ``passes`` wins when present; ``keys`` is the fallback, because the schema
    permits a multi_pass config that carries only ``keys``.
    """
    blocking = config.blocking
    if getattr(blocking, "strategy", "static") == "multi_pass":
        passes = list(getattr(blocking, "passes", None) or [])
        if passes:
            logger.info(
                "Spark tier: multi_pass blocking over %d pass(es)", len(passes)
            )
            return passes
    return list(blocking.keys or [])


def generate_candidates(
    source_df: Any, config: Any, *, id_col: str, transform_udf: str | None = None
) -> Any:
    """Candidate pairs ``(a, b)`` with ``a < b``, unioned over every blocking
    pass and de-duplicated.

    Multiple passes are a UNION of self-joins, exactly as the one-box blocker
    unions its per-key blocks: a pair generated by two passes is ONE candidate,
    not two, so `distinct()` here is semantic and not an optimization.

    ``transform_udf`` routes the blocking keys' normalization through the jar's
    ``golden_transform`` instead of a Python worker. It matters more here than
    anywhere else in the tier: a value normalized differently lands in a
    DIFFERENT BLOCK and is never compared to its own duplicate, so the failure
    is a missing match rather than a wrong one. Nothing downstream can detect
    it. Hence the kernel refuses any chain it cannot run identically instead of
    falling back.
    """
    from pyspark.sql import functions as F

    per_pass = []
    for i, key_config in enumerate(blocking_passes(config)):
        key_col, _fields = _block_key_column(key_config, transform_udf)
        keyed = source_df.withColumn("__block_key__", key_col)
        keyed = keyed.where(_valid_key(F.col("__block_key__")))
        a = keyed.alias("a")
        b = keyed.alias("b")
        pairs = a.join(
            b,
            (F.col("a.__block_key__") == F.col("b.__block_key__"))
            & (F.col(f"a.{id_col}") < F.col(f"b.{id_col}")),
        ).select(
            F.col(f"a.{id_col}").alias("a"),
            F.col(f"b.{id_col}").alias("b"),
        )
        logger.debug("Spark tier: blocking pass %d on %s", i, key_config.fields)
        per_pass.append(pairs)

    out = per_pass[0]
    for nxt in per_pass[1:]:
        out = out.unionByName(nxt)
    return out.distinct()


# ── scoring ──────────────────────────────────────────────────────────

def _field_score_parts(field: Any, a_prefix: str, b_prefix: str) -> tuple[Any, Any]:
    """``(weighted_score_contribution, weight_contribution)`` for one field.

    Both are zero when the field is not comparable on this pair, which is what
    implements ``score_pair``'s exclusion. Note that comparability is decided
    from the RAW columns (``isNotNull`` on both sides) rather than from the
    UDF's output: the scorer kernel treats a missing value as ``""`` and
    therefore scores null-vs-null as a PERFECT 1.0, which would merge two
    records whose only evidence is that both are missing the field.
    """
    from pyspark.sql import functions as F

    from goldenmatch.spark.scorers import make_scorer_udf

    col = field.resolved_field
    a_col = F.col(f"{a_prefix}.{col}")
    b_col = F.col(f"{b_prefix}.{col}")
    weight = float(field.weight)

    chain = list(getattr(field, "transforms", None) or [])
    if chain:
        a_val, b_val = _transformed(a_col, chain), _transformed(b_col, chain)
    else:
        a_val, b_val = a_col.cast("string"), b_col.cast("string")

    if field.scorer == "exact":
        raw = F.when(a_val == b_val, F.lit(1.0)).otherwise(F.lit(0.0))
    else:
        raw = make_scorer_udf(field.scorer)(a_val, b_val)

    comparable = a_col.isNotNull() & b_col.isNotNull()
    return (
        F.when(comparable, raw * F.lit(weight)).otherwise(F.lit(0.0)),
        F.when(comparable, F.lit(weight)).otherwise(F.lit(0.0)),
    )


def _transformed(
    col: Any, chain: list[str], transform_udf: str | None = None
) -> Any:
    """Apply a one-box transform chain to a column via ``apply_transforms``.

    ``transform_udf`` is the SQL name of the jar's ``golden_transform``. When
    given, the chain runs ENTIRELY IN THE EXECUTOR JVM over the same pyo3-free
    ``transforms-core`` Python uses, so no Python worker is involved and no
    executor virtualenv is needed.

    The chain is validated on the DRIVER first. ``bloom_filter`` and plugin
    transforms are Python-only by design, and a chain containing one is
    refused here -- at plan time, naming the transform -- rather than
    returning nulls from every row of an already-distributed job.
    """
    if transform_udf is not None:
        from pyspark.sql import functions as F

        from goldenmatch.spark.jvm import unsupported_transforms

        bad = unsupported_transforms(chain)
        if bad:
            raise ValueError(
                f"the JVM transform path cannot run {bad}. `bloom_filter` is "
                f"HMAC-keyed PPRL and plugin transforms are arbitrary Python; "
                f"both are Python-only by design. Drop them from the chain, "
                f"or omit transform_udf and ship a Python environment."
            )
        return F.call_udf(
            transform_udf, col.cast("string"), F.lit(",".join(chain))
        )

    from goldenmatch.spark._arrow import arrow_udf, from_pylist, to_pylist

    @arrow_udf("string")
    def _udf(c):
        from goldenmatch.utils.transforms import apply_transforms

        return from_pylist(
            [
                None if v is None else apply_transforms(str(v), chain)
                for v in to_pylist(c)
            ],
            "string",
        )

    return _udf(col.cast("string"))


def _matchkey_score_expr(
    mk: Any, a_prefix: str, b_prefix: str, *, fs_model: Any = None
) -> Any:
    """The Spark twin of ``weighted_pair_score`` for one matchkey."""
    from pyspark.sql import functions as F

    mk_type = getattr(mk, "type", None) or getattr(mk, "comparison", None)
    if mk_type == "probabilistic":
        # P5. The score is P(match) from the trained model, not a weighted
        # similarity average -- a different quantity on a different scale, which
        # is why the threshold below is `link_threshold` and not `threshold`.
        from goldenmatch.spark.probabilistic import fs_score_expr

        return fs_score_expr(mk, fs_model, a_prefix, b_prefix)
    if mk_type == "exact":
        # An exact matchkey is agreement on every field. Null is not agreement:
        # `a IS NULL AND b IS NULL` must not read as a match, which is the same
        # trap as the weighted path's null-vs-null 1.0.
        cond = None
        for f in mk.fields:
            col = f.resolved_field
            a_col, b_col = F.col(f"{a_prefix}.{col}"), F.col(f"{b_prefix}.{col}")
            this = a_col.isNotNull() & b_col.isNotNull() & (a_col == b_col)
            cond = this if cond is None else (cond & this)
        return F.when(cond, F.lit(1.0)).otherwise(F.lit(0.0))

    nums, dens = [], []
    for f in mk.fields:
        n, d = _field_score_parts(f, a_prefix, b_prefix)
        nums.append(n)
        dens.append(d)

    num = nums[0]
    for n in nums[1:]:
        num = num + n
    den = dens[0]
    for d in dens[1:]:
        den = den + d
    # den == 0 means no field was comparable -> score_pair returns 0.0.
    return F.when(den > F.lit(0.0), num / den).otherwise(F.lit(0.0))


def _matchkey_threshold(mk: Any, em: Any) -> float:
    """The accept cutoff for one matchkey, on ITS OWN scale.

    A probabilistic matchkey scores P(match) and cuts at `link_threshold`; a
    weighted one scores a similarity average and cuts at `threshold`. Reusing
    `threshold` for both would silently compare a probability against a
    similarity cutoff -- numbers in the same range, meaning different things.

    `resolve_thresholds` is the one-box authority for the probabilistic default
    (including a model's calibrated cutoff when EM picked one), so it is called
    rather than re-deriving the precedence here.
    """
    mk_type = getattr(mk, "type", None) or getattr(mk, "comparison", None)
    if mk_type == "exact":
        return 1.0
    if mk_type == "probabilistic":
        from goldenmatch.core.probabilistic import resolve_thresholds

        # ORDER MATTERS AND IS (link, review), NOT (review, link). Taking the
        # second element would cut at the REVIEW threshold -- lower than link by
        # construction -- and silently auto-link every pair the one-box would
        # have sent to a human. Pinned by
        # tests/test_spark_fs_unit.py::test_threshold_is_the_link_not_the_review.
        link, _review = resolve_thresholds(mk, em)
        return float(link)
    return float(mk.threshold)


def score_candidates(
    candidates: Any,
    source_df: Any,
    config: Any,
    *,
    id_col: str,
    fs_models: dict[str, Any] | None = None,
    scorer_udf: str | None = None,
    transform_udf: str | None = None,
    batch_size: int = 10_000,
    scorer_shape: str = "row",
) -> Any:
    """Score candidate pairs under every matchkey; return ``(a, b, score)``.

    A pair accepted by ANY matchkey is a match (matchkeys are alternatives, not
    conjuncts -- the one-box semantics), so this unions each matchkey's accepted
    set and keeps ``max(score)`` per canonical pair, matching the tier's
    existing MAX dedup contract.
    """
    from pyspark.sql import functions as F

    # The source aliases must NOT be "a"/"b": the candidate frame's own columns
    # are named `a` and `b`, so `F.col("a.first")` would be ambiguous between
    # "alias a, column first" and a field of a struct-ish `a`. Distinct sentinel
    # aliases remove the question rather than relying on resolution order.
    lhs, rhs = "__lhs__", "__rhs__"
    a = source_df.alias(lhs)
    b = source_df.alias(rhs)
    joined = (
        candidates.alias("__cand__")
        .join(a, F.col(f"{lhs}.{id_col}") == F.col("__cand__.a"))
        .join(b, F.col(f"{rhs}.{id_col}") == F.col("__cand__.b"))
    )

    if scorer_udf is not None:
        if scorer_shape not in ("row", "batch"):
            raise ValueError(
                f"scorer_shape must be 'row' or 'batch'; got {scorer_shape!r}"
            )
        if scorer_shape == "row":
            from goldenmatch.spark.jvm import ROW_UDF_NAME

            return _score_candidates_jvm_rowwise(
                joined, config, lhs=lhs, rhs=rhs, scorer_udf=ROW_UDF_NAME,
                transform_udf=transform_udf, fs_models=fs_models,
            )
        return _score_candidates_jvm(
            joined, config, lhs=lhs, rhs=rhs, scorer_udf=scorer_udf,
            transform_udf=transform_udf, fs_models=fs_models,
            batch_size=batch_size,
        )

    models = fs_models or {}
    per_mk = []
    for mk in config.get_matchkeys():
        em = models.get(mk.name)
        scored = joined.select(
            F.col("__cand__.a").alias("a"),
            F.col("__cand__.b").alias("b"),
            _matchkey_score_expr(mk, lhs, rhs, fs_model=em).alias("score"),
        ).where(F.col("score") >= F.lit(_matchkey_threshold(mk, em)))
        per_mk.append(scored)

    out = per_mk[0]
    for nxt in per_mk[1:]:
        out = out.unionByName(nxt)
    return out.groupBy("a", "b").agg(F.max("score").alias("score"))


# ── scoring, in the JVM ──────────────────────────────────────────────
#
# The gap this closes, stated precisely, because the obvious reading of it is
# wrong.
#
# It looks like a shape mismatch: the config pipeline scores "per field, row
# shaped" and the jar's scorer is "batched, array shaped". But
# `make_scorer_udf` is an ARROW udf -- Spark hands it a whole batch and it
# returns a whole batch of scores. Its real signature is already
# ``(a[], b[]) -> double[]``, which is exactly `golden_score_batch`. The two
# compute the same function at the same granularity.
#
# The actual difference is who forms the batch. Spark forms it IMPLICITLY for an
# arrow_udf, invisibly in the plan. A Java UDF registered over Spark Connect only
# ever sees one row, so its batch must be formed EXPLICITLY -- an `array<string>`
# column, which only `collect_list` can build.
#
# That is why a naive port is a disaster rather than a small regression. Calling
# the jar per row means ~10,000 JNI crossings where there is currently ONE
# Python call, and it would look like "native scoring is slow" while measuring
# nothing but call overhead.
#
# So the batch is built once, explicitly, and every scorer call rides it:
#
#   joined -> groupBy(batch_key).agg(collect_list(struct(...)))   ONE list
#          -> one UDF call per distinct SCORER (not per field, not per row)
#          -> reshape n*k scores back to n rows of k
#          -> arrays_zip + explode                                ONE generator
#
# One call per scorer rather than per field because the UDF takes a single
# scorer id for the whole array: a matchkey mixing jaro_winkler and levenshtein
# needs two calls, not one, and not one per field.
#
# The alignment discipline is `batched.py`'s, for its reasons: exactly one
# `collect_list` (several are separate aggregates with no shared order
# guarantee, and they agree often enough to pass a test and skew under a
# different plan), and `arrays_zip` before a single `explode` (two explodes are
# two independent generators pairing nothing). Neither mistake crashes; both
# silently attach pair i's score to pair j.

#: Column holding the collected pairs of one scoring batch.
_SROWS = "__score_rows__"


# The two higher-order lambdas this path needs, built by FACTORIES rather than
# written inline with default arguments.
#
# PySpark decides what a higher-order lambda MEANS from its parameter count:
# one parameter is ``(element)``, two is ``(element, index)``. So the natural
# way to capture a loop variable --
#
#     F.transform(rows, lambda r, g=group: ...)
#
# -- is read as a two-argument lambda, and ``g`` receives the array INDEX. The
# captured value is silently discarded and every row is built from an integer.
# It does not raise. It MISALIGNS, which is the one failure this construction
# exists to prevent. (Four parameters does raise, which is the lucky case.)
#
# Module level rather than nested so the arity contract is testable without a
# Spark session -- see test_spark_config_pipeline_unit.py.

def _pick_fields(group: list[dict], prefix: str):
    """``r -> array(r.<prefix><i>, ...)`` over one scorer's slots, in order."""
    def f(r):
        from pyspark.sql import functions as F

        return F.array(*[r[f"{prefix}{s['i']}"] for s in group])
    return f


def _reshape_scores(raw_name: str, k: int):
    """``(r, i) -> raw[i*k : i*k+k]`` -- one row's k scores out of the flat
    ``n*k`` array the kernel returned.

    ``slice`` rather than ``array(raw[i*k], raw[i*k+1], ...)``: both are
    correct, but indexing mentions the UDF call k times inside the lambda and
    nothing guarantees Spark folds those back into one invocation. That would be
    k native calls per batch instead of one -- exactly the overhead the batching
    exists to remove. ``slice`` names it once. (1-indexed.)
    """
    def f(r, i):
        from pyspark.sql import functions as F

        return F.slice(F.col(raw_name), i * k + 1, k)
    return f


def _weighted_scorer_slots(config: Any) -> tuple[list[dict], dict[tuple, int]]:
    """Every distinct ``(column, transform chain, scorer)`` a weighted matchkey
    needs, and the map from a field to its slot.

    Deduplicated because two matchkeys naming the same field with the same chain
    and scorer are the same comparison, and scoring it twice would double both
    the JNI work and the width of the collected struct.

    Keyed on all THREE parts: the same column can appear under a different
    chain (a substring key in one matchkey, the raw value in another) or a
    different scorer, and those are genuinely different comparisons. Keying on
    the column alone would silently score one of them with the other's settings.
    """
    slots: list[dict] = []
    index: dict[tuple, int] = {}
    for mk in config.get_matchkeys():
        mk_type = getattr(mk, "type", None) or getattr(mk, "comparison", None)
        if mk_type != "weighted":
            continue
        for f in mk.fields:
            # `exact`-scored fields get a slot too, even though they need no
            # kernel. Their agreement is computed in pure Spark SQL BEFORE the
            # batch and carried in the struct, because the alternative is
            # recomputing it after `collect_list` -- where the raw columns no
            # longer exist. Skipping them here was the first shape of this
            # function and it made a weighted matchkey with one exact field
            # unrepresentable.
            key = _slot_key(f)
            if key not in index:
                index[key] = len(slots)
                slots.append({
                    "col": key[0], "chain": list(key[1]), "scorer": key[2],
                    "i": len(slots),
                })
    return slots, index


def _slot_key(field: Any) -> tuple:
    """A field's slot identity: ``(column, chain, scorer)``.

    One function so the build side and the read side cannot compute it
    differently -- a mismatch would raise a KeyError at plan time rather than
    misalign silently, but only because they are the same call.
    """
    return (
        field.resolved_field,
        tuple(getattr(field, "transforms", None) or []),
        field.scorer,
    )


def _score_candidates_jvm(
    joined: Any,
    config: Any,
    *,
    lhs: str,
    rhs: str,
    scorer_udf: str,
    transform_udf: str | None,
    fs_models: dict[str, Any] | None,
    batch_size: int,
) -> Any:
    """``score_candidates``'s body with every scorer call in the executor JVM.

    Returns ``(a, b, score)``, identical in shape and semantics to the Python
    path -- which is what lets the parity gate compare them directly.
    """
    from pyspark.sql import functions as F

    from goldenmatch.spark.batched import batch_key
    from goldenmatch.spark.jvm import scorer_id

    matchkeys = list(config.get_matchkeys())
    # Refused rather than silently routed to the Python path: a caller who
    # passed `scorer_udf` and got no error would still need an executor
    # virtualenv, and would only find out from a ModuleNotFoundError mid-job.
    _refuse_probabilistic(matchkeys)

    slots, index = _weighted_scorer_slots(config)

    # ── the ONE struct, and the ONE collect_list ──────────────────────
    parts = [F.col("__cand__.a").alias("a"), F.col("__cand__.b").alias("b")]
    for s in slots:
        a_col = F.col(f"{lhs}.{s['col']}")
        b_col = F.col(f"{rhs}.{s['col']}")
        if s["chain"]:
            a_val = _transformed(a_col, s["chain"], transform_udf=transform_udf)
            b_val = _transformed(b_col, s["chain"], transform_udf=transform_udf)
        else:
            a_val, b_val = a_col.cast("string"), b_col.cast("string")
        if s["scorer"] == "exact":
            # No kernel: the agreement IS the score, computed here in SQL.
            parts.append(
                F.when(a_val == b_val, F.lit(1.0)).otherwise(F.lit(0.0))
                .alias(f"r{s['i']}")
            )
        else:
            parts += [a_val.alias(f"x{s['i']}"), b_val.alias(f"y{s['i']}")]
        # Comparability is decided from the RAW columns, exactly as the Python
        # path does, and it must be carried INTO the batch: after
        # `collect_list` the raw columns are gone, and re-deriving it from the
        # transformed value would be wrong anyway. The kernel maps a missing
        # value to "" and therefore scores null-vs-null as a PERFECT 1.0, which
        # would merge two records whose only shared evidence is that both are
        # missing the field.
        parts.append((a_col.isNotNull() & b_col.isNotNull()).alias(f"c{s['i']}"))
    # Exact matchkeys need no kernel, so their score is computed BEFORE the
    # batch and carried along rather than round-tripping through it.
    for m, mk in enumerate(matchkeys):
        mk_type = getattr(mk, "type", None) or getattr(mk, "comparison", None)
        if mk_type == "exact":
            parts.append(_matchkey_score_expr(mk, lhs, rhs).alias(f"e{m}"))

    grouped = joined.groupBy(
        batch_key("partition", batch_size).alias("__batch__")
    ).agg(F.collect_list(F.struct(*parts)).alias(_SROWS))

    # ── one UDF call per distinct scorer ──────────────────────────────
    by_scorer: dict[str, list[dict]] = {}
    for s in slots:
        if s["scorer"] == "exact":
            continue  # already scored in SQL, carried as r{i}
        by_scorer.setdefault(s["scorer"], []).append(s)

    raw_cols = []
    for si, (name, group) in enumerate(sorted(by_scorer.items())):
        # `flatten(transform(rows, r -> array(r.x_a, r.x_b, ...)))` lays the
        # batch out as pair-major: row 0's k values, then row 1's, and so on.
        # The kernel sees one flat array and the reshape below relies on that
        # ordering, so both sides are built from the SAME slot list in the same
        # order rather than from two iterations that could drift.
        xs = F.flatten(F.transform(F.col(_SROWS), _pick_fields(group, "x")))
        ys = F.flatten(F.transform(F.col(_SROWS), _pick_fields(group, "y")))
        raw_cols.append(
            F.call_udf(scorer_udf, F.lit(int(scorer_id(name))), xs, ys)
            .alias(f"__raw{si}")
        )
    with_scores = grouped.select(F.col(_SROWS), *raw_cols)

    # ── reshape n*k back to n rows of k ───────────────────────────────
    reshaped = []
    for si, (_name, group) in enumerate(sorted(by_scorer.items())):
        k = len(group)
        # `slice` rather than indexing k times. Both are correct, but building
        # `array(raw[i*k], raw[i*k+1], ...)` mentions the UDF call k times
        # inside the lambda, and nothing guarantees Spark folds those back into
        # one invocation -- k native calls per batch instead of one, which is
        # precisely the overhead this whole construction exists to avoid.
        # `slice` names it once. (1-indexed.)
        reshaped.append(
            F.transform(F.col(_SROWS), _reshape_scores(f"__raw{si}", k)).alias(f"__sc{si}")
        )
    shaped = with_scores.select(F.col(_SROWS), *reshaped)

    # arrays_zip pairs positionally BEFORE the explode, so ONE generator walks
    # already-paired structs.
    zipped = shaped.select(
        F.explode(
            F.arrays_zip(F.col(_SROWS), *[F.col(f"__sc{i}") for i in range(len(by_scorer))])
        ).alias("__z__")
    )

    # ── the weighted combine, per matchkey ────────────────────────────
    pos: dict[int, tuple[int, int]] = {}
    for si, (_name, group) in enumerate(sorted(by_scorer.items())):
        for p, s in enumerate(group):
            pos[s["i"]] = (si, p)

    row = f"__z__.{_SROWS}"
    accepted = []
    for m, mk in enumerate(matchkeys):
        mk_type = getattr(mk, "type", None) or getattr(mk, "comparison", None)
        if mk_type == "exact":
            score = F.col(f"{row}.e{m}")
        else:
            nums, dens = [], []
            for f in mk.fields:
                weight = float(f.weight)
                idx = index[_slot_key(f)]
                if f.scorer == "exact":
                    raw = F.col(f"{row}.r{idx}")
                else:
                    si, p = pos[idx]
                    raw = F.col(f"__z__.__sc{si}")[p]
                comparable = F.col(f"{row}.c{idx}")
                nums.append(F.when(comparable, raw * F.lit(weight)).otherwise(F.lit(0.0)))
                dens.append(F.when(comparable, F.lit(weight)).otherwise(F.lit(0.0)))
            num, den = nums[0], dens[0]
            for n in nums[1:]:
                num = num + n
            for d in dens[1:]:
                den = den + d
            # den == 0 means no field was comparable -> score_pair returns 0.0.
            score = F.when(den > F.lit(0.0), num / den).otherwise(F.lit(0.0))

        em = (fs_models or {}).get(mk.name)
        # NULL where this matchkey rejects the pair. `greatest` skips nulls, so
        # a pair no matchkey accepted comes out null and is dropped.
        accepted.append(
            F.when(score >= F.lit(_matchkey_threshold(mk, em)), score)
        )

    # ONE pass, not a union of one select per matchkey.
    #
    # The Python path unions N per-matchkey frames and takes `max(score)` per
    # pair. Doing that here would re-derive `zipped` once per matchkey -- and
    # `zipped` is where the kernel calls live, so N matchkeys would mean N times
    # the native work and N times the shuffle, for a result that is identical.
    #
    # `greatest` over the per-matchkey accepted scores IS that max: each
    # matchkey contributes at most one score per pair, so the maximum over the
    # union equals the greatest of the accepted values, and a pair with no
    # accepted matchkey has all-null inputs and drops out. It also removes the
    # final groupBy entirely -- there is nothing left to aggregate.
    best = F.greatest(*accepted) if len(accepted) > 1 else accepted[0]
    return zipped.select(
        F.col(f"{row}.a").alias("a"),
        F.col(f"{row}.b").alias("b"),
        best.alias("score"),
    ).where(F.col("score").isNotNull())


def _refuse_probabilistic(matchkeys: list) -> None:
    """Both JVM shapes refuse a probabilistic matchkey, for one reason.

    Shared rather than duplicated: two copies of a refusal drift, and the shape
    a caller picked is not a reason to accept work the JVM cannot do.
    """
    for mk in matchkeys:
        mk_type = getattr(mk, "type", None) or getattr(mk, "comparison", None)
        if mk_type == "probabilistic":
            raise NotImplementedError(
                f"matchkey {mk.name!r} is probabilistic, and the JVM scoring "
                f"path does not run Fellegi-Sunter -- it scores fields, while "
                f"a probabilistic matchkey combines EM-learned weights into "
                f"P(match). Omit scorer_udf for this config and ship a Python "
                f"environment, or use weighted/exact matchkeys."
            )


def _score_candidates_jvm_rowwise(
    joined: Any,
    config: Any,
    *,
    lhs: str,
    rhs: str,
    scorer_udf: str,
    transform_udf: str | None,
    fs_models: dict[str, Any] | None,
) -> Any:
    """``score_candidates``'s body with a ROW-shaped JVM scorer. No batching.

    Returns ``(a, b, score)``, identical in shape and semantics to
    :func:`_score_candidates_jvm` and to the Python path.

    ## Why this replaced the batched shape as the default

    J1 grouped pairs into arrays because Spark Connect permits only row-shaped
    UDFs, and asserted that a per-row downcall "would be dominated by call
    overhead". Measured (run 31714236735, 200k rows -> 1.9M pairs):

        J1's reshape        +1.997s   (73% of the gap to the Python path)
        the JNI mechanism   +0.747s   (27%)  = 393 ns/pair

    The batching cost 2.7x more than the downcall it avoided. Removing it made
    the JVM arm **1.95x faster** and brought it level with the pure Python path
    (0.98x), because Spark's arrays are ``ArrayData`` of ``InternalRow``, not
    columnar vectors: batching in the SQL layer materialises every pair three
    times (``collect_list`` struct, ``arrays_zip``, ``explode``) to avoid one
    columnar Arrow transfer that is cheaper than the churn.

    So this plan is the Python path's plan -- one projection, one UDF call per
    field per pair, one filter -- with the scorer call landing in the executor
    JVM. No ``collect_list``, no ``arrays_zip``, no ``explode``, and therefore
    no ``batch_size``: nothing is materialised as an array, so there is no heap
    commitment to bound.

    ## One evaluation per slot

    The per-slot score is projected to a named column and the weighted combine
    reads that name. Spark's ``CollapseProject`` declines to inline an
    expression that is referenced more than once and is not cheap, and a UDF is
    not cheap -- so a slot shared by several matchkeys is called ONCE, which is
    the same de-duplication :func:`_weighted_scorer_slots` gives the batched
    shape.
    """
    from pyspark.sql import functions as F

    from goldenmatch.spark.jvm import scorer_id

    matchkeys = list(config.get_matchkeys())
    _refuse_probabilistic(matchkeys)

    slots, index = _weighted_scorer_slots(config)

    parts = [F.col("__cand__.a").alias("a"), F.col("__cand__.b").alias("b")]
    for s in slots:
        a_col = F.col(f"{lhs}.{s['col']}")
        b_col = F.col(f"{rhs}.{s['col']}")
        if s["chain"]:
            a_val = _transformed(a_col, s["chain"], transform_udf=transform_udf)
            b_val = _transformed(b_col, s["chain"], transform_udf=transform_udf)
        else:
            a_val, b_val = a_col.cast("string"), b_col.cast("string")
        if s["scorer"] == "exact":
            # No kernel: the agreement IS the score, computed here in SQL --
            # identical to the batched shape, which also keeps `exact` out of
            # the UDF.
            parts.append(
                F.when(a_val == b_val, F.lit(1.0)).otherwise(F.lit(0.0))
                .alias(f"r{s['i']}")
            )
        else:
            parts.append(
                F.call_udf(
                    scorer_udf, F.lit(int(scorer_id(s["scorer"]))), a_val, b_val
                ).alias(f"s{s['i']}")
            )
        # Comparability from the RAW columns, as everywhere else: the kernel
        # maps a missing value to "" and would score null-vs-null as a PERFECT
        # 1.0, merging two records whose only shared evidence is that both are
        # missing the field.
        parts.append((a_col.isNotNull() & b_col.isNotNull()).alias(f"c{s['i']}"))
    for m, mk in enumerate(matchkeys):
        mk_type = getattr(mk, "type", None) or getattr(mk, "comparison", None)
        if mk_type == "exact":
            parts.append(_matchkey_score_expr(mk, lhs, rhs).alias(f"e{m}"))

    scored = joined.select(*parts)

    # ── the weighted combine, per matchkey ────────────────────────────
    #
    # Deliberately the same arithmetic as the batched shape, reading plain
    # column names instead of exploded struct fields. Any divergence here would
    # be a scoring difference between two paths that must agree, which is what
    # the parity gate exists to catch.
    accepted = []
    for m, mk in enumerate(matchkeys):
        mk_type = getattr(mk, "type", None) or getattr(mk, "comparison", None)
        if mk_type == "exact":
            score = F.col(f"e{m}")
        else:
            nums, dens = [], []
            for f in mk.fields:
                weight = float(f.weight)
                idx = index[_slot_key(f)]
                raw = F.col(f"r{idx}" if f.scorer == "exact" else f"s{idx}")
                comparable = F.col(f"c{idx}")
                nums.append(F.when(comparable, raw * F.lit(weight)).otherwise(F.lit(0.0)))
                dens.append(F.when(comparable, F.lit(weight)).otherwise(F.lit(0.0)))
            num, den = nums[0], dens[0]
            for n in nums[1:]:
                num = num + n
            for d in dens[1:]:
                den = den + d
            # den == 0 means no field was comparable -> score_pair returns 0.0.
            score = F.when(den > F.lit(0.0), num / den).otherwise(F.lit(0.0))

        em = (fs_models or {}).get(mk.name)
        # NULL where this matchkey rejects the pair; `greatest` skips nulls, so
        # a pair no matchkey accepted comes out null and is dropped.
        accepted.append(F.when(score >= F.lit(_matchkey_threshold(mk, em)), score))

    best = F.greatest(*accepted) if len(accepted) > 1 else accepted[0]
    return scored.select(
        F.col("a"), F.col("b"), best.alias("score")
    ).where(F.col("score").isNotNull())


# ── golden ───────────────────────────────────────────────────────────

def _field_strategy(golden_rules: Any, column: str) -> str:
    """The survivorship strategy for one column.

    Only the simple shapes resolve here: a single ``GoldenFieldRule`` per field
    and the default. A LIST of conditional rules is a ``when:`` cascade that
    needs the one-box evaluator, so it is refused rather than silently
    collapsed to its first clause.
    """
    default = getattr(golden_rules, "default_strategy", None)
    if default is None:
        default_rule = getattr(golden_rules, "default", None)
        default = getattr(default_rule, "strategy", None) if default_rule else None
    default = default or "most_complete"

    rule = (getattr(golden_rules, "field_rules", None) or {}).get(column)
    if rule is None:
        return default
    if isinstance(rule, list):
        raise NotImplementedError(
            f"The Spark tier does not support conditional (list) golden rules; "
            f"column {column!r} has {len(rule)} clauses. They need the one-box "
            f"`when:` evaluator, and collapsing them to one clause would "
            f"silently change survivorship."
        )
    strategy = getattr(rule, "strategy", None) or default
    if getattr(rule, "when", None):
        raise NotImplementedError(
            f"The Spark tier does not support `when:` conditions on golden "
            f"rules (column {column!r})."
        )
    return strategy


def build_golden_from_rules(
    assignments: Any,
    source_df: Any,
    config: Any,
    *,
    golden_cols: list[str],
    id_col: str,
    survivorship_udf: str | None = None,
) -> Any:
    """Golden records with a PER-FIELD strategy, unlike ``build_golden``'s one
    strategy for every column.

    ``survivorship_udf`` routes the merge through the jar's
    ``golden_survivorship`` instead of a Python worker. Same no-fallback rule as
    the other kernels: a wrong survivor is a golden record that looks entirely
    right, so the kernel refuses a strategy it cannot run identically rather
    than approximating it.
    """
    from pyspark.sql import functions as F

    from goldenmatch.config.schemas import GoldenRulesConfig
    from goldenmatch.spark.golden import make_merge_udf, merge_expr

    golden_rules = config.golden_rules or GoldenRulesConfig(
        default_strategy="most_complete"
    )

    joined = assignments.join(
        source_df, assignments["member_id"] == source_df[id_col]
    )
    multi = (
        joined.groupBy("cluster_id")
        .agg(F.count(F.lit(1)).alias("__n__"))
        .where(F.col("__n__") > 1)
        .select("cluster_id")
    )
    joined = joined.join(multi, on="cluster_id")

    collected = joined.groupBy("cluster_id").agg(
        *[
            F.collect_list(F.col(c).cast("string")).alias(f"__vals_{c}__")
            for c in golden_cols
        ]
    )
    def merged(c: str) -> Any:
        strategy = _field_strategy(golden_rules, c)
        vals = F.col(f"__vals_{c}__")
        if survivorship_udf is not None:
            return merge_expr(vals, strategy, survivorship_udf).alias(c)
        return make_merge_udf(strategy)(vals).alias(c)

    return collected.select(
        F.col("cluster_id"), *[merged(c) for c in golden_cols]
    )


# ── entry point ──────────────────────────────────────────────────────

def _resolve_fs_models(config: Any, fs_model_path: str | None) -> dict[str, Any]:
    """Trained FS model per probabilistic matchkey, resolved BEFORE any Spark
    work starts.

    Up front on purpose: a missing model is a config error, and discovering it
    after the blocking self-join has been submitted turns a one-line message
    into a failed distributed job.
    """
    from goldenmatch.spark.probabilistic import resolve_fs_model

    models: dict[str, Any] = {}
    for mk in config.get_matchkeys():
        mk_type = getattr(mk, "type", None) or getattr(mk, "comparison", None)
        if mk_type == "probabilistic":
            models[mk.name] = resolve_fs_model(mk, model_path=fs_model_path)
    return models


def run_config_pipeline(
    source_df: Any,
    config: Any = None,
    *,
    id_col: str = "__row_id__",
    golden_cols: list[str] | None = None,
    wcc: str = "scale",
    fs_model_path: str | None = None,
    allow_large_autoconfig: bool = False,
    transform_udf: str | None = None,
    survivorship_udf: str | None = None,
    scorer_udf: str | None = None,
    scorer_shape: str = "row",
) -> Any:
    """Run the Spark tier from a ``GoldenMatchConfig``.

    ``source_df`` is a Spark DataFrame carrying ``id_col`` (int) plus every
    column the config's blocking keys, matchkey fields and golden rules name.
    Returns the golden DataFrame ``(cluster_id, *golden_cols)``.

    ``golden_cols`` defaults to every column named by a golden rule, or -- when
    the config declares none -- every matchkey field. It is an explicit
    parameter because "which columns end up in the golden record" is a product
    decision the config does not always state.

    ``scorer_shape`` picks how the JVM scorer is CALLED, and defaults to
    ``"row"`` because that is 1.95x faster (measured, run 31714236735). J1
    batched pairs into arrays on the assertion that a per-row downcall "would
    be dominated by call overhead"; the bisect put +1.997s on the reshape that
    batching requires and +0.747s (393 ns/pair) on the downcall itself, so the
    cure was 2.7x the disease. ``"batch"`` keeps the array shape reachable --
    it amortises string marshalling per call and could still win on long
    values, and the parity gate compares the two.

    ``transform_udf``, ``survivorship_udf`` and ``scorer_udf`` route
    normalization, survivorship and scoring into the jar's kernels. **Passing
    all three runs the whole pipeline with no Python worker on the executors**,
    which is what the jar exists for -- clustering is already pure Spark SQL, so
    those three are the entire surface.

    ``scorer_udf`` arrived last and was the hard one. The obvious reading of the
    gap -- "the config pipeline scores per-field and row-shaped, the jar scores
    batched and array-shaped" -- is wrong: :func:`make_scorer_udf` is an ARROW
    udf, so it already receives a whole batch and returns a whole batch. The two
    compute the same function at the same granularity. The real difference is
    that Spark forms an arrow_udf's batch implicitly, while a Java UDF over
    Connect sees one row and must be handed its batch explicitly. See
    :func:`_score_candidates_jvm`.

    Probabilistic matchkeys are refused under ``scorer_udf`` rather than left on
    the Python path, so "no error" cannot mean "still needs a virtualenv".
    """
    if config is None:
        # P6 zero-config. Deriving the config costs a driver-side sample plus a
        # cluster-wide count, so it happens once here rather than being folded
        # into the scoring path.
        from goldenmatch.spark.autoconfig import auto_configure_spark

        config, _provenance = auto_configure_spark(
            source_df, allow_large=allow_large_autoconfig
        )

    _validate_spark_config_supported(config)

    from goldenmatch.spark.clustering import (
        connected_components,
        connected_components_scale,
    )

    if golden_cols is None:
        golden_cols = _default_golden_cols(config)

    fs_models = _resolve_fs_models(config, fs_model_path)

    candidates = generate_candidates(
        source_df, config, id_col=id_col, transform_udf=transform_udf
    )
    pairs = score_candidates(
        candidates, source_df, config, id_col=id_col, fs_models=fs_models,
        scorer_udf=scorer_udf, transform_udf=transform_udf,
        scorer_shape=scorer_shape,
    )

    ids_df = source_df.select(id_col)
    if wcc == "scale":
        assignments = connected_components_scale(pairs, ids_df, id_col=id_col)
    elif wcc == "label_prop":
        assignments = connected_components(pairs, ids_df, id_col=id_col)
    else:
        raise ValueError(
            f"wcc must be 'scale' or 'label_prop'; got {wcc!r}. (An "
            f"unrecognized value would have silently degraded to label-prop.)"
        )

    return build_golden_from_rules(
        assignments,
        source_df,
        config,
        golden_cols=golden_cols,
        id_col=id_col,
        survivorship_udf=survivorship_udf,
    )


def _default_golden_cols(config: Any) -> list[str]:
    """Columns to survive when the caller does not say."""
    rules = getattr(config, "golden_rules", None)
    named = list((getattr(rules, "field_rules", None) or {}).keys()) if rules else []
    if named:
        return named
    seen: list[str] = []
    for mk in config.get_matchkeys():
        for f in mk.fields:
            if f.resolved_field not in seen:
                seen.append(f.resolved_field)
    return seen
