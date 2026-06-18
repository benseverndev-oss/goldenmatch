"""Vectorized survivorship resolution (provenance=False fast path).

Byte-identical to the slow per-cluster path (the oracle). Built up
strategy-by-strategy, each gated by a parity test in test_native_parity.py.
"""
from __future__ import annotations

import polars as pl

from goldenmatch.core.golden import _is_internal, _stable_value_expr
from goldenmatch.core.survivorship.conditions import select_conditional_strategy
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


def _group_conf_name(g) -> str:
    """Internal per-cluster confidence column name for group ``g`` (dropped
    before the final frame; only the row-wise mean survives as
    ``__golden_confidence__``)."""
    return f"__conf_group_{g.name}__"


def _group_tie_expr(g, pop: pl.Expr | None = None) -> pl.Expr:
    """Per-cluster tie indicator for group ``g`` (Boolean), matching
    winner._ranking's ``tie`` flag.

    * ``most_complete``: >=2 rows share the max populated-count.
    * ``anchor``: >=2 rows share the top composite key ``(anchor_present,
      populated_count)`` -- i.e. the same (present, count) as the rank-0 winner.
    * ``source_priority`` / ``most_recent``: winner._ranking returns ``tie=False``
      unconditionally, so no tie ever applies (confidence is never scaled by 0.7).

    ``pop`` is the per-row populated-count expr; the caller (:func:`_group_conf_expr`)
    passes its own already-built expr to avoid constructing the subtree twice.
    """
    if pop is None:
        pop = _populated_count_expr(g.columns)
    if g.strategy == "most_complete":
        return (pop == pop.max()).sum() > 1
    if g.strategy == "anchor":
        present = pl.col(g.anchor).is_not_null()
        # Top key is the rank-0 winner's (present, pop). After _sorted_for_group
        # the winner is row 0, so the top key == (present.first(), pop ordered by
        # the winner). Equivalent: count rows whose (present, pop) equals the max
        # present AND, among those, the max pop -- but the winner is whichever has
        # the highest (present, pop) lexicographically. Reproduce winner._ranking:
        #   top_key = (present[w], counts[w]); tie = #rows with that key > 1.
        # The winning (present, pop) is the lexicographic max over rows.
        # present True sorts above False; within the chosen present, max pop wins.
        top_present = present.max()  # True if any row has the anchor
        # pop restricted to rows at the winning present level. When no row has
        # the anchor, top_present=False selects ALL rows -> degrades to
        # most_complete (filter is never empty: >=1 row is always at
        # top_present).
        pop_at_top = pop.filter(present == top_present)
        top_pop = pop_at_top.max()
        return ((present == top_present) & (pop == top_pop)).sum() > 1
    # source_priority / most_recent: never tie.
    return pl.lit(False)


def _group_conf_expr(g) -> pl.Expr:
    """Per-cluster group confidence for ``g`` (Float64), byte-identical to
    winner.group_winner's ``conf``.

    ``base = (winner_populated + n_filled) / len(g.columns)``; ``x 0.7`` on a tie.

    Operates on the frame already sorted by :func:`_sorted_for_group`, so the
    rank-0 winner is row 0 of each ``__cluster_id__`` partition:

    * ``winner_populated`` = the winner row's OWN non-null count among
      ``g.columns`` (PRE-fill) = ``_populated_count_expr(...).first()``.
    * ``n_filled`` = group cells where the winner is null AND a non-null donor
      exists in the ranking (``allow_fill`` only; 0 otherwise). Per column the
      back-fill resolves to ``drop_nulls().first()``, so a cell is "filled" iff
      ``col.first() is null AND col.drop_nulls().first() is not null``.
    """
    n_cols = len(g.columns)
    # Build the per-row populated-count subtree ONCE and reuse it for both
    # winner_populated and the tie check (Polars CSE would dedup it either way,
    # but sharing the expr keeps the intent explicit).
    pop = _populated_count_expr(g.columns)
    winner_populated = pop.first().cast(pl.Float64)
    if g.allow_fill:
        filled = pl.lit(0, dtype=pl.Float64)
        for c in g.columns:
            filled = filled + (
                pl.col(c).first().is_null() & pl.col(c).drop_nulls().first().is_not_null()
            ).cast(pl.Float64)
    else:
        filled = pl.lit(0.0, dtype=pl.Float64)
    base = (winner_populated + filled) / pl.lit(float(n_cols)) if n_cols else pl.lit(0.0)
    tie = _group_tie_expr(g, pop)
    return pl.when(tie).then(base * 0.7).otherwise(base)


