"""Vectorized survivorship resolution (provenance=False fast path).

Byte-identical to the slow per-cluster path (the oracle). Built up
strategy-by-strategy, each gated by a parity test in test_native_parity.py.
"""
from __future__ import annotations

import polars as pl

from goldenmatch.core.golden import _is_internal, _stable_value_expr
from goldenmatch.core.survivorship.validate import goldenflow_filter


def survivorship_native_eligible(rules, provenance) -> bool:
    """True when the vectorized survivorship path can handle this config.
    Returns False for now -- flipped on in Phase F when the path is complete."""
    # rules/provenance inspected in Phase F when the gate is flipped on
    return False


def _populated_count_expr(columns) -> pl.Expr:
    """Per-row populated count over ``columns`` (non-null cells), as Int32.

    Mirrors winner._populated: ``sum(1 for c in cols if row.get(c) is not None)``.
    """
    total = pl.lit(0, dtype=pl.Int32)
    for c in columns:
        total = total + pl.col(c).is_not_null().cast(pl.Int32)
    return total


def _sorted_for_group(multi_df: pl.DataFrame, g) -> pl.DataFrame:
    """Sort ``multi_df`` so that, within each ``__cluster_id__``, row 0 is the
    group winner per ``g.strategy`` and ties resolve to the lowest ``__row_id__``.

    The sort keys reproduce winner._ranking exactly (best-first), with
    ``__row_id__`` ascending as the final deterministic tiebreak (the slow path
    sorts the frame by ``[__cluster_id__, __row_id__]`` first, so positional
    index == ``__row_id__`` ascending).
    """
    strategy = g.strategy
    # Defensive backstop: the Phase-F eligibility gate pre-checks column
    # presence and falls back to the slow path for a misconfigured call, so
    # this guard normally never fires -- it just makes the failure legible if
    # an absent date_column/anchor ever reaches here (vs a cryptic Polars
    # ColumnNotFound on the sort).
    if strategy == "most_recent" and g.date_column not in multi_df.columns:
        raise ValueError(
            f"survivorship group {g.name!r}: most_recent date_column "
            f"{g.date_column!r} not present in frame columns {multi_df.columns}"
        )
    if strategy == "anchor" and g.anchor not in multi_df.columns:
        raise ValueError(
            f"survivorship group {g.name!r}: anchor column {g.anchor!r} "
            f"not present in frame columns {multi_df.columns}"
        )
    helper_cols: list[str] = []
    # global sort by [cluster_id, <strategy key>, row_id];
    # group_by(maintain_order=True).first() then yields each cluster's winner
    # (first row in its partition).
    by: list[str] = ["__cluster_id__"]
    descending: list[bool] = [False]
    nulls_last: list[bool] = [False]

    if strategy == "source_priority":
        # rank = index in source_priority; unknown source -> len(priority) sentinel.
        priority = list(g.source_priority or [])
        sentinel = len(priority)
        rank_map = {s: i for i, s in enumerate(priority)}
        # replace_strict maps any source not in rank_map (incl. null) to the
        # sentinel, matching winner._ranking's ``rank.get(source, len(rank))``.
        df = multi_df.with_columns(
            pl.col("__source__").replace_strict(
                rank_map, default=sentinel, return_dtype=pl.Int64
            ).alias("__src_rank__")
        )
        helper_cols.append("__src_rank__")
        by.append("__src_rank__")
        descending.append(False)  # source rank ascending (best = lowest index)
        nulls_last.append(False)
    elif strategy == "most_recent":
        # date DESC, nulls LAST. (winner._ranking: key=(d is not None, d),
        # reverse=True -> non-null first, then date desc; null dates last.)
        df = multi_df
        by.append(g.date_column)
        descending.append(True)
        nulls_last.append(True)
    elif strategy == "anchor":
        # anchor-present first (DESC), then populated-count DESC. Degrades to
        # most_complete when no row has the anchor (all present=False).
        df = multi_df.with_columns([
            pl.col(g.anchor).is_not_null().alias("__anchor_present__"),
            _populated_count_expr(g.columns).alias("__pop__"),
        ])
        helper_cols.extend(["__anchor_present__", "__pop__"])
        by.append("__anchor_present__")
        descending.append(True)  # present (True) before absent (False)
        nulls_last.append(False)
        by.append("__pop__")
        descending.append(True)
        nulls_last.append(False)
    else:  # most_complete
        df = multi_df.with_columns(
            _populated_count_expr(g.columns).alias("__pop__")
        )
        helper_cols.append("__pop__")
        by.append("__pop__")
        descending.append(True)  # populated-count descending
        nulls_last.append(False)

    # Final deterministic tiebreak: lowest __row_id__ wins (== slow-path
    # positional index 0 after the [__cluster_id__, __row_id__] presort).
    by.append("__row_id__")
    descending.append(False)
    nulls_last.append(False)

    out = df.sort(by=by, descending=descending, nulls_last=nulls_last)
    if helper_cols:
        out = out.drop(helper_cols)
    return out


