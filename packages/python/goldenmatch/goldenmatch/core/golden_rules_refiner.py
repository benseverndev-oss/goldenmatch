"""Post-cluster golden-rules refinement (#golden-strategies, v1.18).

Runs between ``build_clusters`` and ``build_golden_records`` when
``GoldenRulesConfig.adaptive=True``. Reads cluster output + column
profiles and emits a refined ``GoldenRulesConfig`` with per-field
strategies informed by:

- Within-cluster value spread (high spread → ``confidence_majority``)
- Per-source completeness ranking (one source dominates → ``source_priority``)
- Date column inference (full timestamp coverage → ``most_recent``)
- ``col_type`` + ``avg_len`` (free-text + long → ``longest_value``)
- ``null_rate`` (mostly-NULL → ``first_non_null`` fast path)

Spec: ``docs/superpowers/specs/2026-05-22-intelligent-golden-rules-design.md``
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import polars as pl

    from goldenmatch.config.schemas import GoldenRulesConfig
    from goldenmatch.core.autoconfig import ColumnProfile

logger = logging.getLogger(__name__)


# Thresholds (env-overridable; defaults from spec).
# Spread = avg distinct values per cluster, for clusters with size >= 2.
HIGH_SPREAD_THRESHOLD = 2.0
FREE_TEXT_SPREAD_THRESHOLD = 1.5
FREE_TEXT_AVG_LEN_THRESHOLD = 20.0
HIGH_NULL_RATE_THRESHOLD = 0.5
SOURCE_DOMINANCE_THRESHOLD = 1.5  # top source must be > median * this


@dataclass(frozen=True)
class RefinementSignals:
    """Per-field signals computed from clusters + column profiles.

    ``within_cluster_spread[field]`` is the average distinct value count
    across multi-member clusters, for that field. 1.0 = unanimous;
    > 2 = high disagreement.

    ``per_source_completeness[field][source]`` is the non-null rate of
    ``field`` in rows tagged with ``source``. Computed only when the
    ``__source__`` column is present.

    ``date_column_coverage[field]`` is the fraction of multi-member
    clusters where every member has a non-null date value in ``field``.
    Used to detect timestamp-shaped fields suitable for ``most_recent``.

    ``col_type`` / ``avg_len`` / ``null_rate`` are carried forward from
    pre-cluster ``ColumnProfile``.
    """

    within_cluster_spread: dict[str, float]
    per_source_completeness: dict[str, dict[str, float]]
    date_column_coverage: dict[str, float]
    col_type: dict[str, str]
    avg_len: dict[str, float]
    null_rate: dict[str, float]


def compute_refinement_signals(
    clusters: dict[int, dict],
    prepared_df: pl.DataFrame,
    column_profiles: list[ColumnProfile],
) -> RefinementSignals:
    """Compute per-field signals from clusters + prepared frame.

    Cheap aggregations (one polars groupby per signal, no per-cluster
    loops in Python). Skipped fields default to neutral values so the
    rule table sees explicit zeros instead of KeyErrors.
    """
    import polars as pl

    # Build a member-id -> cluster-id map by expanding cluster members.
    cluster_id_per_row: dict[int, int] = {}
    multi_cluster_member_ids: list[int] = []
    for cid, info in clusters.items():
        members = info.get("members") or []
        if len(members) < 2:
            continue
        for m in members:
            cluster_id_per_row[m] = cid
            multi_cluster_member_ids.append(m)

    if not multi_cluster_member_ids:
        # No multi-member clusters -- every signal is neutral.
        empty_str: dict[str, float] = {}
        empty_src: dict[str, dict[str, float]] = {}
        return RefinementSignals(
            within_cluster_spread=empty_str,
            per_source_completeness=empty_src,
            date_column_coverage=empty_str,
            col_type={p.name: p.col_type for p in column_profiles},
            avg_len={p.name: float(p.avg_len) for p in column_profiles},
            null_rate={p.name: float(p.null_rate) for p in column_profiles},
        )

    # Filter prepared_df to multi-cluster members (eager; the set is
    # small relative to N) + attach cluster_id.
    if "__row_id__" in prepared_df.columns:
        ids_col = "__row_id__"
    else:
        prepared_df = prepared_df.with_row_index("__row_id__")
        ids_col = "__row_id__"

    cluster_id_series = pl.DataFrame({
        ids_col: list(cluster_id_per_row.keys()),
        "__cluster_id__": list(cluster_id_per_row.values()),
    })
    multi_df = prepared_df.join(cluster_id_series, on=ids_col, how="inner")

    user_cols = [
        c for c in multi_df.columns
        if not c.startswith("__") and c not in {ids_col, "__cluster_id__"}
    ]

    # Within-cluster spread: avg distinct values per cluster per field.
    within_cluster_spread: dict[str, float] = {}
    for col in user_cols:
        # n_unique per cluster, then mean across clusters.
        agg = (
            multi_df.lazy()
            .group_by("__cluster_id__")
            .agg(pl.col(col).n_unique().alias("__distinct__"))
            .select(pl.col("__distinct__").mean().alias("mean_distinct"))
            .collect()
        )
        mean_distinct = agg["mean_distinct"][0] if agg.height > 0 else 1.0
        within_cluster_spread[col] = float(mean_distinct or 1.0)

    # Per-source completeness: non-null rate per (source, field).
    per_source_completeness: dict[str, dict[str, float]] = {}
    if "__source__" in prepared_df.columns:
        for col in user_cols:
            per_source: dict[str, float] = {}
            agg = (
                prepared_df.lazy()
                .group_by("__source__")
                .agg(
                    pl.col(col).is_not_null().mean().alias("non_null_rate"),
                )
                .collect()
            )
            for row in agg.iter_rows(named=True):
                per_source[str(row["__source__"])] = float(row["non_null_rate"] or 0.0)
            if per_source:
                per_source_completeness[col] = per_source

    # Date-column coverage: fraction of multi-member clusters where
    # every member has a non-null value in this field AND the field's
    # dtype is a date/datetime/string-castable-to-date.
    date_column_coverage: dict[str, float] = {}
    profile_by_name = {p.name: p for p in column_profiles}
    for col in user_cols:
        p = profile_by_name.get(col)
        if p is None or p.col_type not in ("date",):
            continue
        # Count clusters where the field is non-null for every member.
        agg = (
            multi_df.lazy()
            .group_by("__cluster_id__")
            .agg(pl.col(col).is_not_null().all().alias("__all_present__"))
            .select(pl.col("__all_present__").mean().alias("coverage"))
            .collect()
        )
        if agg.height > 0:
            date_column_coverage[col] = float(agg["coverage"][0] or 0.0)

    return RefinementSignals(
        within_cluster_spread=within_cluster_spread,
        per_source_completeness=per_source_completeness,
        date_column_coverage=date_column_coverage,
        col_type={p.name: p.col_type for p in column_profiles},
        avg_len={p.name: float(p.avg_len) for p in column_profiles},
        null_rate={p.name: float(p.null_rate) for p in column_profiles},
    )


def _pick_strategy_for_field(
    field: str,
    signals: RefinementSignals,
) -> tuple[str, dict] | None:
    """Apply the rule table to one field; return (strategy_name, kwargs)
    or None to fall through to the base default.

    Rule order (first match wins) per
    ``docs/superpowers/specs/2026-05-22-intelligent-golden-rules-design.md``.
    """
    col_type = signals.col_type.get(field, "unknown")
    avg_len = signals.avg_len.get(field, 0.0)
    null_rate = signals.null_rate.get(field, 0.0)
    spread = signals.within_cluster_spread.get(field, 1.0)
    date_cov = signals.date_column_coverage.get(field, 0.0)

    # Rule 1: date column with full timestamp coverage.
    if col_type == "date" and date_cov > 0.5:
        return "most_recent", {"date_column": field}

    # Rule 2: source_priority when one source clearly dominates.
    per_source = signals.per_source_completeness.get(field)
    if per_source and len(per_source) >= 2:
        sorted_sources = sorted(
            per_source.items(), key=lambda kv: kv[1], reverse=True,
        )
        _top_source, top_rate = sorted_sources[0]
        # Median of all sources.
        rates = sorted([r for _, r in sorted_sources])
        median = rates[len(rates) // 2]
        if median > 0 and top_rate > median * SOURCE_DOMINANCE_THRESHOLD:
            ordered = [s for s, _ in sorted_sources]
            return "source_priority", {"source_priority": ordered}

    # Rule 3: long free-text field with disagreement -> longest_value.
    if (
        col_type in ("string", "address", "description")
        and avg_len > FREE_TEXT_AVG_LEN_THRESHOLD
        and spread > FREE_TEXT_SPREAD_THRESHOLD
    ):
        return "longest_value", {}

    # Rule 4: mostly-NULL field -> first_non_null fast path.
    if null_rate > HIGH_NULL_RATE_THRESHOLD:
        return "first_non_null", {}

    # Rule 5: high within-cluster disagreement -> confidence_majority.
    if spread > HIGH_SPREAD_THRESHOLD:
        return "confidence_majority", {}

    # Otherwise: defer to base rules' default.
    return None


def refine_golden_rules(
    base_rules: GoldenRulesConfig,
    clusters: dict[int, dict],
    prepared_df: pl.DataFrame,
    column_profiles: list[ColumnProfile],
) -> GoldenRulesConfig:
    """Refine ``base_rules`` based on cluster + column signals.

    Returns a NEW ``GoldenRulesConfig`` with ``field_rules`` populated.
    Does NOT mutate ``base_rules``. When ``base_rules.adaptive`` is
    False (default), returns ``base_rules`` unchanged.
    """
    from goldenmatch.config.schemas import GoldenFieldRule

    if not base_rules.adaptive:
        return base_rules

    signals = compute_refinement_signals(clusters, prepared_df, column_profiles)

    new_field_rules: dict[str, GoldenFieldRule] = dict(base_rules.field_rules)
    for field in signals.within_cluster_spread.keys():
        if field in new_field_rules:
            # Caller-provided rule wins; don't override.
            continue
        result = _pick_strategy_for_field(field, signals)
        if result is None:
            continue
        strategy, kwargs = result
        try:
            new_field_rules[field] = GoldenFieldRule(strategy=strategy, **kwargs)
            logger.info(
                "Refined golden rule: field=%r -> strategy=%s %s",
                field, strategy, kwargs or "",
            )
        except Exception as exc:  # pragma: no cover -- defensive
            logger.warning(
                "Refiner skipped field=%r strategy=%s: %s",
                field, strategy, exc,
            )

    refined = base_rules.model_copy(update={"field_rules": new_field_rules})
    return refined
