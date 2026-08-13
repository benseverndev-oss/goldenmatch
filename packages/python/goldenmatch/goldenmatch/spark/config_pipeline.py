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
) -> Any:
    """Run the Spark tier from a ``GoldenMatchConfig``.

    ``source_df`` is a Spark DataFrame carrying ``id_col`` (int) plus every
    column the config's blocking keys, matchkey fields and golden rules name.
    Returns the golden DataFrame ``(cluster_id, *golden_cols)``.

    ``golden_cols`` defaults to every column named by a golden rule, or -- when
    the config declares none -- every matchkey field. It is an explicit
    parameter because "which columns end up in the golden record" is a product
    decision the config does not always state.

    ``transform_udf`` and ``survivorship_udf`` route normalization and
    survivorship into the jar's kernels, so those two stages need no Python
    worker.

    **Passing both does NOT make this run jar-only, and there is deliberately no
    ``scorer_udf`` to pass.** Scoring here is per-field and row-shaped
    (:func:`_field_score_parts` builds one Python UDF per field of each
    matchkey), while the jar's scorer is a batched, array-shaped UDF over a
    single value column -- the shape Spark Connect forces. They are different
    call structures, not the same call with a different backend, so routing this
    stage is a design change rather than a parameter. Until that lands, a
    ``run_config_pipeline`` call still needs a Python environment on the
    executors for scoring alone, whatever the other two arguments say.
    ``scripts/spark_jar_only_inventory.py`` probes this end to end and reports
    it rather than leaving it to be read off the source.
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
        candidates, source_df, config, id_col=id_col, fs_models=fs_models
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