def _resolve_group(multi_df: pl.DataFrame, g) -> pl.DataFrame:
    """Resolve one field group to one row per cluster (``__cluster_id__`` +
    ``g.columns`` + the internal per-cluster confidence column). Byte-identical
    to winner.group_winner for provenance=False (value + confidence).
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
    # One confidence per group (the mean denominator counts a group as ONE unit
    # regardless of column count -- resolve_cluster appends res.confidence once).
    agg_exprs.append(_group_conf_expr(g).alias(_group_conf_name(g)))
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
    ``when:`` conditionals are routed to the conditional path
    (:func:`_resolve_conditionals`) BEFORE this is called, so a list reaching
    here is a routing bug -- fail legibly rather than silently mis-resolve.
    """
    rule_entry = rules.field_rules.get(col, default_rule)
    if isinstance(rule_entry, list):
        raise NotImplementedError(
            f"survivorship scalar {col!r}: list-form conditional field_rules "
            "(when:) must be routed to the conditional path, not the scalar path"
        )
    return rule_entry


def _source_priority_eligible(col: str, rule, has_row_id: bool):
    """Returns ``(src_rank_expr, eligible_mask_expr)`` for source_priority scalar
    resolution, shared by BOTH the value path (:func:`_scalar_value_expr`) and the
    confidence path (:func:`_scalar_conf_expr`) so the eligibility logic stays in
    one place.

    ``eligible`` = the first-occurrence row per source (lowest ``__row_id__``, or
    input order without one) AND the value is non-null AND the source is in the
    priority list. ``src_rank`` is the index in ``rule.source_priority`` (unknown /
    null source -> ``len(priority)`` sentinel), matching ``merge_field``'s record.

    The ``.over("__source__")`` is evaluated PER-CLUSTER-PARTITION because this expr
    runs inside ``group_by("__cluster_id__").agg(...)`` -- the window is scoped to
    each cluster's rows, never the global frame.
    """
    priority = list(rule.source_priority or [])
    sentinel = len(priority)
    rank_map = {s: i for i, s in enumerate(priority)}
    src_rank = pl.col("__source__").replace_strict(
        rank_map, default=sentinel, return_dtype=pl.Int64
    )
    if has_row_id:
        is_first = pl.col("__row_id__") == pl.col("__row_id__").min().over("__source__")
    else:
        is_first = pl.col("__source__").cum_count().over("__source__") == 1
    eligible = is_first & pl.col(col).is_not_null() & (src_rank < sentinel)
    return src_rank, eligible


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
                # cast to signed before negation so an unsigned __row_id__ negates
                # correctly (date DESC, row_id ASC)
                -pl.col("__row_id__").cast(pl.Int64),
            ]).filter(mask)
            return nn.sort_by(key, descending=True).first()
        # No __row_id__: stable date-desc sort -> ties keep input order.
        return nn.sort_by(pl.col(date_col).filter(mask), descending=True).first()
    if strategy == "source_priority":
        # First occurrence of a source = its lowest-__row_id__ row. A row is
        # eligible to win only if it is that first occurrence AND its value is
        # non-null (merge_field records the first occurrence's value -- a null
        # there blocks the source). Among eligible rows, lowest source rank
        # wins; sources only appear once among first-occurrences so no further
        # tiebreak is needed, but rank<sentinel (inside the shared helper)
        # guards against an unknown source winning (merge_field only returns
        # sources listed in priority). Eligibility shared with the conf path.
        src_rank, eligible = _source_priority_eligible(col, rule, has_row_id)
        nn = pl.col(col).filter(eligible)
        key = src_rank.filter(eligible)
        return nn.sort_by(key, descending=False).first()
    raise NotImplementedError(
        f"survivorship scalar strategy {strategy!r} not handled by the native path"
    )