def _resolve_group(multi_df: pl.DataFrame, g) -> pl.DataFrame:
    """Resolve one field group to one row per cluster (``__cluster_id__`` +
    ``g.columns``). Byte-identical to winner.group_winner for provenance=False.
    """
    ordered = _sorted_for_group(multi_df, g)
    if g.allow_fill:
        # allow_fill: first non-null walking the strategy ranking. Relies on
        # (a) df.sort() being stable and (b) group_by(maintain_order=True).agg()
        # preserving the sorted within-group row order, so drop_nulls().first()
        # == first non-null in ranking order (matches winner.group_winner's walk).
        agg_exprs = [pl.col(c).drop_nulls().first().alias(c) for c in g.columns]
    else:
        # lock-step: every column = the winner row's value (nulls included).
        agg_exprs = [pl.col(c).first().alias(c) for c in g.columns]
    return (
        ordered.group_by("__cluster_id__", maintain_order=True)
        .agg(agg_exprs)
    )


# Strategies a scalar value-expr cannot express as a pure aggregate; the
# Phase-F eligibility gate routes a config using these to the slow path. Until
# then a Phase C config must not use them (parity configs don't).
_SCALAR_NATIVE_INELIGIBLE = ("custom:", "confidence_majority")


def _scalar_strategy_native_ineligible(strategy: str) -> bool:
    """True for scalar strategies the native value-expr cannot express
    (custom plugins / confidence_majority -- both need per-cluster Python)."""
    return any(
        strategy == s or strategy.startswith(s)
        for s in _SCALAR_NATIVE_INELIGIBLE
    )


def _scalar_resolution_rule(col, rules, default_rule):
    """The single ``GoldenFieldRule`` that governs scalar column ``col``.

    Mirrors ``resolve_cluster``: ``rules.field_rules.get(col, default_rule)``,
    then (for a non-conditional, non-list rule) the rule itself. List-form
    ``when:`` conditionals are Phase E and rejected here so a misconfigured
    Phase C call fails legibly rather than silently mis-resolving.
    """
    rule_entry = rules.field_rules.get(col, default_rule)
    if isinstance(rule_entry, list):
        raise NotImplementedError(
            f"survivorship scalar {col!r}: list-form conditional field_rules "
            "(when:) are Phase E, not handled by the native path"
        )
    return rule_entry


