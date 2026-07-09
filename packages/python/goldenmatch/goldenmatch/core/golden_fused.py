"""Fused Arrow-native golden-record production.

Turns a cluster map into golden records in one FFI call, holding intermediates
as Rust Vecs -- no wide multi_df, no per-cluster Python dicts. Byte-identical to
core/golden.build_golden_records_batch for the covered config surface; declines
loudly (returns None) for validator/plugin/LLM configs and for configs the
polars-native fast columnar path already handles. Design:
docs/superpowers/specs/2026-07-08-fused-golden-record-kernel-design.md
"""

from __future__ import annotations

from typing import Any

import polars as pl

from goldenmatch.config.schemas import GoldenFieldRule, GoldenRulesConfig

_GOLDEN_STRATEGY_IDS = {
    "most_complete": 0,
    "majority_vote": 1,
    "source_priority": 2,
    "most_recent": 3,
    "first_non_null": 4,
    "longest_value": 5,
    "unanimous_or_null": 6,
    "confidence_majority": 7,
}
_COVERED_STRATEGIES = frozenset(_GOLDEN_STRATEGY_IDS)


def _rule_covered(rule: GoldenFieldRule) -> bool:
    if rule.strategy not in _COVERED_STRATEGIES:
        return False  # custom:* and any unknown strategy
    if getattr(rule, "validate_with", None):
        return False
    # conditional predicate lowerability is checked in golden_fused_ready
    return True


def golden_fused_ready(rules: GoldenRulesConfig) -> bool:
    """True iff every effective strategy is covered, no validator/plugin/LLM,
    and every conditional predicate lowers to the kernel IR."""
    if getattr(rules, "use_llm_for_ambiguous", False):
        return False
    if rules.default_strategy not in _COVERED_STRATEGIES:
        return False
    # field_rules: each entry is a GoldenFieldRule or a list of them (conditional)
    for entry in rules.field_rules.values():
        clauses = entry if isinstance(entry, list) else [entry]
        for clause in clauses:
            if not _rule_covered(clause):
                return False
            # predicate lowerability wired in Stage 6; until then decline list-form.
            if getattr(clause, "when", None) is not None:
                from goldenmatch.core.golden_fused_predicate import predicate_lowerable

                if not predicate_lowerable(clause.when):
                    return False
    for group in rules.field_groups:
        if group.strategy not in _COVERED_STRATEGIES and group.strategy not in {"anchor"}:
            return False
    if rules.cluster_overrides:
        for overrides in rules.cluster_overrides.values():
            for rule in overrides.values():
                if not _rule_covered(rule):
                    return False
    return True


# ─── internal-column detection (mirror core/golden._is_internal) ─────────────


def _is_internal(col: str) -> bool:
    from goldenmatch.core.golden import _is_internal as _gi

    return _gi(col)


def _native_golden_symbol() -> Any | None:
    # Narrow catch (cf. fused_match._match_fused_symbol): a missing/unbuilt
    # extension is an ImportError; a not-yet-published symbol is an
    # AttributeError. Anything else is a real loader bug and should surface, not
    # degrade to a silent decline.
    try:
        from goldenmatch.core._native_loader import native_module

        return getattr(native_module(), "golden_fused", None)
    except (ImportError, AttributeError):
        return None


def _gather_with_nulls(series: pl.Series, idx: list[int]) -> pl.Series:
    """Gather ``series`` at positional ``idx`` (per output cluster), mapping the
    ``-1`` sentinel to null while preserving the source column's native dtype."""
    idx_s = pl.Series("__idx__", idx, dtype=pl.Int64)
    safe = idx_s.clip(lower_bound=0)  # -1 -> 0 (masked out below)
    gathered = series.gather(safe)
    keep = idx_s >= 0
    tmp = pl.DataFrame({"__g__": gathered, "__keep__": keep})
    return tmp.select(
        pl.when(pl.col("__keep__")).then(pl.col("__g__")).otherwise(None).alias(series.name)
    ).to_series()


