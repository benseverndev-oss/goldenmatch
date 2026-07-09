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

# Stage 5: field_groups. Group strategies form their OWN small enum (shared with
# the Rust `GROUP_*` consts) -- distinct from `_GOLDEN_STRATEGY_IDS` because the
# group ranking (winner.py::group_winner) is NOT the scalar merge_field dispatch
# (e.g. group `most_complete` ranks by populated-count over the group columns and
# derives one lock-step confidence, unlike scalar `most_complete`). `anchor` is a
# group-only strategy with no scalar counterpart.
_GROUP_STRATEGY_IDS = {
    "most_complete": 0,
    "source_priority": 1,
    "most_recent": 2,
    "anchor": 3,
}

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
class _GoldenFusedGroupSpec:
    """One field_group's kernel spec (a Rust ``#[derive(FromPyObject)]`` struct
    reads these attrs). Groups resolve as a UNIT: the kernel ranks the cluster's
    rows once per group (winner.py::group_winner), pins ONE winner row across all
    the group's columns (or per-column back-fill indices under ``allow_fill``),
    and emits ONE confidence for the group -- so a group spec is per-group, not
    per-column (like ``pair_edges`` is per-cluster).

    - ``col_indices``: indices into the output columns (into ``text_cols`` /
      ``code_cols``) for this group's columns, in the group's declared order.
    - ``strategy``: a ``_GROUP_STRATEGY_IDS`` code.
    - ``priority_codes``: for ``source_priority`` -- the group's source list
      mapped into the shared ``source_code`` space (absent sources encoded < 0);
      empty otherwise.
    - ``date_col`` / ``date_null_mask``: for ``most_recent`` -- Int64 arrays
      (len n_rows), mask 1 = null-date; empty placeholders otherwise.
    - ``anchor_col_index``: for ``anchor`` -- the output-column index of the
      anchor column (the kernel reads its code array for anchor-presence); -1
      otherwise.
    - ``allow_fill``: per-column back-fill of null group cells from the next-best
      ranked row (winner.py:65).
    """

    col_indices: list[int]
    strategy: int
    priority_codes: list[int] = field(default_factory=list)
    date_col: Any = None  # pa.Int64Array (len n_rows) or empty placeholder
    date_null_mask: Any = None  # pa.Int64Array (len n_rows) or empty placeholder
    anchor_col_index: int = -1
    allow_fill: bool = False


@dataclass
class _GoldenFusedClause:
    """One conditional clause (Stage 6): a lowered ``when:`` predicate (RPN IR, a
    list of ``golden_fused_predicate.PredInstr``) + the scalar strategy id to
    apply when it holds. A Rust ``#[derive(FromPyObject)]`` struct reads ``ir`` /
    ``strategy``."""

    ir: list  # list[golden_fused_predicate.PredInstr]
    strategy: int


@dataclass
class _GoldenFusedConditional:
    """One conditional (list-form) field_rule (Stage 6): the ordered non-default
    clauses + the when-less default strategy id, for the output column
    ``col_index``. The kernel evaluates each clause's IR against the resolved
    winner codes (in resolution order) and applies the first holding clause's
    strategy, else ``default_strategy`` -- mirroring
    ``conditions.select_conditional_strategy``. A Rust ``#[derive(FromPyObject)]``
    struct reads these attrs."""

    col_index: int
    clauses: list  # list[_GoldenFusedClause]
    default_strategy: int


