"""Staged per-cluster survivorship resolution (Approach 1). Spec section 3.

Wraps the existing merge_field machinery: walks resolution units (scalar fields
and lock-step groups) in topological order so a `when:` predicate reads
already-resolved fields. Output dict shape matches build_golden_records_batch's
per-record dict so downstream consumers are unchanged.
"""
from __future__ import annotations

from goldenmatch.config.schemas import GoldenFieldRule
from goldenmatch.core.survivorship.conditions import select_conditional_strategy
from goldenmatch.core.survivorship.validate import goldenflow_filter
from goldenmatch.core.survivorship.winner import group_winner


def resolve_cluster(cluster_df, rules, resolution_order, *,
                    quality_scores=None, pair_scores=None, provenance=False):
    # Local import avoids any import cycle (golden imports resolve lazily).
    from goldenmatch.core.golden import (
        ClusterProvenance, FieldProvenance, GroupProvenance, _is_internal, merge_field,
    )

    user_cols = [c for c in cluster_df.columns if not _is_internal(c) and c != "__cluster_id__"]
    n = cluster_df.height
    col_arrays = {c: cluster_df[c].to_list() for c in user_cols}
    source_array = cluster_df["__source__"].to_list() if "__source__" in cluster_df.columns else None
    row_id_array = cluster_df["__row_id__"].to_list() if "__row_id__" in cluster_df.columns else None

    # Remap pair_scores (row-id keyed) to positional indices, like build_golden_records_batch.
    positional_pair_scores = None
    if pair_scores and row_id_array is not None:
        rid_to_pos = {rid: pos for pos, rid in enumerate(row_id_array)}
        positional_pair_scores = {}
        for (ra, rb), sc in pair_scores.items():
            pa, pb = rid_to_pos.get(ra), rid_to_pos.get(rb)
            if pa is not None and pb is not None:
                positional_pair_scores[(pa, pb)] = sc

    groups_by_unit = {f"group:{g.name}": g for g in rules.field_groups}
    default_rule = GoldenFieldRule(strategy=rules.default_strategy)

    resolved: dict = {}
    field_dicts: dict = {}
    field_provs: dict = {}
    group_provs: list = []
    confidences: list[float] = []
    date_arrays: dict = {}

    def _dates_for(date_col):
        if date_col and date_col in cluster_df.columns:
            if date_col not in date_arrays:
                date_arrays[date_col] = cluster_df[date_col].to_list()
            return date_arrays[date_col]
        return None

    for unit in resolution_order:
        if unit.startswith("group:"):
            g = groups_by_unit.get(unit)
            if g is None:
                continue
            dates = _dates_for(g.date_column) if g.strategy == "most_recent" else None
            rows = []
            for i in range(n):
                row = {"__pos__": i}
                for c in g.columns:
                    row[c] = col_arrays[c][i] if c in col_arrays else None
                if source_array is not None:
                    row["__source__"] = source_array[i]
                rows.append(row)
            res = group_winner(rows, list(g.columns), strategy=g.strategy,
                               source_priority=g.source_priority, dates=dates)
            wid = (row_id_array[res.winner_pos]
                   if (row_id_array is not None and res.winner_pos is not None and res.winner_pos >= 0)
                   else None)
            wsrc = (source_array[res.winner_pos]
                    if (source_array is not None and res.winner_pos is not None and res.winner_pos >= 0)
                    else None)
            for c in g.columns:
                v = res.values.get(c)
                fd = {"value": v, "confidence": res.confidence}
                if provenance:
                    fd["source_row_id"] = wid
                field_dicts[c] = fd
                resolved[c] = v
            confidences.append(res.confidence)
            if provenance:
                group_provs.append(GroupProvenance(
                    name=g.name, columns=list(g.columns), strategy=g.strategy,
                    winner_row_id=wid, winner_source=wsrc,
                    values=dict(res.values), tie=res.tie, confidence=res.confidence,
                ))
        else:
            col = unit
            if col not in col_arrays:
                continue
            rule_entry = rules.field_rules.get(col, default_rule)
            chosen = select_conditional_strategy(rule_entry, resolved) or default_rule
            values = list(col_arrays[col])
            dropped = 0
            validator_name = getattr(chosen, "validate_with", None)
            if validator_name:
                filtered = goldenflow_filter(values, validator_name)
                dropped = sum(1 for a, b in zip(values, filtered) if a is not None and b is None)
                values = filtered
            sources = source_array if (chosen.strategy == "source_priority" and source_array is not None) else None
            dates = _dates_for(chosen.date_column) if (chosen.strategy == "most_recent") else None
            weights = None
            if quality_scores is not None and row_id_array is not None:
                weights = [quality_scores.get((rid, col), 1.0) for rid in row_id_array]
            val, conf, idx = merge_field(values, chosen, sources=sources, dates=dates,
                                         quality_weights=weights, pair_scores=positional_pair_scores,
                                         cluster=cluster_df)
            src_row = row_id_array[idx] if (idx is not None and row_id_array is not None) else None
            fd = {"value": val, "confidence": conf}
            if provenance:
                fd["source_row_id"] = src_row
            field_dicts[col] = fd
            resolved[col] = val
            confidences.append(conf)
            if provenance:
                field_provs[col] = FieldProvenance(
                    value=val, source_row_id=src_row, strategy=chosen.strategy, confidence=conf,
                    condition=getattr(chosen, "when", None), validator=validator_name,
                    dropped_invalid=dropped,
                )

    # Assemble in ORIGINAL user-column order for stable output.
    result: dict = {}
    for col in user_cols:
        if col in field_dicts:
            result[col] = field_dicts[col]
    result["__golden_confidence__"] = sum(confidences) / len(confidences) if confidences else 0.0

    prov = None
    if provenance:
        prov = ClusterProvenance(cluster_id=-1, cluster_quality="strong",
                                 cluster_confidence=0.0, fields=field_provs, groups=group_provs)
    return result, prov
