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
_SUPPORTED_BLOCKING = ("static",)

# Matchkey types P4 executes. "probabilistic" is P5's whole subject.
_SUPPORTED_MATCHKEY_TYPES = ("weighted", "exact")

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
    if blocking is None or not getattr(blocking, "keys", None):
        raise ValueError(
            "The Spark tier requires explicit blocking keys "
            "(config.blocking.keys). Without candidate generation the tier "
            "would self-join every record against every other -- refused "
            "rather than attempted."
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
            extra = (
                " Fellegi-Sunter on Spark is P5 of "
                "docs/superpowers/specs/2026-08-10-spark-native-execution-"
                "design.md; no m_prob/u_prob/match_weight exists in this tier "
                "yet, so a probabilistic matchkey cannot be executed here."
                if mk_type == "probabilistic"
                else ""
            )
            raise NotImplementedError(
                f"The Spark tier supports matchkey types "
                f"{_SUPPORTED_MATCHKEY_TYPES}; matchkey {mk.name!r} has "
                f"type={mk_type!r}.{extra}"
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

def _block_key_column(key_config: Any) -> tuple[Any, list[str]]:
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
        part = _transformed(raw, chain) if chain else raw.cast("string")
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


def generate_candidates(source_df: Any, config: Any, *, id_col: str) -> Any:
    """Candidate pairs ``(a, b)`` with ``a < b``, unioned over every blocking
    pass and de-duplicated.

    Multiple passes are a UNION of self-joins, exactly as the one-box blocker
    unions its per-key blocks: a pair generated by two passes is ONE candidate,
    not two, so `distinct()` here is semantic and not an optimization.
    """
    from pyspark.sql import functions as F

    per_pass = []
    for i, key_config in enumerate(config.blocking.keys):
        key_col, _fields = _block_key_column(key_config)
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


def _transformed(col: Any, chain: list[str]) -> Any:
    """Apply a one-box transform chain to a column via ``apply_transforms``."""
    from pyspark.sql.functions import pandas_udf

    @pandas_udf("string")
    def _udf(c):
        import pandas as pd

        from goldenmatch.utils.transforms import apply_transforms

        return pd.Series(
            [
                None if v is None else apply_transforms(str(v), chain)
                for v in c.tolist()
            ],
            dtype="object",
        )

    return _udf(col.cast("string"))


def _matchkey_score_expr(mk: Any, a_prefix: str, b_prefix: str) -> Any:
    """The Spark twin of ``weighted_pair_score`` for one matchkey."""
    from pyspark.sql import functions as F

    mk_type = getattr(mk, "type", None) or getattr(mk, "comparison", None)
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


def score_candidates(
    candidates: Any, source_df: Any, config: Any, *, id_col: str
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

    per_mk = []
    for mk in config.get_matchkeys():
        mk_type = getattr(mk, "type", None) or getattr(mk, "comparison", None)
        threshold = 1.0 if mk_type == "exact" else float(mk.threshold)
        scored = joined.select(
            F.col("__cand__.a").alias("a"),
            F.col("__cand__.b").alias("b"),
            _matchkey_score_expr(mk, lhs, rhs).alias("score"),
        ).where(F.col("score") >= F.lit(threshold))
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
) -> Any:
    """Golden records with a PER-FIELD strategy, unlike ``build_golden``'s one
    strategy for every column."""
    from pyspark.sql import functions as F

    from goldenmatch.config.schemas import GoldenRulesConfig
    from goldenmatch.spark.golden import make_merge_udf

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
    return collected.select(
        F.col("cluster_id"),
        *[
            make_merge_udf(_field_strategy(golden_rules, c))(
                F.col(f"__vals_{c}__")
            ).alias(c)
            for c in golden_cols
        ],
    )


# ── entry point ──────────────────────────────────────────────────────

def run_config_pipeline(
    source_df: Any,
    config: Any,
    *,
    id_col: str = "__row_id__",
    golden_cols: list[str] | None = None,
    wcc: str = "scale",
) -> Any:
    """Run the Spark tier from a ``GoldenMatchConfig``.

    ``source_df`` is a Spark DataFrame carrying ``id_col`` (int) plus every
    column the config's blocking keys, matchkey fields and golden rules name.
    Returns the golden DataFrame ``(cluster_id, *golden_cols)``.

    ``golden_cols`` defaults to every column named by a golden rule, or -- when
    the config declares none -- every matchkey field. It is an explicit
    parameter because "which columns end up in the golden record" is a product
    decision the config does not always state.
    """
    _validate_spark_config_supported(config)

    from goldenmatch.spark.clustering import (
        connected_components,
        connected_components_scale,
    )

    if golden_cols is None:
        golden_cols = _default_golden_cols(config)

    candidates = generate_candidates(source_df, config, id_col=id_col)
    pairs = score_candidates(candidates, source_df, config, id_col=id_col)

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
        assignments, source_df, config, golden_cols=golden_cols, id_col=id_col
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