@dataclass
class _GoldenFusedResolutionPlan:
    """The kernel's RESOLUTION PLAN -- control-flow, not columnar data. Where the
    side-channels carrier holds per-column value arrays (text/code/source/date/
    weights/pair-edges/group-specs), this sub-struct holds the CONTROL flow that
    decides which strategy each scalar column resolves under, and in what order.
    Nested on ``_GoldenFusedSideChannels.resolution_plan`` (a Rust
    ``#[derive(FromPyObject)]`` ``ResolutionPlan`` reads these attrs) so the two
    concerns don't intermix as later stages (e.g. provenance) touch the carrier.

    - ``col_order`` (Stage 6): scalar-column resolution order (non-group
      output-column indices, in build_resolution_order topological order -- covers
      EVERY non-group column exactly once, so a conditional's referenced columns
      resolve first). Order is immaterial for the mutually-independent
      non-conditional columns (Stages 0-5).
    - ``conditionals`` (Stage 6): the conditional (list-form field_rule) specs, in
      resolution order. Empty => no list-form field_rules (Stages 0-5 behavior).
    - ``overrides`` (Stage 7): per-(cluster, col) strategy overrides, flattened to
      ``(cluster_id, col_index, strategy_code)`` tuples. The kernel buckets by
      ``cluster_id`` and, when resolving a scalar column for that cluster,
      dispatches the override strategy instead of the column's base
      ``strategy_ids`` entry (a column with NO override keeps its base strategy).
      Empty when no cluster_overrides apply -- which, matching the reference
      EXACTLY, is whenever cluster_overrides is unset OR survivorship is active
      (see ``run_golden_fused_arrow`` for the precedence rationale). The extra
      value channels an override strategy needs (source_code / date) are built via
      the shared per-column ``candidate_rules`` fold, so only the strategy code
      lives here.
    """

    col_order: list[int] = field(default_factory=list)
    conditionals: list[_GoldenFusedConditional] = field(default_factory=list)
    overrides: list[tuple[int, int, int]] = field(default_factory=list)


