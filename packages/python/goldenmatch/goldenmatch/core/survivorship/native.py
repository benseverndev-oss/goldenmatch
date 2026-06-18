"""Vectorized survivorship resolution (provenance=False fast path).

Byte-identical to the slow per-cluster path (the oracle). Built up
strategy-by-strategy, each gated by a parity test in test_native_parity.py.
"""
from __future__ import annotations

import polars as pl


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


def build_survivorship_native(multi_df, rules) -> pl.DataFrame:
    """Vectorized group survivorship (Phase B): one row per cluster carrying
    ``__cluster_id__`` + the resolved group columns.

    Assumes every user column is a member of some ``field_groups`` group
    (scalar/conditional resolution is Phase C/E). Each group is resolved
    independently and joined back on ``__cluster_id__``.
    """
    result: pl.DataFrame | None = None
    for g in rules.field_groups:
        resolved = _resolve_group(multi_df, g)
        if result is None:
            result = resolved
        else:
            # inner: every group resolves over the same source frame -> identical cluster set
            result = result.join(resolved, on="__cluster_id__", how="inner")
    if result is None:
        # No groups: just the distinct cluster ids.
        result = multi_df.select("__cluster_id__").unique(maintain_order=True)
    return result