def _scalar_value_expr(col: str, len_alias: str | None, rule, has_row_id: bool) -> pl.Expr:
    """Per-cluster survivor VALUE for scalar column ``col`` under ``rule``.

    Byte-identical to ``merge_field``'s winning VALUE (confidence is Phase D):

    * ``most_complete`` / ``longest_value`` -> longest non-null string, ties
      broken by lowest ``__row_id__``. Both pick the same value (they differ
      only in tie confidence, which Phase C does not compare), so both reuse
      ``_stable_value_expr``'s ``most_complete`` branch.
    * ``first_non_null`` -> lowest-``__row_id__`` non-null. Reuses
      ``_stable_value_expr``'s ``first_non_null`` branch.
    * ``most_recent`` -> value at the max ``date_column`` among rows where BOTH
      the value AND the date are non-null (``merge_field._most_recent`` requires
      both); ties on the top date broken by lowest ``__row_id__``.
    * ``source_priority`` -> walking ``rule.source_priority``, the value of the
      FIRST-occurring (lowest ``__row_id__``) row of the best-ranked source
      whose first occurrence is non-null. A source whose first occurrence is
      null is skipped even if a later same-source row is populated, matching
      ``merge_field._source_priority``'s first-occurrence record.

    The all-non-null-agree short-circuit in ``merge_field`` returns the first
    non-null index regardless of strategy, but the VALUE there is identical to
    every per-strategy pick (all candidates equal), so it needs no special case
    for value parity.
    """
    strategy = rule.strategy
    if strategy in ("most_complete", "longest_value"):
        # _stable_value_expr keys on the "most_complete" branch (len desc,
        # __row_id__ asc). longest_value picks the same value (str(v) length),
        # differing from most_complete only in tie confidence (Phase D).
        return _stable_value_expr(col, len_alias, "most_complete", has_row_id)
    if strategy == "first_non_null":
        return _stable_value_expr(col, None, "first_non_null", has_row_id)
    if strategy == "most_recent":
        date_col = rule.date_column
        # Both value and date must be present (merge_field._most_recent filters
        # on ``v is not None and d is not None``).
        mask = pl.col(col).is_not_null() & pl.col(date_col).is_not_null()
        nn = pl.col(col).filter(mask)
        if has_row_id:
            # date DESC, then __row_id__ ASC -> one composite struct key
            # (date, -__row_id__) sorted descending (same idiom as
            # _stable_value_expr's most_complete key).
            key = pl.struct([
                pl.col(date_col),
                -pl.col("__row_id__").cast(pl.Int64),
            ]).filter(mask)
            return nn.sort_by(key, descending=True).first()
        # No __row_id__: stable date-desc sort -> ties keep input order.
        return nn.sort_by(pl.col(date_col).filter(mask), descending=True).first()
    if strategy == "source_priority":
        # rank = index in source_priority; unknown/null source -> sentinel.
        priority = list(rule.source_priority or [])
        sentinel = len(priority)
        rank_map = {s: i for i, s in enumerate(priority)}
        src_rank = pl.col("__source__").replace_strict(
            rank_map, default=sentinel, return_dtype=pl.Int64
        )
        # First occurrence of a source = its lowest-__row_id__ row. A row is
        # eligible to win only if it is that first occurrence AND its value is
        # non-null (merge_field records the first occurrence's value -- a null
        # there blocks the source). Among eligible rows, lowest source rank
        # wins; sources only appear once among first-occurrences so no further
        # tiebreak is needed, but rank<sentinel guards against an unknown
        # source winning (merge_field only returns sources listed in priority).
        if has_row_id:
            is_first = (
                pl.col("__row_id__")
                == pl.col("__row_id__").min().over("__source__")
            )
            eligible = is_first & pl.col(col).is_not_null() & (src_rank < sentinel)
            nn = pl.col(col).filter(eligible)
            key = src_rank.filter(eligible)
            return nn.sort_by(key, descending=False).first()
        # No __row_id__: fall back to input order for "first occurrence".
        # cum_count over source == 0 marks the first occurrence in input order.
        is_first = pl.col("__source__").cum_count().over("__source__") == 1
        eligible = is_first & pl.col(col).is_not_null() & (src_rank < sentinel)
        nn = pl.col(col).filter(eligible)
        key = src_rank.filter(eligible)
        return nn.sort_by(key, descending=False).first()
    raise NotImplementedError(
        f"survivorship scalar strategy {strategy!r} not handled by the native path"
    )


