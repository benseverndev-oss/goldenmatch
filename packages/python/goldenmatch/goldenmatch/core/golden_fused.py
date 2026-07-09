"""Fused Arrow-native golden-record production.

Turns a cluster map into golden records in one FFI call, holding intermediates
as Rust Vecs -- no wide multi_df, no per-cluster Python dicts. Byte-identical to
core/golden.build_golden_records_batch for the covered config surface; declines
loudly (returns None) for validator/plugin/LLM configs and for configs the
polars-native fast columnar path already handles. Design:
docs/superpowers/specs/2026-07-08-fused-golden-record-kernel-design.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

# Strategies the kernel implements as pure-scalar branches (needing only the
# per-column text/code keys, no extra gathered column).
_KERNEL_SCALAR_STRATEGIES = frozenset(
    {"most_complete", "majority_vote", "first_non_null", "longest_value", "unanimous_or_null"}
)

# Stage 2 strategies: they need extra gathered columns (source_priority needs the
# factorized __source__ + the priority list; most_recent needs a date i64 + null
# mask).
_KERNEL_STAGE2_STRATEGIES = frozenset({"source_priority", "most_recent"})

# Stage 4: confidence_majority needs per-cluster pair-score edges (a side channel,
# not an extra gathered column).
_KERNEL_STAGE4_STRATEGIES = frozenset({"confidence_majority"})

# Every strategy the kernel can dispatch today.
_KERNEL_COVERED_STRATEGIES = (
    _KERNEL_SCALAR_STRATEGIES | _KERNEL_STAGE2_STRATEGIES | _KERNEL_STAGE4_STRATEGIES
)

# polars dtypes whose physical i64 repr is order-preserving vs the Python object
# comparison the reference `_most_recent` does (`sort(key=date, reverse=True)`).
# Temporal dtypes (Date days, Datetime us, ...) and integer dtypes map to a
# monotonic i64 via `.to_physical()`; string/float date columns do NOT (lexical
# vs numeric ordering diverges), so the fused path declines them.
_MOST_RECENT_ORDER_SAFE_DTYPES = (
    pl.Date,
    pl.Datetime,
    pl.Time,
    pl.Duration,
    pl.Int8,
    pl.Int16,
    pl.Int32,
    pl.Int64,
    pl.UInt8,
    pl.UInt16,
    pl.UInt32,
    # NOTE: pl.UInt64 is intentionally excluded -- its cast to Int64 wraps for
    # values >= 2**63, which would silently flip the date ordering vs the
    # reference's Python-int compare. Such a column declines to the classic path.
)


def _factorize_with_map(values: list) -> tuple[list[int], dict]:
    """Like ``_factorize_codes`` but also returns the ``raw value -> code`` map.

    Used to translate a ``source_priority`` list (raw source strings) into the
    same code space as the factorized ``__source__`` column. A priority source
    NOT present in the column has no entry, so callers map it to a negative
    "absent" sentinel the kernel skips.
    """
    codes: list[int] = []
    mapping: dict = {}
    for v in values:
        if v is None:
            codes.append(-1)
            continue
        c = mapping.get(v)
        if c is None:
            c = len(mapping)
            mapping[v] = c
        codes.append(c)
    return codes, mapping


def _factorize_codes(values: list) -> list[int]:
    """Map raw values to integer codes in first-occurrence order.

    Keyed by the raw Python value (``==``/``hash``), so ``1`` and ``1.0``
    (equal + same hash) collapse to one code, matching the reference's
    ``set(v)`` / ``Counter(v)`` grouping in ``core/golden.py`` (:82/:153/:246).
    ``None`` -> ``-1`` (the null sentinel the kernel reads). This is the
    byte-identical grouping key for ``majority_vote`` / ``unanimous_or_null``
    and the universal short-circuit (raw-value equality, NOT text equality).

    The null-> -1 / first-occurrence contract lives ONCE, in
    ``_factorize_with_map`` -- this drops the map half.
    """
    return _factorize_with_map(values)[0]


@dataclass
class _GoldenFusedSideChannels:
    """Per-column side channels handed to the ``golden_fused`` kernel as ONE
    carrier arg (a Rust ``#[derive(FromPyObject)]`` struct reads these attrs).

    Consolidating the side channels here keeps the FFI call's positional arity
    flat: each later stage (qweights, pair scores, group specs, predicate IR,
    cluster-override codes) adds ONE field + ONE assignment, not another pair of
    positional args aligned across the Python marshal site and the Rust
    destructure. All per-column lists are ``n_output_cols`` long (a placeholder
    entry for columns that don't use the channel); ``source_code`` is a single
    shared Arrow column (empty when no source_priority column exists).
    """

    source_code: Any  # pa.Int64Array (len n_rows) or empty placeholder
    priority_codes: list[list[int]] = field(default_factory=list)
    date_cols: list[Any] = field(default_factory=list)  # per-col pa.Int64Array
    date_null_masks: list[Any] = field(default_factory=list)  # per-col pa.Int64Array
    # Stage 3: per-column pa.Float64Array quality weights (len n_rows) aligned to
    # the sorted frame -- populated (every column) ONLY when quality_scores is not
    # None; an empty array per column signals the kernel's unweighted branch.
    qweights: list[Any] = field(default_factory=list)
    # Stage 4: confidence_majority per-cluster pair-score edges, flattened GLOBALLY
    # to `(cluster_id, a_local, b_local, score)` tuples. Positions are LOCAL to the
    # cluster's sorted span (0-based); the kernel buckets by `cluster_id` (so no
    # Python-side span-order prediction) and preserves per-cluster INSERTION ORDER,
    # which is the incoming `pair_scores.items()` order -- load-bearing for the
    # representative index (set on the FIRST agreeing edge; spec 6.4). Empty when
    # no confidence_majority column is present or no pair scores were supplied.
    pair_edges: list[tuple[int, int, int, float]] = field(default_factory=list)


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
    if (idx_s >= 0).all():
        # Common case (no null winner): a plain gather is the cheapest path and
        # preserves the source dtype exactly. The when/then below handles the
        # null-sentinel case and DOES round-trip Object columns fine -- this is a
        # fast/simple shortcut, not a correctness workaround.
        return series.gather(idx_s)
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

    # Per-column effective strategy + rule (default or field_rule). Stage 5/6/7
    # extend this with groups / conditionals / cluster overrides.
    strategy_ids: list[int] = []
    effective_rules: list[GoldenFieldRule] = []
    default_rule = GoldenFieldRule(strategy=rules.default_strategy)
    for c in user_cols:
        rule = rules.field_rules.get(c)
        rule = rule if isinstance(rule, GoldenFieldRule) else default_rule
        strat = rule.strategy
        # Kernel branches covered today: scalar strategies + source_priority /
        # most_recent (Stage 2). confidence_majority lands in Stage 4 -- decline.
        if strat not in _KERNEL_COVERED_STRATEGIES:
            return None  # temporary decline until the strategy's stage lands
        strategy_ids.append(_GOLDEN_STRATEGY_IDS[strat])
        effective_rules.append(rule)

    import pyarrow as pa

    n = sdf.height
    if "__row_id__" in sdf.columns:
        row_ids_arr = sdf.get_column("__row_id__").cast(pl.Int64).to_arrow()
    else:
        row_ids_arr = pa.array(range(n), type=pa.int64())
    cluster_ids_arr = sdf.get_column("__cluster_id__").cast(pl.Int64).to_arrow()

    # Build the comparable keys per column from the RAW Python values:
    #   text = Python `str(v)` (byte-identical to the reference's `str(v)`;
    #          polars' Utf8 cast can format numbers/dates differently),
    #   code = `_factorize_codes(v)` (raw-value equality, the grouping key for
    #          the short-circuit / majority / unanimous).
    # Codes are passed for EVERY column (the universal short-circuit needs them),
    # text only matters for most_complete/longest_value but is cheap to carry.
    text_cols: list[Any] = []
    code_cols: list[Any] = []
    for c in user_cols:
        values = sdf.get_column(c).to_list()
        text = [None if v is None else str(v) for v in values]
        codes = _factorize_codes(values)
        text_cols.append(pa.array(text, type=pa.string()))
        code_cols.append(pa.array(codes, type=pa.int64()))

    # ── Stage 2: source_priority + most_recent extra keys ────────────────────
    empty_i64 = pa.array([], type=pa.int64())

    # source_code: factorized __source__ (shared across all columns). Only built
    # when some column uses source_priority; if that column is configured but no
    # __source__ is present, the reference would raise -- decline instead.
    priority_codes: list[list[int]] = [[] for _ in user_cols]
    source_code_arr: Any = empty_i64
    if any(r.strategy == "source_priority" for r in effective_rules):
        if "__source__" not in sdf.columns:
            return None
        src_values = sdf.get_column("__source__").to_list()
        src_codes, src_map = _factorize_with_map(src_values)
        source_code_arr = pa.array(src_codes, type=pa.int64())
        for ci, rule in enumerate(effective_rules):
            if rule.strategy == "source_priority":
                # A priority source absent from the column -> negative sentinel
                # the kernel skips (never collides with a >=0 real source code
                # nor the -1 null-source group, both excluded by the < 0 guard).
                priority_codes[ci] = [src_map.get(s, -1) for s in (rule.source_priority or [])]

    # most_recent: per-column date i64 (order-preserving physical) + null mask
    # (1 = null-date). Non-most_recent columns get empty arrays.
    date_cols: list[Any] = [empty_i64 for _ in user_cols]
    date_null_masks: list[Any] = [empty_i64 for _ in user_cols]
    for ci, rule in enumerate(effective_rules):
        if rule.strategy != "most_recent":
            continue
        date_col = rule.date_column
        # Reference passes dates only when the date column is present; absent ->
        # merge_field raises. Decline so the caller falls back cleanly.
        if not date_col or date_col not in sdf.columns:
            return None
        date_series = sdf.get_column(date_col)
        if not isinstance(date_series.dtype, _MOST_RECENT_ORDER_SAFE_DTYPES):
            # Non-order-preserving physical repr (e.g. string / float dates):
            # can't guarantee byte-parity with the reference's object compare.
            return None
        phys = date_series.to_physical().cast(pl.Int64)
        phys_list = phys.to_list()
        date_vals = [0 if x is None else int(x) for x in phys_list]
        mask_vals = [1 if x is None else 0 for x in phys_list]
        date_cols[ci] = pa.array(date_vals, type=pa.int64())
        date_null_masks[ci] = pa.array(mask_vals, type=pa.int64())

    # ── Stage 3: per-column quality weights ──────────────────────────────────
    # Mirror the reference (resolve.py:124 / golden.py:999): per column, the
    # weight for a sorted-frame row is quality_scores.get((row_id, col), 1.0).
    # Present (every column, even all-1.0) ONLY when quality_scores is not None,
    # so the reference is off the fast path (_polars_native_eligible False) and
    # runs merge_field's `quality_weights is not None` branch. When None, empty
    # arrays => the kernel's unweighted branch => byte-identical to Stages 0-2.
    empty_f64 = pa.array([], type=pa.float64())
    qweights: list[Any] = [empty_f64 for _ in user_cols]
    if quality_scores is not None:
        if "__row_id__" in sdf.columns:
            row_ids_list = sdf.get_column("__row_id__").to_list()
        else:
            row_ids_list = list(range(n))
        for ci, c in enumerate(user_cols):
            w = [float(quality_scores.get((rid, c), 1.0)) for rid in row_ids_list]
            qweights[ci] = pa.array(w, type=pa.float64())

    # ── Stage 4: confidence_majority per-cluster pair-score edges ─────────────
    # Mirror build_golden_records_batch (golden.py:969-980): per cluster, remap
    # the row-id-keyed edges to LOCAL positions within that cluster's sorted span
    # via a rid->pos map, dropping edges whose endpoints aren't both in the span.
    # Preserve each cluster's `items()` iteration order (spec 6.4 -- the kernel's
    # representative index is set on the first agreeing edge in that order). An
    # absent cluster / falsy edge dict leaves the cluster with no edges, so the
    # kernel falls back to majority_vote -- matching the reference's None
    # positional_pair_scores.
    pair_edges: list[tuple[int, int, int, float]] = []
    if (
        cluster_pair_scores
        and "__row_id__" in sdf.columns
        and any(r.strategy == "confidence_majority" for r in effective_rules)
    ):
        cid_list = sdf.get_column("__cluster_id__").to_list()
        rid_list = sdf.get_column("__row_id__").to_list()
        pos = 0
        n_local = len(cid_list)
        while pos < n_local:
            cid = cid_list[pos]
            start = pos
            while pos < n_local and cid_list[pos] == cid:
                pos += 1
            cluster_scores = cluster_pair_scores.get(int(cid))
            if cluster_scores:
                rid_to_pos = {rid: p for p, rid in enumerate(rid_list[start:pos])}
                for (rid_a, rid_b), score in cluster_scores.items():
                    pa = rid_to_pos.get(rid_a)
                    pb = rid_to_pos.get(rid_b)
                    if pa is not None and pb is not None:
                        pair_edges.append((int(cid), pa, pb, float(score)))

    side = _GoldenFusedSideChannels(
        source_code=source_code_arr,
        priority_codes=priority_codes,
        date_cols=date_cols,
        date_null_masks=date_null_masks,
        qweights=qweights,
        pair_edges=pair_edges,
    )
    winner_idx, field_conf, cluster_ids_out = fn(
        row_ids_arr,
        cluster_ids_arr,
        len(user_cols),
        strategy_ids,
        text_cols,
        code_cols,
        side,
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
