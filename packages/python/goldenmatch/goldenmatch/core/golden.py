"""Golden record builder with per-field merge strategies."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

import polars as pl

from goldenmatch.config.schemas import GoldenFieldRule, GoldenRulesConfig


@dataclass
class FieldProvenance:
    value: Any
    source_row_id: int
    strategy: str
    confidence: float
    candidates: list[dict] = dataclass_field(default_factory=list)


@dataclass
class ClusterProvenance:
    cluster_id: int
    cluster_quality: str
    cluster_confidence: float
    fields: dict[str, FieldProvenance] = dataclass_field(default_factory=dict)


@dataclass
class GoldenRecordResult:
    df: pl.DataFrame
    provenance: list[ClusterProvenance] = dataclass_field(default_factory=list)

# Columns to skip when building golden records
_INTERNAL_PREFIXES = ("__row_id__", "__source__", "__block_key__", "__mk_")


def _is_internal(col: str) -> bool:
    return any(col.startswith(p) for p in _INTERNAL_PREFIXES) or col == "__mk_"


def merge_field(
    values: list,
    rule: GoldenFieldRule,
    sources: list[str] | None = None,
    dates: list | None = None,
    quality_weights: list[float] | None = None,
) -> tuple[object, float, int | None]:
    """Merge a list of values using the given rule's strategy.

    Returns (value, confidence, source_index) where source_index is the
    index into the values list that the winning value came from.
    """
    non_null = [(i, v) for i, v in enumerate(values) if v is not None]

    if not non_null:
        return (None, 0.0, None)

    # If all non-null values are identical, return with confidence 1.0
    unique_vals = set(v for _, v in non_null)
    if len(unique_vals) == 1:
        return (non_null[0][1], 1.0, non_null[0][0])

    strategy = rule.strategy

    if strategy == "most_complete":
        return _most_complete(non_null, quality_weights)
    elif strategy == "majority_vote":
        return _majority_vote(non_null, quality_weights)
    elif strategy == "source_priority":
        return _source_priority(values, rule, sources)
    elif strategy == "most_recent":
        return _most_recent(values, dates)
    elif strategy == "first_non_null":
        return _first_non_null(non_null, quality_weights)
    else:
        raise ValueError(f"Unknown strategy: {strategy}")


def _most_complete(non_null: list[tuple[int, object]], quality_weights: list[float] | None = None) -> tuple:
    str_vals = [(i, str(v), v) for i, v in non_null]
    max_len = max(len(s) for _, s, _ in str_vals)
    longest = [(i, s, v) for i, s, v in str_vals if len(s) == max_len]
    if len(longest) == 1:
        return (longest[0][2], 1.0, longest[0][0])
    # Tie-break by quality weight if available
    if quality_weights is not None:
        best = max(longest, key=lambda x: quality_weights[x[0]] if x[0] < len(quality_weights) else 1.0)
        conf = min(1.0, 0.7 * quality_weights[best[0]]) if best[0] < len(quality_weights) else 0.7
        return (best[2], conf, best[0])
    return (longest[0][2], 0.7, longest[0][0])


def _majority_vote(non_null: list[tuple[int, object]], quality_weights: list[float] | None = None) -> tuple:
    if quality_weights is not None:
        # Weighted vote: sum quality weights per value
        value_weights: dict[object, float] = {}
        value_idx: dict[object, int] = {}
        for i, v in non_null:
            w = quality_weights[i] if i < len(quality_weights) else 1.0
            value_weights[v] = value_weights.get(v, 0.0) + w
            if v not in value_idx:
                value_idx[v] = i
        winner = max(value_weights, key=value_weights.__getitem__)
        total_weight = sum(value_weights.values())
        conf = value_weights[winner] / total_weight if total_weight > 0 else 0.0
        return (winner, conf, value_idx[winner])
    counts = Counter(v for _, v in non_null)
    winner, count = counts.most_common(1)[0]
    total = len(non_null)
    # Find the index of the first occurrence of the winner
    winner_idx = next(i for i, v in non_null if v == winner)
    return (winner, count / total, winner_idx)


def _source_priority(
    values: list,
    rule: GoldenFieldRule,
    sources: list[str] | None,
) -> tuple:
    if sources is None:
        raise ValueError("source_priority strategy requires sources list")
    source_val = {}
    source_idx = {}
    for i, (src, val) in enumerate(zip(sources, values)):
        if src not in source_val:
            source_val[src] = val
            source_idx[src] = i

    for idx, src in enumerate(rule.source_priority):
        val = source_val.get(src)
        if val is not None:
            conf = max(0.1, 1.0 - idx * 0.1)
            return (val, conf, source_idx[src])

    # Fallback: no match found in priority list
    return (None, 0.0, None)


def _most_recent(values: list, dates: list | None) -> tuple:
    if dates is None:
        raise ValueError("most_recent strategy requires dates list")
    indexed_pairs = [(i, d, v) for i, (d, v) in enumerate(zip(dates, values)) if v is not None and d is not None]
    if not indexed_pairs:
        return (None, 0.0, None)
    indexed_pairs.sort(key=lambda x: x[1], reverse=True)
    top_date = indexed_pairs[0][1]
    tied = [p for p in indexed_pairs if p[1] == top_date]
    conf = 1.0 if len(tied) == 1 else 0.5
    return (indexed_pairs[0][2], conf, indexed_pairs[0][0])


def _first_non_null(non_null: list[tuple[int, object]], quality_weights: list[float] | None = None) -> tuple:
    if quality_weights is not None:
        # Pick the non-null value with the highest quality weight
        best = max(non_null, key=lambda x: quality_weights[x[0]] if x[0] < len(quality_weights) else 1.0)
        return (best[1], 0.6, best[0])
    return (non_null[0][1], 0.6, non_null[0][0])


def _build_golden_records_polars_native(
    multi_df: pl.DataFrame,
    rules: GoldenRulesConfig,
    user_cols: list[str],
) -> list[dict]:
    """Polars-native fast path for the common simple-strategy case.

    Eligible when ``default_strategy`` is ``most_complete`` or
    ``first_non_null``, no per-field rules, and no quality_scores. Computes
    every cluster's winner value + confidence via Polars group_by aggregates
    -- no per-cluster Python merge_field call.

    Confidence simplification vs ``merge_field``:
      - all non-null values identical: 1.0 (preserved)
      - else most_complete: 0.7 (approximation -- the original gives 1.0
        when the longest value is unique among non-nulls. The
        approximation always returns 0.7 when there's more than one
        distinct non-null value, regardless of length uniqueness. For
        the typo-variant workloads this rarely matters; for callers that
        need exact confidence semantics, set field_rules so the slow
        path triggers.)
      - first_non_null: 0.6 (matches merge_field's _first_non_null when
        no quality_weights)
      - all values null: skipped (matches merge_field)
    """
    strategy = rules.default_strategy
    if strategy == "most_complete":
        # For each user col compute (winner_value, all_same_flag) per cluster.
        # winner is the non-null value with the longest string length;
        # ties broken by row order (Polars stable sort).
        len_col_aliases = {col: f"__len_{col}__" for col in user_cols}
        prepped = multi_df.with_columns([
            pl.col(col).cast(pl.Utf8).str.len_chars().alias(alias)
            for col, alias in len_col_aliases.items()
        ])
        agg_exprs: list = []
        for col in user_cols:
            # sort_by(descending=True).first() picks the longest non-null
            # value, matching the prior top_k_by(by=..., k=1, reverse=False)
            # intent. top_k_by mis-binds args on newer Polars where
            # 'reverse' is no longer a kwarg; sort_by is stable across
            # Polars 0.20+. See #362.
            agg_exprs.append(
                pl.col(col).filter(pl.col(col).is_not_null())
                .sort_by(len_col_aliases[col], descending=True)
                .first().alias(f"__val_{col}__")
            )
            agg_exprs.append(
                pl.col(col).drop_nulls().n_unique().alias(f"__nuniq_{col}__")
            )
        agg = (
            prepped.group_by("__cluster_id__", maintain_order=True)
            .agg(agg_exprs)
        )
    elif strategy == "first_non_null":
        agg_exprs = []
        for col in user_cols:
            agg_exprs.append(
                pl.col(col).drop_nulls().first().alias(f"__val_{col}__")
            )
            agg_exprs.append(
                pl.col(col).drop_nulls().n_unique().alias(f"__nuniq_{col}__")
            )
        agg = (
            multi_df.group_by("__cluster_id__", maintain_order=True)
            .agg(agg_exprs)
        )
    else:
        raise ValueError(f"polars-native path does not handle strategy {strategy!r}")

    # Stream the agg in 500K-cluster batches so peak Python-list footprint
    # caps regardless of cluster count. At 25M with 16.6M clusters the
    # non-streamed path materialised ~10 GB of Python strings + dicts
    # simultaneously, pushing combined RSS (cluster dict + golden) past
    # the 64 GB ceiling. With streaming, per-batch peak is
    # BATCH_SIZE * n_user_cols * mean_string_size which is bounded.
    BATCH_SIZE = 500_000
    same_strategy_conf = 1.0  # when all same
    diff_strategy_conf = 0.7 if strategy == "most_complete" else 0.6
    n_clusters = agg.height

    results: list[dict] = []
    for batch_start in range(0, n_clusters, BATCH_SIZE):
        batch = agg.slice(batch_start, BATCH_SIZE)
        cluster_ids = batch["__cluster_id__"].to_list()
        val_arrays = {col: batch[f"__val_{col}__"].to_list() for col in user_cols}
        nuniq_arrays = {col: batch[f"__nuniq_{col}__"].to_list() for col in user_cols}
        for i, cid in enumerate(cluster_ids):
            result: dict = {}
            confidences: list[float] = []
            for col in user_cols:
                val = val_arrays[col][i]
                nuniq = nuniq_arrays[col][i] or 0
                if val is None:
                    conf = 0.0
                elif nuniq <= 1:
                    conf = same_strategy_conf
                else:
                    conf = diff_strategy_conf
                result[col] = {"value": val, "confidence": conf}
                confidences.append(conf)
            result["__golden_confidence__"] = (
                sum(confidences) / len(confidences) if confidences else 0.0
            )
            result["__cluster_id__"] = cid
            results.append(result)
    return results


def _polars_native_eligible(
    rules: GoldenRulesConfig,
    quality_scores: dict[tuple[int, str], float] | None,
) -> bool:
    """Gate the polars-native fast path. See docstring on
    ``_build_golden_records_polars_native`` for the confidence
    approximation.
    """
    if quality_scores is not None:
        return False
    if rules.default_strategy not in ("most_complete", "first_non_null"):
        return False
    if rules.field_rules:
        return False
    return True


def build_golden_records_batch(
    multi_df: Any,  # pl.DataFrame | ray.data.Dataset (Phase 4)
    rules: GoldenRulesConfig,
    quality_scores: dict[tuple[int, str], float] | None = None,
) -> list[dict]:
    """Vectorized batch builder for many golden records sharing a parent df.

    Same per-record output as ``build_golden_record`` but the parent df is
    sorted by ``__cluster_id__`` once and each user column is pulled to a
    Python list ONCE for the whole frame. At 5M scale with 1.67M clusters,
    this collapses ~6.7M per-cluster ``cluster_df[col].to_list()`` round-
    trips into ~4 (one per user column). Measured drop: golden stage went
    from 307s to ~30s on the 5M Linux bench.

    Fast path: when ``rules`` uses a simple uniform strategy (no field
    rules, no quality_scores, default_strategy in {most_complete,
    first_non_null}) the entire compute is one Polars group_by per
    column -- 6.7M merge_field calls collapse to 4. Confidence is
    approximated (see _build_golden_records_polars_native docstring).

    Phase 4: when multi_df is a Ray Dataset, dispatch to the distributed
    golden path via build_golden_records_smart. If quality_scores are also
    set, collect to driver first (quality_scores dict cannot round-trip
    across Ray workers).

    Args:
        multi_df: DataFrame containing rows from every multi-member cluster,
            with a ``__cluster_id__`` column. Will be sorted by that column.
            May also be a ray.data.Dataset (Phase 4 distributed path).
        rules: golden rules configuration.
        quality_scores: optional per-(row_id, col) quality weights.

    Returns:
        List of golden records (same dict shape as ``build_golden_record``),
        in ascending ``__cluster_id__`` order. Each result has its
        ``__cluster_id__`` field set.
    """
    # Phase 4: distributed path when multi_df is a Ray Dataset.
    from goldenmatch.distributed import is_ray_dataset
    if is_ray_dataset(multi_df):
        if quality_scores:
            import logging

            import pyarrow as pa
            logging.getLogger(__name__).info(
                "build_golden_records_batch: quality_scores set on Ray Dataset "
                "input; collecting to driver for in-memory build.",
            )
            tables = list(multi_df.iter_batches(batch_format="pyarrow"))
            multi_df = pl.from_arrow(pa.concat_tables(tables)) if tables else pl.DataFrame()
        else:
            from goldenmatch.distributed.golden import build_golden_records_smart
            user_columns = [
                c for c in multi_df.schema().names
                if not c.startswith("__")
            ]
            return build_golden_records_smart(
                multi_df, rules, user_columns=user_columns,
            )

    if "__cluster_id__" not in multi_df.columns:
        raise ValueError("multi_df must contain __cluster_id__ column")
    if multi_df.height == 0:
        return []

    if _polars_native_eligible(rules, quality_scores):
        user_cols = [
            c for c in multi_df.columns
            if not _is_internal(c) and c != "__cluster_id__"
        ]
        if user_cols:
            return _build_golden_records_polars_native(multi_df, rules, user_cols)

    sorted_df = multi_df.sort("__cluster_id__")
    sizes = (
        sorted_df.lazy()
        .group_by("__cluster_id__", maintain_order=True)
        .agg(pl.len().alias("__size__"))
        .collect()
    )
    cluster_ids = sizes["__cluster_id__"].to_list()
    size_list = sizes["__size__"].to_list()

    user_cols = [c for c in sorted_df.columns if not _is_internal(c) and c != "__cluster_id__"]
    col_arrays: dict[str, list] = {col: sorted_df[col].to_list() for col in user_cols}
    has_source = "__source__" in sorted_df.columns
    source_array = sorted_df["__source__"].to_list() if has_source else None
    has_row_id = "__row_id__" in sorted_df.columns
    row_id_array = sorted_df["__row_id__"].to_list() if has_row_id else None

    # Lazy-load date columns when actually needed by a rule. Most workloads
    # use the default ``most_complete`` strategy and never reach this branch.
    date_arrays: dict[str, list] = {}

    default_rule = GoldenFieldRule(strategy=rules.default_strategy)

    results: list[dict] = []
    offset = 0
    for cid, size in zip(cluster_ids, size_list):
        result: dict = {}
        confidences: list[float] = []
        for col in user_cols:
            values = col_arrays[col][offset:offset + size]
            field_rule = rules.field_rules.get(col, default_rule)

            sources = None
            dates = None
            weights = None
            if field_rule.strategy == "source_priority" and source_array is not None:
                sources = source_array[offset:offset + size]
            elif field_rule.strategy == "most_recent" and field_rule.date_column:
                date_col = field_rule.date_column
                if date_col in sorted_df.columns:
                    if date_col not in date_arrays:
                        date_arrays[date_col] = sorted_df[date_col].to_list()
                    dates = date_arrays[date_col][offset:offset + size]
            if quality_scores is not None and row_id_array is not None:
                weights = [
                    quality_scores.get((rid, col), 1.0)
                    for rid in row_id_array[offset:offset + size]
                ]

            val, conf, _idx = merge_field(
                values, field_rule, sources=sources, dates=dates, quality_weights=weights,
            )
            result[col] = {"value": val, "confidence": conf}
            confidences.append(conf)

        result["__golden_confidence__"] = (
            sum(confidences) / len(confidences) if confidences else 0.0
        )
        result["__cluster_id__"] = cid
        results.append(result)
        offset += size

    return results


def build_golden_record(
    cluster_df: pl.DataFrame,
    rules: GoldenRulesConfig,
    quality_scores: dict[tuple[int, str], float] | None = None,
) -> dict:
    """Build a golden record from a cluster DataFrame.

    Returns dict of {col: {"value": v, "confidence": c}, ...,
    "__golden_confidence__": mean_of_confidences}.
    """
    result = {}
    confidences = []
    row_ids = cluster_df["__row_id__"].to_list() if "__row_id__" in cluster_df.columns else None

    for col in cluster_df.columns:
        if _is_internal(col):
            continue

        values = cluster_df[col].to_list()

        # Look up field rule or build default
        if col in rules.field_rules:
            field_rule = rules.field_rules[col]
        else:
            field_rule = GoldenFieldRule(strategy=rules.default_strategy)

        # Gather optional lists
        sources = None
        dates = None
        weights = None
        if field_rule.strategy == "source_priority" and "__source__" in cluster_df.columns:
            sources = cluster_df["__source__"].to_list()
        if field_rule.strategy == "most_recent" and field_rule.date_column:
            if field_rule.date_column in cluster_df.columns:
                dates = cluster_df[field_rule.date_column].to_list()
        if quality_scores is not None and row_ids is not None:
            weights = [quality_scores.get((rid, col), 1.0) for rid in row_ids]

        val, conf, _idx = merge_field(values, field_rule, sources=sources, dates=dates, quality_weights=weights)
        result[col] = {"value": val, "confidence": conf}
        confidences.append(conf)

    if confidences:
        result["__golden_confidence__"] = sum(confidences) / len(confidences)
    else:
        result["__golden_confidence__"] = 0.0

    return result


def build_golden_record_with_provenance(
    df: pl.DataFrame,
    rules: GoldenRulesConfig,
    clusters: dict[int, dict],
    quality_scores: dict[tuple[int, str], float] | None = None,
) -> GoldenRecordResult:
    """Build golden records with field-level provenance tracking."""
    golden_rows = []
    provenance_list = []

    cluster_col = "__cluster_id__"
    if cluster_col not in df.columns:
        # Single cluster case
        cluster_ids = [1]
        cluster_dfs = {1: df}
    else:
        cluster_ids = sorted(df[cluster_col].unique().to_list())
        cluster_dfs = {cid: df.filter(pl.col(cluster_col) == cid) for cid in cluster_ids}

    for cid in cluster_ids:
        cluster_df = cluster_dfs[cid]
        cinfo = clusters.get(cid, {})
        row_ids = cluster_df["__row_id__"].to_list() if "__row_id__" in cluster_df.columns else list(range(len(cluster_df)))

        # Build golden record + provenance in a single pass (no double merge_field call)
        field_provenance = {}
        golden_row = {"__cluster_id__": cid}
        confidences = []

        for col in cluster_df.columns:
            if _is_internal(col):
                continue
            values = cluster_df[col].to_list()
            if col in rules.field_rules:
                field_rule = rules.field_rules[col]
            else:
                field_rule = GoldenFieldRule(strategy=rules.default_strategy)

            sources = None
            dates = None
            weights = None
            if field_rule.strategy == "source_priority" and "__source__" in cluster_df.columns:
                sources = cluster_df["__source__"].to_list()
            if field_rule.strategy == "most_recent" and field_rule.date_column:
                if field_rule.date_column in cluster_df.columns:
                    dates = cluster_df[field_rule.date_column].to_list()
            if quality_scores is not None and row_ids:
                weights = [quality_scores.get((rid, col), 1.0) for rid in row_ids]

            val, conf, src_idx = merge_field(values, field_rule, sources=sources, dates=dates, quality_weights=weights)
            confidences.append(conf)

            source_row_id = row_ids[src_idx] if src_idx is not None and src_idx < len(row_ids) else row_ids[0]

            candidates = []
            for rid, v in zip(row_ids, values):
                q = quality_scores.get((rid, col), 1.0) if quality_scores else 1.0
                candidates.append({"row_id": rid, "value": v, "quality": q})

            field_provenance[col] = FieldProvenance(
                value=val,
                source_row_id=source_row_id,
                strategy=field_rule.strategy,
                confidence=conf,
                candidates=candidates,
            )
            golden_row[col] = val

        golden_row["__golden_confidence__"] = sum(confidences) / len(confidences) if confidences else 0.0
        golden_rows.append(golden_row)

        provenance_list.append(ClusterProvenance(
            cluster_id=cid,
            cluster_quality=cinfo.get("cluster_quality", "strong"),
            cluster_confidence=cinfo.get("confidence", 0.0),
            fields=field_provenance,
        ))

    golden_df = pl.DataFrame(golden_rows) if golden_rows else pl.DataFrame()
    return GoldenRecordResult(df=golden_df, provenance=provenance_list)