def _resolve_scalars(multi_df: pl.DataFrame, rules, scalar_cols) -> pl.DataFrame:
    """Resolve every scalar column to one row per cluster (``__cluster_id__`` +
    ``scalar_cols``). Byte-identical (values only) to ``resolve_cluster``'s
    per-scalar ``merge_field`` walk for non-conditional field rules.
    """
    from goldenmatch.config.schemas import GoldenFieldRule

    default_rule = GoldenFieldRule(strategy=rules.default_strategy)
    has_row_id = "__row_id__" in multi_df.columns

    rules_by_col: dict = {}
    for col in scalar_cols:
        rule = _scalar_resolution_rule(col, rules, default_rule)
        if _scalar_strategy_native_ineligible(rule.strategy):
            # Phase F gate routes these to the slow path; refuse loudly for now.
            raise NotImplementedError(
                f"survivorship scalar {col!r}: strategy {rule.strategy!r} is "
                "native-ineligible (custom/confidence_majority); slow path only"
            )
        rules_by_col[col] = rule

    # Pass 1: apply every validate: pre-mask. Invalid cells -> null using the
    # SAME goldenflow validator series the slow path filters with. We replace
    # the column in place so downstream length/agg exprs read the masked column
    # (exactly as resolve_cluster filters candidates BEFORE merge_field).
    mask_exprs: list = []
    for col in scalar_cols:
        validator_name = getattr(rules_by_col[col], "validate_with", None)
        if validator_name:
            values = multi_df[col].to_list()
            filtered = goldenflow_filter(values, validator_name)
            # filtered[i] is None exactly where the candidate was dropped (or
            # already null); rebuild the column straight from it.
            mask_exprs.append(
                pl.Series(name=col, values=filtered, dtype=multi_df[col].dtype)
            )
    prepped = multi_df.with_columns(mask_exprs) if mask_exprs else multi_df

    # Pass 2: length helpers for most_complete/longest_value, computed FROM the
    # already-masked column so a validate-dropped value can't win on length.
    len_aliases: dict = {}
    len_exprs: list = []
    for col in scalar_cols:
        if rules_by_col[col].strategy in ("most_complete", "longest_value"):
            alias = f"__len_{col}__"
            len_aliases[col] = alias
            len_exprs.append(
                pl.col(col).cast(pl.Utf8).str.len_chars().alias(alias)
            )
    if len_exprs:
        prepped = prepped.with_columns(len_exprs)

    agg_exprs = [
        _scalar_value_expr(col, len_aliases.get(col), rules_by_col[col], has_row_id).alias(col)
        for col in scalar_cols
    ]
    return (
        prepped.group_by("__cluster_id__", maintain_order=True)
        .agg(agg_exprs)
    )


def build_survivorship_native(multi_df, rules) -> pl.DataFrame:
    """Vectorized survivorship (Phase B groups + Phase C scalars): one row per
    cluster carrying ``__cluster_id__`` + every resolved user column.

    Group columns are resolved in lock-step per ``field_groups`` (Phase B).
    Scalar columns (any user column NOT in a group) are resolved by their
    per-field ``GoldenFieldRule`` strategy, or ``default_strategy`` (Phase C).
    Each unit resolves over the same source frame and is joined back on
    ``__cluster_id__``. List-form ``when:`` conditionals are Phase E.
    """
    result: pl.DataFrame | None = None
    for g in rules.field_groups:
        resolved = _resolve_group(multi_df, g)
        if result is None:
            result = resolved
        else:
            # inner: every group resolves over the same source frame -> identical cluster set
            result = result.join(resolved, on="__cluster_id__", how="inner")

    # Scalar columns = user columns not owned by any group.
    grouped_cols = {c for g in rules.field_groups for c in g.columns}
    scalar_cols = [
        c for c in multi_df.columns
        if not _is_internal(c) and c != "__cluster_id__" and c not in grouped_cols
    ]
    if scalar_cols:
        scalars = _resolve_scalars(multi_df, rules, scalar_cols)
        if result is None:
            result = scalars
        else:
            result = result.join(scalars, on="__cluster_id__", how="inner")

    if result is None:
        # No groups and no scalars: just the distinct cluster ids.
        result = multi_df.select("__cluster_id__").unique(maintain_order=True)
    return result