@dataclass
class _GoldenFusedSideChannels:
    """Per-column side channels handed to the ``golden_fused`` kernel as ONE
    carrier arg (a Rust ``#[derive(FromPyObject)]`` struct reads these attrs).

    Consolidating the side channels here keeps the FFI call's positional arity
    flat: each later stage (qweights, pair scores, group specs) adds ONE field +
    ONE assignment, not another pair of positional args aligned across the Python
    marshal site and the Rust destructure. The fields split into two concerns:
    per-column VALUE channels (below) vs the CONTROL-flow ``resolution_plan``
    sub-struct (`col_order` / `conditionals` / `overrides`). All per-column lists
    are ``n_output_cols`` long (a placeholder entry for columns that don't use the
    channel); ``source_code`` is a single shared Arrow column (empty when no
    source_priority column exists).
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
    # Stage 5: per-group specs (one entry per rules.field_groups, group-detection
    # order). The kernel resolves each group as a unit and returns a per-group
    # confidence (folded into the cluster mean ONCE), plus the winner index for
    # every group column. Empty when no field_groups are configured.
    group_specs: list[_GoldenFusedGroupSpec] = field(default_factory=list)
    # Stage 6/7: the CONTROL-flow resolution plan (col_order + conditionals +
    # cluster overrides) -- separated from the value channels above so provenance
    # (Stage 8) and later work touch one concern at a time.
    resolution_plan: _GoldenFusedResolutionPlan = field(
        default_factory=_GoldenFusedResolutionPlan
    )


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


def _build_date_arrays(sdf: pl.DataFrame, date_col: str | None) -> tuple[Any, Any] | None:
    """Build the ``(date_i64, null_mask_i64)`` Arrow arrays for a ``most_recent``
    date column, or ``None`` to decline. Shared by the scalar and group
    ``most_recent`` paths. Declines when the column is absent (the reference's
    ``merge_field`` would raise) or its physical repr is not order-preserving vs
    the reference's Python-object date compare (``_MOST_RECENT_ORDER_SAFE_DTYPES``).
    """
    import pyarrow as pa

    if not date_col or date_col not in sdf.columns:
        return None
    date_series = sdf.get_column(date_col)
    if not isinstance(date_series.dtype, _MOST_RECENT_ORDER_SAFE_DTYPES):
        return None
    phys_list = date_series.to_physical().cast(pl.Int64).to_list()
    date_vals = [0 if x is None else int(x) for x in phys_list]
    mask_vals = [1 if x is None else 0 for x in phys_list]
    return pa.array(date_vals, type=pa.int64()), pa.array(mask_vals, type=pa.int64())


def _build_provenance_records(
    *,
    sdf: pl.DataFrame,
    n: int,
    user_cols: list[str],
    out: dict[str, pl.Series],
    winner_idx: Any,
    field_conf: Any,
    group_conf: Any,
    group_col_lists: list[list[int]],
    cluster_ids_out: Any,
    gconf: list[float],
) -> list[dict]:
    """Assemble Stage 8 per-field provenance records from the kernel outputs.

    Returns records byte-identical (at the field level) to
    ``build_golden_records_batch(provenance=True)``: each user-col field dict
    carries ``{value, confidence, source_row_id}``, plus ``__cluster_id__`` /
    ``__golden_confidence__``. ``source_row_id`` = the SORTED frame's ``__row_id__``
    at the kernel's ``winner_idx`` (or ``None`` when ``winner_idx = -1``, matching
    ``merge_field``'s ``idx=None``) -- derivable Python-side from data the kernel
    already returns, no kernel change. A group column's ``winner_idx`` already
    reflects the group winner row (or the per-column FILLED row under
    ``allow_fill``), and the kernel pins the winner position (not ``-1``) for a
    null-pinned group cell, so ``source_row_id`` = the winner/filled id, matching
    resolve.py's ``filled_ids.get(c, wid)``. Per-column confidence: a grouped
    column takes its GROUP's single confidence (the kernel writes ``field_conf=0.0``
    for grouped cols and the real value into ``group_conf``), a scalar column its
    own ``field_conf`` -- mirroring resolve_cluster, which stamps ``res.confidence``
    on every group column's field dict.
    """
    if "__row_id__" in sdf.columns:
        row_ids_sorted = sdf.get_column("__row_id__").cast(pl.Int64).to_list()
    else:
        row_ids_sorted = list(range(n))
    # col index -> owning group index, from the group_col_lists already built for
    # the kernel call (no rebuild from group_specs).
    col_to_group: dict[int, int] = {}
    for g_idx, cols in enumerate(group_col_lists):
        for ci in cols:
            col_to_group[ci] = g_idx
    col_value_lists = {c: out[c].to_list() for c in user_cols}
    records: list[dict] = []
    for k in range(len(cluster_ids_out)):
        rec: dict = {}
        for ci, c in enumerate(user_cols):
            wi = winner_idx[ci][k]
            src_row = row_ids_sorted[wi] if wi is not None and wi >= 0 else None
            if ci in col_to_group:
                conf = group_conf[col_to_group[ci]][k]
            else:
                conf = field_conf[ci][k]
            rec[c] = {
                "value": col_value_lists[c][k],
                "confidence": conf,
                "source_row_id": src_row,
            }
        rec["__golden_confidence__"] = gconf[k]
        rec["__cluster_id__"] = cluster_ids_out[k]
        records.append(rec)
    return records


def run_golden_fused_arrow(
    columns: Any,  # pl.DataFrame (cluster frame with __row_id__ + __cluster_id__)
    config: GoldenRulesConfig,
    quality_scores: dict[tuple[int, str], float] | None = None,
    cluster_pair_scores: dict[int, dict[tuple[int, int], float]] | None = None,
    provenance: bool = False,
) -> pl.DataFrame | tuple[pl.DataFrame, list[dict]] | None:
    """Build golden records for a cluster frame via the fused Arrow kernel.

    Returns (one row per multi-member cluster: every user column at its native
    dtype + ``__cluster_id__`` + ``__golden_confidence__``):

    - ``provenance=False`` (default): a golden ``pl.DataFrame``.
    - ``provenance=True``: a ``(golden_df, records)`` tuple, mirroring
      ``build_golden_records_from_frames``'s ``(df, list[dict])`` shape. ``records``
      is byte-identical at the FIELD level to
      ``build_golden_records_batch(..., provenance=True)`` -- each user-column field
      dict carries ``{value, confidence, source_row_id}`` alongside
      ``__cluster_id__`` / ``__golden_confidence__``. ``source_row_id`` is the
      winning record's ``__row_id__`` (``None`` when the field is all-null / has no
      winner). NOTE: it does NOT reproduce the slow path's ``__survivorship_prov__``
      ``ClusterProvenance`` object (per-field ``source_row_id`` is the derivable,
      kernel-change-free provenance surface; the full object carries a group ``tie``
      flag and a conditional's fired-clause strategy that the kernel doesn't emit).

    Returns ``None`` to decline (in BOTH provenance modes) -- when the config is
    not covered (`golden_fused_ready` False), when the config+`quality_scores`
    would route the reference to the polars-native fast columnar path
    (`golden.py::_polars_native_eligible`), or when the native kernel is absent.
    The caller then falls back to `build_golden_records_batch`.
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
        empty_df = pl.DataFrame(empty)
        return (empty_df, []) if provenance else empty_df

    # ── Stage 5: identify group-owned columns (resolved as a unit, NOT via the
    # per-column scalar dispatch). Every group column must be a present user
    # column -- decline otherwise (the reference tolerates a missing group column
    # as all-null + emits an extra output column; the fused path gathers on frame
    # columns only, so an off-frame group column has no home here).
    col_index = {c: i for i, c in enumerate(user_cols)}
    grouped_col_idx: set[int] = set()
    group_col_lists: list[list[int]] = []
    for g in rules.field_groups:
        try:
            gcols = [col_index[c] for c in g.columns]
        except KeyError:
            return None
        group_col_lists.append(gcols)
        grouped_col_idx.update(gcols)

    # Per-column effective strategy(-ies). Group-owned columns get a benign
    # most_complete placeholder (their winner + confidence come from the group
    # pass). A plain column has one rule; a CONDITIONAL (list-form) field_rule has
    # a list of clauses -- `conditional_cols[ci]` records the clause list and the
    # kernel picks a clause via IR eval (Stage 6). `candidate_rules[ci]` is every
    # rule whose extra channels (source/date) must be built for that column (one
    # for a plain column; all clauses for a conditional), so the right channel is
    # present whichever clause fires. Stage 7 extends this with cluster_overrides.
    # Reconstruct the default rule verbatim from `default_strategy`, mirroring
    # build_golden_records_batch (golden.py:948). RAISE-parity is intentional: a
    # bare `most_recent`/`source_priority` default (no date_column/source_priority)
    # makes the reference raise in merge_field; here it makes the shared
    # source/date channel builders return None (decline) so the caller falls back
    # to the reference, which then raises the SAME error. Do NOT "fix" this into a
    # silent None/most_complete coercion -- that would mask the reference's raise.
    default_rule = GoldenFieldRule(strategy=rules.default_strategy)
    strategy_ids: list[int] = []
    candidate_rules: list[list[GoldenFieldRule]] = []
    conditional_cols: dict[int, list[GoldenFieldRule]] = {}
    for ci, c in enumerate(user_cols):
        if ci in grouped_col_idx:
            strategy_ids.append(_GOLDEN_STRATEGY_IDS["most_complete"])
            candidate_rules.append([])  # resolved by the group pass; no scalar channels
            continue
        entry = rules.field_rules.get(c)
        if isinstance(entry, list):
            # Conditional field_rule: every clause's strategy must be a covered
            # kernel scalar branch. The when-less default's strategy is the
            # placeholder strategy_id (the kernel ignores it once cond_index is set,
            # but keep it sane).
            for clause in entry:
                if clause.strategy not in _KERNEL_COVERED_STRATEGIES:
                    return None
            default_clause = next((r for r in entry if r.when is None), None)
            if default_clause is None:
                return None  # schema guarantees exactly one; decline defensively
            conditional_cols[ci] = list(entry)
            strategy_ids.append(_GOLDEN_STRATEGY_IDS[default_clause.strategy])
            candidate_rules.append(list(entry))
            continue
        rule = entry if isinstance(entry, GoldenFieldRule) else default_rule
        strat = rule.strategy
        # Kernel branches covered today: scalar strategies + source_priority /
        # most_recent (Stage 2) + confidence_majority (Stage 4).
        if strat not in _KERNEL_COVERED_STRATEGIES:
            return None  # temporary decline until the strategy's stage lands
        strategy_ids.append(_GOLDEN_STRATEGY_IDS[strat])
        candidate_rules.append([rule])

    # ── Stage 7: cluster_overrides. Precedence matched to the reference EXACTLY.
    # `cluster_overrides` is honored by build_golden_records_batch ONLY on the
    # classic slow path (golden.py:981-987, `per_cluster[col]` REPLACES the
    # column's base field_rule for that cluster) -- and that path is reached only
    # when survivorship is INACTIVE (no field_groups, no conditional/list-form
    # field_rules). When survivorship IS active the reference routes through
    # resolve_cluster, which NEVER reads cluster_overrides (it walks
    # field_rules/groups/default), so the overrides are silently ignored. So:
    # build the override channel ONLY when survivorship is inactive; otherwise
    # leave it empty and let the fused group/conditional passes resolve normally
    # (also ignoring the overrides) -- byte-identical to the reference either way.
    from goldenmatch.core.golden import _survivorship_active

    override_specs: list[tuple[int, int, int]] = []
    if rules.cluster_overrides and not _survivorship_active(rules):
        for ov_cid, per_col in rules.cluster_overrides.items():
            for ov_col, ov_rule in per_col.items():
                ci = col_index.get(ov_col)
                if ci is None:
                    # Override targets a column not in user_cols. The reference's
                    # classic loop iterates user_cols only, so `per_cluster[col]`
                    # for a non-user column is never consulted -> a no-op there.
                    # Skip it here to match (no divergence).
                    continue
                if ov_rule.strategy not in _KERNEL_COVERED_STRATEGIES:
                    return None  # temporary decline until the strategy's stage lands
                # Fold the override rule into candidate_rules[ci] so the shared
                # source_priority / most_recent channel builders below construct
                # the channel this override's strategy needs (and decline on a
                # per-column channel conflict, e.g. two clusters overriding the
                # same column to source_priority with different priority lists).
                candidate_rules[ci].append(ov_rule)
                override_specs.append(
                    (int(ov_cid), ci, _GOLDEN_STRATEGY_IDS[ov_rule.strategy])
                )

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
    # Per-column raw value -> code map (Stage 6): reused by the conditional
    # predicate lowering's `code_of` so a `when:` literal resolves to the SAME
    # code space as the referenced column's factorized winner value.
    col_maps: dict[str, dict] = {}
    for c in user_cols:
        values = sdf.get_column(c).to_list()
        text = [None if v is None else str(v) for v in values]
        codes, vmap = _factorize_with_map(values)
        col_maps[c] = vmap
        text_cols.append(pa.array(text, type=pa.string()))
        code_cols.append(pa.array(codes, type=pa.int64()))

    # ── Stage 2: source_priority + most_recent extra keys ────────────────────
    empty_i64 = pa.array([], type=pa.int64())

    # source_code: factorized __source__ (shared across all columns AND groups).
    # Built when some scalar column OR some field_group uses source_priority; if
    # so configured but no __source__ is present, the reference would raise --
    # decline instead. `src_map` (raw source -> code) is reused below to map the
    # group source_priority lists into the same code space.
    priority_codes: list[list[int]] = [[] for _ in user_cols]
    source_code_arr: Any = empty_i64
    src_map: dict = {}
    need_source = any(
        r.strategy == "source_priority" for rlist in candidate_rules for r in rlist
    ) or any(g.strategy == "source_priority" for g in rules.field_groups)
    if need_source:
        if "__source__" not in sdf.columns:
            return None
        src_values = sdf.get_column("__source__").to_list()
        src_codes, src_map = _factorize_with_map(src_values)
        source_code_arr = pa.array(src_codes, type=pa.int64())
        for ci, rlist in enumerate(candidate_rules):
            sp_lists = [r.source_priority or [] for r in rlist if r.strategy == "source_priority"]
            if not sp_lists:
                continue
            # A conditional column may hold >1 source_priority clause but only ONE
            # per-column priority channel exists -- decline on conflicting lists.
            if any(sp != sp_lists[0] for sp in sp_lists[1:]):
                return None
            # A priority source absent from the column -> negative sentinel the
            # kernel skips (never collides with a >=0 real source code nor the -1
            # null-source group, both excluded by the < 0 guard).
            priority_codes[ci] = [src_map.get(s, -1) for s in sp_lists[0]]

    # most_recent: per-column date i64 (order-preserving physical) + null mask
    # (1 = null-date). Non-most_recent columns get empty arrays. A conditional
    # column may hold >1 most_recent clause but only ONE per-column date channel
    # exists -- decline on conflicting date columns.
    date_cols: list[Any] = [empty_i64 for _ in user_cols]
    date_null_masks: list[Any] = [empty_i64 for _ in user_cols]
    for ci, rlist in enumerate(candidate_rules):
        mr = [r for r in rlist if r.strategy == "most_recent"]
        if not mr:
            continue
        date_columns = {r.date_column for r in mr}
        if len(date_columns) != 1:
            return None
        built = _build_date_arrays(sdf, mr[0].date_column)
        if built is None:
            # Absent (merge_field would raise) or non-order-preserving physical
            # repr (string/float dates): decline so the caller falls back.
            return None
        date_cols[ci], date_null_masks[ci] = built

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
        and any(r.strategy == "confidence_majority" for rlist in candidate_rules for r in rlist)
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
                    # NB: not `pa`/`pb` -- those would shadow the `pyarrow as pa`
                    # alias used throughout this function.
                    pos_a = rid_to_pos.get(rid_a)
                    pos_b = rid_to_pos.get(rid_b)
                    if pos_a is not None and pos_b is not None:
                        pair_edges.append((int(cid), pos_a, pos_b, float(score)))

    # ── Stage 5: per-group specs. A group resolves as a UNIT (winner.py::
    # group_winner): one winner row pinned across all group columns (or per-column
    # back-fill under allow_fill), and ONE confidence folded into the cluster mean.
    group_specs: list[_GoldenFusedGroupSpec] = []
    for g, gcols in zip(rules.field_groups, group_col_lists):
        gstrat = _GROUP_STRATEGY_IDS[g.strategy]
        gspec_priority: list[int] = []
        gspec_date: Any = empty_i64
        gspec_date_mask: Any = empty_i64
        gspec_anchor_idx = -1
        if g.strategy == "source_priority":
            # src_map is populated (need_source True since a group uses it).
            # NOTE (known divergence, out-of-contract): a DUPLICATE-bearing
            # priority list is un-byte-matchable because the reference's own two
            # paths disagree -- the scalar `_source_priority` returns on the FIRST
            # matching index, while the group `winner.py::_ranking` dict `{s: i}`
            # keeps the LAST. The kernel matches the scalar (first-index) behavior
            # (see golden.rs GROUP_SOURCE_PRIORITY). Duplicates are a user error;
            # GoldenGroupRule permits them but a priority list is an ordered set.
            gspec_priority = [src_map.get(s, -1) for s in (g.source_priority or [])]
        elif g.strategy == "most_recent":
            built = _build_date_arrays(sdf, g.date_column)
            if built is None:
                return None
            gspec_date, gspec_date_mask = built
        elif g.strategy == "anchor":
            # anchor is validated to be one of the group's columns, so it is a
            # present user column (group columns were validated above).
            gspec_anchor_idx = col_index[g.anchor]
        group_specs.append(
            _GoldenFusedGroupSpec(
                col_indices=gcols,
                strategy=gstrat,
                priority_codes=gspec_priority,
                date_col=gspec_date,
                date_null_mask=gspec_date_mask,
                anchor_col_index=gspec_anchor_idx,
                allow_fill=g.allow_fill,
            )
        )

    # ── Stage 6: scalar resolution order + conditional (list-form) specs.
    # `build_resolution_order` topologically sorts units so a conditional `when:`
    # referencing another field/group resolves after it. `col_order` = the
    # non-group scalar columns in that order (covers every non-group user column
    # exactly once; group columns are resolved by the group pass). `conditionals`
    # = one spec per conditional column, in resolution order, with each clause's
    # `when:` lowered into the referenced columns' code space.
    from goldenmatch.core.golden_fused_predicate import _ABSENT_CODE, lower_predicate
    from goldenmatch.core.survivorship.conditions import (
        ResolutionError,
        build_resolution_order,
    )

    def _code_of(name: str, lit: Any) -> int:
        # None literal -> the null code (-1) so `x == None` reproduces the
        # reference's `value == None`; a present value -> its factorization code;
        # an absent literal -> the reserved absent sentinel (distinct from -1).
        if lit is None:
            return -1
        return col_maps.get(name, {}).get(lit, _ABSENT_CODE)

    try:
        order = build_resolution_order(rules.field_rules, rules.field_groups, user_cols)
    except ResolutionError:
        return None  # circular when: dependency -> decline to the classic path

    col_order = [col_index[u] for u in order if not u.startswith("group:") and u in col_index]

    conditionals: list[_GoldenFusedConditional] = []
    for u in order:
        if u.startswith("group:") or u not in col_index:
            continue
        ci = col_index[u]
        clause_list = conditional_cols.get(ci)
        if clause_list is None:
            continue
        clauses_spec: list[_GoldenFusedClause] = []
        default_strat: int | None = None
        for clause in clause_list:
            if clause.when is None:
                default_strat = _GOLDEN_STRATEGY_IDS[clause.strategy]
                continue
            try:
                ir = lower_predicate(clause.when, col_index, _code_of)
            except Exception:
                # The gate already vetted this predicate via predicate_lowerable,
                # so an exception HERE is a real lowering regression, not an
                # expected decline -- log it (a silent classic-path fallback would
                # surface only as an unexplained perf decline) but keep the safety
                # fallback: decline rather than risk a non-byte-identical result.
                import logging

                logging.getLogger(__name__).debug(
                    "golden_fused: lower_predicate failed for a vetted when: %r; "
                    "declining to the classic path",
                    clause.when,
                    exc_info=True,
                )
                return None
            clauses_spec.append(
                _GoldenFusedClause(ir=ir, strategy=_GOLDEN_STRATEGY_IDS[clause.strategy])
            )
        if default_strat is None:
            return None
        conditionals.append(
            _GoldenFusedConditional(
                col_index=ci, clauses=clauses_spec, default_strategy=default_strat
            )
        )

    side = _GoldenFusedSideChannels(
        source_code=source_code_arr,
        priority_codes=priority_codes,
        date_cols=date_cols,
        date_null_masks=date_null_masks,
        qweights=qweights,
        pair_edges=pair_edges,
        group_specs=group_specs,
        resolution_plan=_GoldenFusedResolutionPlan(
            col_order=col_order,
            conditionals=conditionals,
            overrides=override_specs,
        ),
    )
    winner_idx, field_conf, group_conf, cluster_ids_out = fn(
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

    # __golden_confidence__ = mean of per-UNIT confidences. A resolution unit is a
    # scalar column OR a whole group (resolve.py:100/149) -- so the denominator is
    # `n_scalar_cols + n_groups`, NOT n_output_cols: each group contributes exactly
    # ONE confidence (group_conf[g][k]), regardless of how many columns it spans,
    # and group-owned columns are EXCLUDED from the per-field scalar sum.
    n_clusters = len(cluster_ids_out)
    n_groups = len(group_specs)
    scalar_col_indices = [ci for ci in range(len(user_cols)) if ci not in grouped_col_idx]
    denom = len(scalar_col_indices) + n_groups
    # denom >= 1: user_cols is non-empty (early return), and every user column is
    # either a scalar unit or owned by one of the >=1 groups.
    gconf = [
        (
            sum(field_conf[ci][k] for ci in scalar_col_indices)
            + sum(group_conf[g][k] for g in range(n_groups))
        )
        / denom
        for k in range(n_clusters)
    ]
    result = result.with_columns(pl.Series("__golden_confidence__", gconf, dtype=pl.Float64))

    if not provenance:
        return result

    # ── Stage 8: per-field provenance (assembled from data the kernel already
    # returns -- no kernel change). See _build_provenance_records for the mapping.
    records = _build_provenance_records(
        sdf=sdf,
        n=n,
        user_cols=user_cols,
        out=out,
        winner_idx=winner_idx,
        field_conf=field_conf,
        group_conf=group_conf,
        group_col_lists=group_col_lists,
        cluster_ids_out=cluster_ids_out,
        gconf=gconf,
    )
    return result, records