def run_golden_fused_arrow(
    columns: Any,  # pl.DataFrame (cluster frame with __row_id__ + __cluster_id__)
    config: GoldenRulesConfig,
    quality_scores: dict[tuple[int, str], float] | None = None,
    cluster_pair_scores: dict[int, dict[tuple[int, int], float]] | None = None,
    provenance: bool = False,
) -> pl.DataFrame | None:
    """Build golden records for a cluster frame via the fused Arrow kernel.

    Returns a golden ``pl.DataFrame`` (one row per multi-member cluster: every
    user column at its native dtype + ``__cluster_id__`` + ``__golden_confidence__``)
    or ``None`` to decline -- when the config is not covered
    (`golden_fused_ready` False), when the config+`quality_scores` would route
    the reference to the polars-native fast columnar path
    (`golden.py::_polars_native_eligible`), or when the native kernel is absent.
    The caller then falls back to `build_golden_records_batch`.

    Stage 0 covers the `most_complete` strategy only; remaining strategies /
    features return `None` today until their stage lands (they still pass the
    gate, so this is a temporary decline, not the final boundary).
    """
    from goldenmatch.core.golden import _polars_native_eligible

    rules = config
    if not golden_fused_ready(rules):
        return None
    # Fast-path decline: the polars-native columnar path already has no capacity
    # win to capture AND approximates most_complete confidence -- reuse its own
    # gate (which has quality_scores in scope) rather than re-deriving.
    if _polars_native_eligible(rules, quality_scores):
        return None

    fn = _native_golden_symbol()
    if fn is None:
        return None

    df = columns
    if "__cluster_id__" not in df.columns:
        return None

    # Spec 4.3: within-cluster members must be __row_id__-ascending for the
    # kernel's first-occurrence tie-breaks to match the reference.
    sort_cols = ["__cluster_id__", "__row_id__"] if "__row_id__" in df.columns else ["__cluster_id__"]
    sdf = df.sort(sort_cols)

    # Drop singletons (size <= 1), mirroring _multi_df_from_frames' size > 1
    # filter. A plain cluster frame carries no oversized flag, so ~oversized is
    # trivially satisfied here. The window filter preserves the sort order.
    sdf = sdf.filter(pl.len().over("__cluster_id__") > 1)

    user_cols = [c for c in sdf.columns if not _is_internal(c) and c != "__cluster_id__"]

    if sdf.height == 0 or not user_cols:
        # Nothing to build -- an empty golden frame with the expected columns.
        empty = {c: pl.Series(c, [], dtype=sdf[c].dtype) for c in user_cols}
        empty["__cluster_id__"] = pl.Series("__cluster_id__", [], dtype=pl.Int64)
        empty["__golden_confidence__"] = pl.Series("__golden_confidence__", [], dtype=pl.Float64)
        return pl.DataFrame(empty)

    # Per-column effective strategy (default or field_rule). Stage 5/6/7 extend
    # this with groups / conditionals / cluster overrides.
    strategy_ids: list[int] = []
    covered = True
    for c in user_cols:
        rule = rules.field_rules.get(c)
        strat = rule.strategy if isinstance(rule, GoldenFieldRule) else rules.default_strategy
        # Stage 0: only most_complete is implemented in the kernel.
        if strat != "most_complete":
            covered = False
        strategy_ids.append(_GOLDEN_STRATEGY_IDS[strat])
    if not covered:
        return None  # temporary decline until the strategy's stage lands

    import pyarrow as pa

    n = sdf.height
    if "__row_id__" in sdf.columns:
        row_ids_arr = sdf.get_column("__row_id__").cast(pl.Int64).to_arrow()
    else:
        row_ids_arr = pa.array(range(n), type=pa.int64())
    cluster_ids_arr = sdf.get_column("__cluster_id__").cast(pl.Int64).to_arrow()
    text_cols = [sdf.get_column(c).cast(pl.Utf8).to_arrow() for c in user_cols]
    code_cols: list[Any] = []  # Stage 1 wires factorization codes

    winner_idx, field_conf, cluster_ids_out = fn(
        row_ids_arr,
        cluster_ids_arr,
        len(user_cols),
        strategy_ids,
        text_cols,
        code_cols,
    )

    out: dict[str, pl.Series] = {}
    for ci, c in enumerate(user_cols):
        out[c] = _gather_with_nulls(sdf.get_column(c), list(winner_idx[ci]))

    result = pl.DataFrame(out)
    result = result.with_columns(
        pl.Series("__cluster_id__", list(cluster_ids_out), dtype=pl.Int64)
    )

    # __golden_confidence__ = mean of per-field confidences over columns.
    n_clusters = len(cluster_ids_out)
    n_cols = len(user_cols)
    # n_cols >= 1 here (the empty-user_cols case returned early above).
    gconf = [
        sum(field_conf[ci][k] for ci in range(n_cols)) / n_cols
        for k in range(n_clusters)
    ]
    result = result.with_columns(pl.Series("__golden_confidence__", gconf, dtype=pl.Float64))
    return result