def _scalar_conf_name(col: str) -> str:
    """Internal per-cluster confidence column name for scalar ``col`` (dropped
    before the final frame; only the row-wise mean survives)."""
    return f"__conf_scalar_{col}__"


def _scalar_conf_expr(col: str, len_alias: str | None, rule, has_row_id: bool) -> pl.Expr:
    """Per-cluster scalar confidence for ``col`` under ``rule`` (Float64),
    byte-identical to ``merge_field``'s returned confidence.

    The ``merge_field`` control flow this reproduces, in order:

    1. ``non_null`` empty -> ``0.0``.
    2. all non-null values identical (``nuniq <= 1``) -> ``1.0`` for EVERY
       strategy (the short-circuit BEFORE strategy dispatch).
    3. otherwise the strategy's own confidence constant:
       * ``most_complete``: ``1.0`` if the longest ``str(v)`` is unique among
         non-nulls, else ``0.7`` (length tie).
       * ``longest_value``: same winner, tie confidence ``0.5``.
       * ``most_recent``: among rows with value AND date both non-null, ``1.0``
         for a unique top date, ``0.5`` on a date tie, ``0.0`` if no such row.
       * ``first_non_null``: ``0.6`` (all-agree already returned ``1.0``).
       * ``source_priority``: ``max(0.1, 1.0 - idx * 0.1)`` for the chosen
         source's rank ``idx``; ``0.0`` when no priority source matches.

    No ``quality_weights`` are passed on the native path, so every
    quality-weighted tie branch in ``merge_field`` is unreachable here -- the
    order-tie constants (0.7 / 0.5 / etc.) are the live ones.

    Only the strategies ``_scalar_value_expr`` resolves are handled here; any
    other strategy raises ``NotImplementedError`` so the value expr and the
    confidence expr stay in lockstep (the Phase F gate routes the rest to the
    slow path).
    """
    strategy = rule.strategy
    nn = pl.col(col).drop_nulls()
    n_nn = nn.len()
    nuniq = nn.n_unique()

    # Strategy-specific confidence assuming we are PAST the all-agree branch
    # (>= 2 distinct non-null values).
    if strategy in ("most_complete", "longest_value"):
        tie_conf = 0.7 if strategy == "most_complete" else 0.5
        mask = pl.col(col).is_not_null()
        lengths = pl.col(len_alias).filter(mask)
        max_len = lengths.max()
        length_tie = (lengths == max_len).sum() > 1
        strat_conf = pl.when(length_tie).then(pl.lit(tie_conf)).otherwise(pl.lit(1.0))
    elif strategy == "first_non_null":
        strat_conf = pl.lit(0.6)
    elif strategy == "most_recent":
        date_col = rule.date_column
        elig = pl.col(col).is_not_null() & pl.col(date_col).is_not_null()
        dates = pl.col(date_col).filter(elig)
        n_elig = dates.len()
        top_date = dates.max()
        date_tie = (dates == top_date).sum() > 1
        strat_conf = (
            pl.when(n_elig == 0)
            .then(pl.lit(0.0))
            .when(date_tie)
            .then(pl.lit(0.5))
            .otherwise(pl.lit(1.0))
        )
    elif strategy == "source_priority":
        # The winning source is the lowest-rank source whose FIRST occurrence
        # (lowest __row_id__, or input order without one) is non-null -- exactly
        # the eligibility used by the value expr, via the SAME shared helper.
        # conf = max(0.1, 1.0 - idx*0.1) for that idx; 0.0 if no eligible source.
        src_rank, eligible = _source_priority_eligible(col, rule, has_row_id)
        winner_rank = src_rank.filter(eligible).min()
        n_elig = src_rank.filter(eligible).len()
        # max(0.1, 1.0 - idx*0.1), with idx = winner_rank.
        raw = pl.lit(1.0) - winner_rank.cast(pl.Float64) * 0.1
        floored = pl.max_horizontal(pl.lit(0.1), raw)
        strat_conf = pl.when(n_elig == 0).then(pl.lit(0.0)).otherwise(floored)
    else:
        raise NotImplementedError(
            f"survivorship scalar strategy {strategy!r} confidence not handled "
            "by the native path"
        )

    return (
        pl.when(n_nn == 0)
        .then(pl.lit(0.0))
        .when(nuniq <= 1)
        .then(pl.lit(1.0))
        .otherwise(strat_conf)
        .cast(pl.Float64)
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
    # One confidence per scalar unit (matches resolve_cluster appending `conf`
    # once per scalar field).
    agg_exprs.extend(
        _scalar_conf_expr(col, len_aliases.get(col), rules_by_col[col], has_row_id)
        .alias(_scalar_conf_name(col))
        for col in scalar_cols
    )
    return (
        prepped.group_by("__cluster_id__", maintain_order=True)
        .agg(agg_exprs)
    )


def _conditional_conf_name(col: str) -> str:
    """Internal per-cluster confidence column name for conditional ``col``
    (dropped before the final frame; only the row-wise mean survives)."""
    return f"__conf_cond_{col}__"


def _resolve_conditionals(multi_df, rules, resolved_frame, cond_cols, cond_order):
    """Resolve every list-form ``when:`` conditional column to one value +
    one confidence per cluster, byte-identical to ``resolve_cluster``'s
    conditional branch.

    Mirrors the slow path EXACTLY (the oracle):

    * The ``resolved`` dict a predicate reads holds already-RESOLVED WINNERS
      (group winners + scalar winners + earlier-resolved conditional winners),
      NOT raw candidate values -- exactly as ``resolve_cluster`` builds
      ``resolved[col] = v`` per resolution unit. We seed it from
      ``resolved_frame`` (the vectorized group + scalar winners, one row per
      cluster) and extend it as each conditional resolves, in ``cond_order``
      (the :func:`build_resolution_order` toposort restricted to conditionals).
    * ``select_conditional_strategy(rule_entry, resolved)`` (REUSED VERBATIM from
      conditions.py; it calls ``eval_predicate``) picks the first ``when:`` clause
      whose predicate holds, else the when-less default; ``or default_rule`` matches
      the slow path's ``... or default_rule`` guard.
    * The chosen clause's ``validate:`` pre-masks candidates via
      ``goldenflow_filter`` (the SAME validator the slow path filters with),
      then ``merge_field(values, chosen, sources=, dates=, ...)`` (the SAME
      function ``resolve_cluster`` calls) returns ``(value, confidence, idx)``.
      We use the value + confidence; ``idx`` (provenance) is unused on this
      provenance=False path.

    Returns ``(values_by_cluster, conf_by_cluster)`` -- two dicts keyed by
    ``__cluster_id__`` mapping to ``{col: value}`` / ``{col: confidence}`` -- so
    the caller can join them onto the frame and fold the conditional
    confidences into the ``__golden_confidence__`` mean.
    """
    from goldenmatch.config.schemas import GoldenFieldRule
    from goldenmatch.core.golden import merge_field

    default_rule = GoldenFieldRule(strategy=rules.default_strategy)
    has_source = "__source__" in multi_df.columns
    has_row_id = "__row_id__" in multi_df.columns

    # Sort once so a cluster's rows are in __row_id__ order (matches the slow
    # path's [__cluster_id__, __row_id__] presort, which makes merge_field's
    # first-occurrence / lowest-index tiebreaks land on the lowest __row_id__).
    if has_row_id:
        ordered = multi_df.sort(["__cluster_id__", "__row_id__"])
    else:
        ordered = multi_df

    # Per-conditional source frame: only the candidate columns + the when-/
    # strategy-referenced columns are needed. Materialize per cluster as Python
    # lists (the same shape merge_field consumes), partitioned by cluster.
    cond_clauses = {c: rules.field_rules[c] for c in cond_cols}

    # Columns we must materialize from multi_df: each conditional column's
    # candidates, plus any date_column a clause might use, plus __source__.
    needed_cols = set(cond_cols)
    for clauses in cond_clauses.values():
        for r in clauses:
            if r.date_column:
                needed_cols.add(r.date_column)
    needed_cols = [c for c in needed_cols if c in ordered.columns]

    # Resolved winners (group + scalar) per cluster, keyed by cluster_id.
    resolved_seed: dict = {}
    seed_cols = [c for c in resolved_frame.columns if c != "__cluster_id__"]
    for row in resolved_frame.iter_rows(named=True):
        cid = row["__cluster_id__"]
        resolved_seed[cid] = {c: row[c] for c in seed_cols}

    values_by_cluster: dict = {}
    conf_by_cluster: dict = {}

    for cid, sub in ordered.group_by("__cluster_id__", maintain_order=True):
        cluster_id = cid[0] if isinstance(cid, tuple) else cid
        col_arrays = {c: sub[c].to_list() for c in needed_cols}
        source_array = sub["__source__"].to_list() if has_source else None

        resolved = dict(resolved_seed.get(cluster_id, {}))
        cond_values: dict = {}
        cond_confs: dict = {}
        for col in cond_order:
            rule_entry = cond_clauses[col]
            # REUSED VERBATIM (conditions.py): first satisfied when:-clause, else
            # the when-less default; eval_predicate drives it. `or default_rule`
            # mirrors resolve_cluster.
            chosen = select_conditional_strategy(rule_entry, resolved) or default_rule
            values = list(col_arrays.get(col, [None] * sub.height))
            validator_name = getattr(chosen, "validate_with", None)
            if validator_name:
                values = goldenflow_filter(values, validator_name)
            sources = (
                source_array
                if (chosen.strategy == "source_priority" and source_array is not None)
                else None
            )
            dates = (
                col_arrays.get(chosen.date_column)
                if (chosen.strategy == "most_recent" and chosen.date_column in col_arrays)
                else None
            )
            # No quality_weights / pair_scores on the native provenance=False
            # path (resolve_cluster only passes them when provided; the native
            # path never is). merge_field is the SAME function the slow path
            # calls -- identical (value, confidence) by construction.
            val, conf, _ = merge_field(values, chosen, sources=sources, dates=dates)
            resolved[col] = val
            cond_values[col] = val
            cond_confs[col] = conf
        values_by_cluster[cluster_id] = cond_values
        conf_by_cluster[cluster_id] = cond_confs

    return values_by_cluster, conf_by_cluster


def build_survivorship_native(multi_df, rules) -> pl.DataFrame:
    """Vectorized survivorship (Phase B groups + Phase C scalars): one row per
    cluster carrying ``__cluster_id__`` + every resolved user column.

    Group columns are resolved in lock-step per ``field_groups`` (Phase B).
    Scalar columns (any user column NOT in a group, with a non-list rule) are
    resolved by their per-field ``GoldenFieldRule`` strategy, or
    ``default_strategy`` (Phase C). List-form ``when:`` conditional columns are
    resolved last, in a per-cluster pass over the already-resolved group/scalar
    winners (Phase E). Each unit resolves over the same source frame and is
    joined back on ``__cluster_id__``.

    Phase D: every unit (each group, each scalar field, each conditional field)
    also emits ONE internal per-cluster confidence column.
    ``__golden_confidence__`` is the row-wise mean over those columns --
    byte-identical to the slow path's flat mean over the per-unit
    ``confidences`` list (``resolve_cluster``: ``sum(confidences) /
    len(confidences)``). The internal per-unit columns are dropped so the emitted
    frame carries ONLY ``__cluster_id__`` + resolved value columns +
    ``__golden_confidence__`` (matching the oracle frame's column set).
    """
    result: pl.DataFrame | None = None
    # Map each unit to its internal per-cluster confidence column name. Keyed by
    # the SAME unit ids build_resolution_order uses ("group:<name>" / column) so
    # we can sum the confidences in the slow path's exact resolution order below
    # (Python float sum is order-sensitive; matching the order keeps the mean
    # bit-identical to resolve_cluster's sum(confidences)/len(confidences)).
    conf_name_by_unit: dict[str, str] = {}
    for g in rules.field_groups:
        resolved = _resolve_group(multi_df, g)
        conf_name_by_unit[f"group:{g.name}"] = _group_conf_name(g)
        if result is None:
            result = resolved
        else:
            # inner: every group resolves over the same source frame -> identical cluster set
            result = result.join(resolved, on="__cluster_id__", how="inner")

    # User columns not owned by any group split into plain scalars (non-list
    # rule) and conditional columns (list-form when:-rule, resolved in Phase E).
    grouped_cols = {c for g in rules.field_groups for c in g.columns}
    ungrouped_cols = [
        c for c in multi_df.columns
        if not _is_internal(c) and c != "__cluster_id__" and c not in grouped_cols
    ]
    cond_cols = [
        c for c in ungrouped_cols
        if isinstance(rules.field_rules.get(c), list)
    ]
    scalar_cols = [c for c in ungrouped_cols if c not in cond_cols]
    if scalar_cols:
        scalars = _resolve_scalars(multi_df, rules, scalar_cols)
        for c in scalar_cols:
            conf_name_by_unit[c] = _scalar_conf_name(c)
        if result is None:
            result = scalars
        else:
            result = result.join(scalars, on="__cluster_id__", how="inner")

    if result is None and not cond_cols:
        # No groups, no scalars, no conditionals: just the distinct cluster ids.
        # No units -> the slow path emits 0.0 (sum/len of an empty list).
        result = multi_df.select("__cluster_id__").unique(maintain_order=True)
        return result.with_columns(
            pl.lit(0.0, dtype=pl.Float64).alias("__golden_confidence__")
        )

    from goldenmatch.core.survivorship.conditions import build_resolution_order

    user_cols = [
        c for c in multi_df.columns
        if not _is_internal(c) and c != "__cluster_id__"
    ]
    order = build_resolution_order(rules.field_rules, rules.field_groups, user_cols)

    # Phase E: resolve conditional columns in toposort order over the already-
    # resolved group/scalar (and earlier-conditional) winners. resolved_frame
    # carries those winners (one row per cluster); when there are no groups and
    # no scalars it is just the distinct cluster ids (a conditional whose when:
    # only reads its own candidates needs no seed).
    if cond_cols:
        cond_order = [u for u in order if u in cond_cols]
        resolved_frame = (
            result if result is not None
            else multi_df.select("__cluster_id__").unique(maintain_order=True)
        )
        values_by_cluster, conf_by_cluster = _resolve_conditionals(
            multi_df, rules, resolved_frame, cond_cols, cond_order
        )
        # Build one row per cluster carrying the conditional values + their
        # confidences, then join onto result on __cluster_id__.
        cids = resolved_frame["__cluster_id__"].to_list()
        cond_frame_data: dict = {"__cluster_id__": cids}
        for col in cond_cols:
            cond_frame_data[col] = [values_by_cluster[cid][col] for cid in cids]
            conf_name = _conditional_conf_name(col)
            cond_frame_data[conf_name] = [conf_by_cluster[cid][col] for cid in cids]
            conf_name_by_unit[col] = conf_name
        # Preserve each value column's source dtype (lists of None would infer
        # to a null/object column otherwise).
        schema_overrides = {
            col: multi_df.schema[col] for col in cond_cols if col in multi_df.schema
        }
        cond_frame = pl.DataFrame(
            cond_frame_data,
            schema_overrides={**schema_overrides, "__cluster_id__": resolved_frame.schema["__cluster_id__"]},
        )
        if result is None:
            result = cond_frame
        else:
            result = result.join(cond_frame, on="__cluster_id__", how="inner")

    # __golden_confidence__ = sum(per-unit confidences) / unit_count, summed in
    # the slow path's resolution order via an explicit LEFT FOLD so the float
    # arithmetic is bit-identical to Python's sum() (left fold from 0.0).
    conf_cols = [conf_name_by_unit[u] for u in order if u in conf_name_by_unit]
    total = pl.lit(0.0, dtype=pl.Float64)
    for c in conf_cols:
        total = total + pl.col(c)
    result = result.with_columns(
        (total / pl.lit(float(len(conf_cols)))).alias("__golden_confidence__")
    )
    return result.drop(list(conf_name_by_unit.values()))
