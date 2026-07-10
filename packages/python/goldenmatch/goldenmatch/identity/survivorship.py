"""Survivorship learning + per-cell golden-record provenance (#1111, epic #1108).

The Identity Graph builds a *golden record* per entity by rolling its source
records up into one representative row. Two gaps this module closes:

1. **Per-cell provenance.** ``resolve._golden_record_from_members`` produces a
   flat ``{column: value}`` with no trace of *where each cell came from*. An
   MDM golden record has to be auditable: every surviving cell traceable to a
   source record + timestamp. ``build_golden_with_provenance`` returns the same
   values PLUS a ``CellProvenance`` per column (source, source row, timestamp,
   the strategy that picked it, confidence).

2. **Survivorship learning.** Which merge strategy wins a field is configured,
   never learned. When a steward inline-edits a golden cell (a ``FIELD_CORRECT``
   correction: ``field_name`` / ``original_value`` -> ``corrected_value``), that
   is ground truth about which value *should* have survived.
   ``learn_field_survivorship`` replays the candidate strategies against those
   (loser -> winner) pairs and reports, per field, which strategy best
   reproduces the steward's choices -- a recommendation you can fold back into
   ``field_strategies``.

Both reuse the existing strategy engine (``core.golden.merge_field``) rather
than re-implementing survivorship, and neither requires a DB migration.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from goldenmatch._polars_lazy import pl

from goldenmatch.config.schemas import GoldenFieldRule
from goldenmatch.core.golden import _is_internal, merge_field

if TYPE_CHECKING:
    from goldenmatch.config.schemas import SurvivorshipConfig
    from goldenmatch.core.memory.store import Correction

# Default field survivorship strategy when none is configured for a column.
DEFAULT_STRATEGY = "most_complete"

# Strategies whose winner is decidable from the candidate *values* alone -- the
# only ones learnable from a (original_value, corrected_value) correction pair,
# which carries no per-candidate sources / dates / pair-scores. ``most_recent``
# and ``source_priority`` are deliberately excluded from learning (they need the
# candidate context the correction doesn't capture -- a documented follow-up).
DEFAULT_LEARNABLE_STRATEGIES: tuple[str, ...] = (
    "most_complete",
    "longest_value",
    "first_non_null",
    "majority_vote",
    "unanimous_or_null",
)


# ── Per-cell provenance ─────────────────────────────────────────────────────


@dataclass
class CellProvenance:
    """Where one golden-record cell came from."""

    value: Any
    source: str | None              # the ``__source__`` of the winning record
    source_row_id: int | None       # the winning record's ``__row_id__``
    strategy: str                   # the merge strategy that picked it
    confidence: float
    timestamp: Any = None           # the winning record's timestamp, if tracked
    source_record_id: str | None = None  # ``{source}:{pk}`` when a pk col is set


@dataclass
class GoldenRecordWithProvenance:
    values: dict[str, Any]
    provenance: dict[str, CellProvenance] = field(default_factory=dict)

    def as_record(self) -> dict[str, Any]:
        """The flat ``{column: value}`` golden record (provenance dropped)."""
        return dict(self.values)


def build_golden_with_provenance(
    df: pl.DataFrame,
    member_row_ids: list[int],
    *,
    field_strategies: dict[str, str] | None = None,
    default_strategy: str = DEFAULT_STRATEGY,
    source_col: str = "__source__",
    timestamp_col: str | None = None,
    source_pk_col: str | None = None,
    config: SurvivorshipConfig | None = None,
) -> GoldenRecordWithProvenance:
    """Roll cluster members up into a golden record, tracking per-cell provenance.

    For each non-internal column, the configured strategy (``field_strategies``
    overrides ``default_strategy``) is applied via ``core.golden.merge_field``,
    and the winning candidate is traced back to its source record:
    ``CellProvenance(value, source, source_row_id, strategy, confidence,
    timestamp, source_record_id)``.

    ``config`` (a ``SurvivorshipConfig``) supplies defaults for every knob; an
    explicit keyword argument always wins over the config field.
    """
    if config is not None:
        if field_strategies is None:
            field_strategies = dict(config.field_strategies) or None
        if default_strategy == DEFAULT_STRATEGY:
            default_strategy = config.default_strategy
        if timestamp_col is None:
            timestamp_col = config.timestamp_column

    field_strategies = field_strategies or {}
    if "__row_id__" not in df.columns or not member_row_ids:
        return GoldenRecordWithProvenance(values={})

    members = df.filter(pl.col("__row_id__").is_in(member_row_ids))
    if members.is_empty():
        return GoldenRecordWithProvenance(values={})

    rows = members.to_dicts()
    row_ids = [int(r["__row_id__"]) for r in rows]
    sources = [r.get(source_col) for r in rows]
    dates = (
        [r.get(timestamp_col) for r in rows] if timestamp_col else None
    )

    values_out: dict[str, Any] = {}
    prov_out: dict[str, CellProvenance] = {}

    for col in members.columns:
        if _is_internal(col) or col == source_col or col == timestamp_col:
            continue
        col_values = [r.get(col) for r in rows]
        if all(v is None for v in col_values):
            continue
        strategy = field_strategies.get(col, default_strategy)
        rule = _rule_for(strategy, timestamp_col, sources)
        try:
            value, confidence, src_idx = merge_field(
                col_values, rule, sources=sources, dates=dates,
            )
        except (ValueError, KeyError):
            # A misconfigured strategy (e.g. source_priority without a list)
            # falls back to most_complete rather than dropping the cell.
            value, confidence, src_idx = merge_field(
                col_values, GoldenFieldRule(strategy="most_complete"),
            )
        if value is None:
            continue
        values_out[col] = value
        win_row = row_ids[src_idx] if src_idx is not None else None
        win_source = sources[src_idx] if src_idx is not None else None
        win_ts = (
            dates[src_idx] if (dates is not None and src_idx is not None) else None
        )
        prov_out[col] = CellProvenance(
            value=value,
            source=str(win_source) if win_source is not None else None,
            source_row_id=win_row,
            strategy=strategy,
            confidence=float(confidence),
            timestamp=win_ts,
            source_record_id=_record_id_for(
                rows[src_idx] if src_idx is not None else None,
                win_source, source_pk_col,
            ),
        )
    return GoldenRecordWithProvenance(values=values_out, provenance=prov_out)


def _rule_for(
    strategy: str, timestamp_col: str | None, sources: list[Any] | None,
) -> GoldenFieldRule:
    """Build a ``GoldenFieldRule`` for ``strategy``, supplying the extra config
    the strategy validator requires (date_column / source_priority) when we can."""
    if strategy == "most_recent":
        # most_recent needs a date_column; fall back to most_complete if no
        # timestamp column is tracked.
        if timestamp_col:
            return GoldenFieldRule(strategy="most_recent", date_column=timestamp_col)
        return GoldenFieldRule(strategy="most_complete")
    if strategy == "source_priority":
        prio = [str(s) for s in dict.fromkeys(sources or []) if s is not None]
        if prio:
            return GoldenFieldRule(strategy="source_priority", source_priority=prio)
        return GoldenFieldRule(strategy="most_complete")
    return GoldenFieldRule(strategy=strategy)


def _record_id_for(
    row: dict[str, Any] | None, source: Any, source_pk_col: str | None,
) -> str | None:
    if row is None or source is None or not source_pk_col:
        return None
    pk = row.get(source_pk_col)
    if pk is None:
        return None
    return f"{source}:{pk}"


# ── Survivorship learning from FIELD_CORRECT corrections ────────────────────


@dataclass
class FieldStrategyRecommendation:
    """Per-field result of replaying strategies against steward corrections."""

    field_name: str
    best_strategy: str
    agreement: float                 # trust-weighted fraction reproduced (0..1)
    support: int                     # number of corrections considered
    per_strategy: dict[str, float]   # agreement per candidate strategy


def learn_field_survivorship(
    corrections: list[Correction],
    *,
    candidate_strategies: tuple[str, ...] = DEFAULT_LEARNABLE_STRATEGIES,
    min_support: int = 3,
    min_agreement: float = 0.6,
) -> dict[str, FieldStrategyRecommendation]:
    """Learn a per-field survivorship strategy from ``FIELD_CORRECT`` corrections.

    A ``FIELD_CORRECT`` correction is ground truth that, for ``field_name``, the
    steward preferred ``corrected_value`` over ``original_value`` (the value the
    golden record had shown). For each field we replay every
    ``candidate_strategy`` over the ``[original_value, corrected_value]`` pair
    (via ``core.golden.merge_field``) and measure the **trust-weighted fraction**
    of corrections whose winner the strategy reproduces. The best-agreeing
    strategy is the recommendation.

    Returns ``{field_name: FieldStrategyRecommendation}`` for every field with
    enough signal. Use :func:`learned_field_strategies` to get the subset that
    clears ``min_support`` / ``min_agreement`` as a directly-usable
    ``field_strategies`` map.

    Scope: strategies decidable from values alone (``DEFAULT_LEARNABLE_STRATEGIES``);
    ``most_recent`` / ``source_priority`` need per-candidate dates / sources the
    correction doesn't capture, so they're out of the learnable set.
    """
    by_field: dict[str, list[Correction]] = {}
    for c in corrections:
        if str(c.decision) != "field_correct":
            continue
        fn = c.field_name
        if not fn or c.corrected_value is None or c.original_value is None:
            continue
        if c.original_value == c.corrected_value:
            continue
        by_field.setdefault(fn, []).append(c)

    out: dict[str, FieldStrategyRecommendation] = {}
    for fn, items in by_field.items():
        total_trust = sum(_trust(c) for c in items)
        if total_trust <= 0:
            continue
        per_strategy: dict[str, float] = {}
        for strategy in candidate_strategies:
            rule = GoldenFieldRule(strategy=strategy)
            agree_trust = 0.0
            for c in items:
                # Candidate order [original (loser), corrected (winner)] is
                # deliberate: an order-sensitive strategy (first_non_null) that
                # keeps the original therefore scores 0 -- which correctly says
                # "the steward did not keep the original".
                candidates = [c.original_value, c.corrected_value]
                try:
                    value, _conf, _idx = merge_field(candidates, rule)
                except (ValueError, KeyError):
                    continue
                if value == c.corrected_value:
                    agree_trust += _trust(c)
            per_strategy[strategy] = round(agree_trust / total_trust, 6)
        # Best strategy: highest agreement, tie-break by candidate order
        # (earlier = preferred), so e.g. most_complete beats longest_value on a tie.
        best = max(
            candidate_strategies,
            key=lambda s: (per_strategy.get(s, 0.0), -candidate_strategies.index(s)),
        )
        out[fn] = FieldStrategyRecommendation(
            field_name=fn,
            best_strategy=best,
            agreement=per_strategy[best],
            support=len(items),
            per_strategy=per_strategy,
        )
    return out


def learned_field_strategies(
    recommendations: dict[str, FieldStrategyRecommendation],
    *,
    min_support: int = 3,
    min_agreement: float = 0.6,
) -> dict[str, str]:
    """The confident subset of ``learn_field_survivorship`` as a ``{field:
    strategy}`` map, ready to drop into ``field_strategies``. Only fields whose
    recommendation clears both thresholds are included."""
    return {
        fn: rec.best_strategy
        for fn, rec in recommendations.items()
        if rec.support >= min_support and rec.agreement >= min_agreement
    }


def _trust(c: Correction) -> float:
    t = getattr(c, "trust", 1.0)
    try:
        return float(t)
    except (TypeError, ValueError):
        return 1.0
