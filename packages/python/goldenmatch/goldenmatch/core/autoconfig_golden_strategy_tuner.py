"""Adaptive golden-strategy tuner (v1.18.1, #intelligence-2).

Mirrors ``core/autoconfig_ne_tuner.py`` shape. For each field, looks at
past MemoryStore corrections and learns which strategy historically
agreed with user choices. Gated on >= 50 corrections per dataset.

Spec: docs/superpowers/specs/2026-05-22-golden-rules-intelligence-layer-2-design.md
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goldenmatch.core.memory.store import Correction, MemoryStore

logger = logging.getLogger(__name__)

# Default candidate strategies the tuner classifies past corrections
# against. Excludes `custom:*` (plugin-backed) -- those are user-set,
# not auto-pickable.
DEFAULT_CANDIDATE_STRATEGIES: tuple[str, ...] = (
    "most_complete",
    "majority_vote",
    "first_non_null",
    "longest_value",
    "confidence_majority",
)

MIN_CORRECTIONS: int = 50
HELDOUT_FRACTION: float = 0.1
OVERFIT_GUARD_PP: float = 5.0


def _min_corrections() -> int:
    """Env-overridable minimum-corrections gate."""
    raw = os.environ.get("GOLDENMATCH_GOLDEN_TUNER_MIN_CORRECTIONS")
    if raw:
        try:
            return max(int(raw), 1)
        except ValueError:
            logger.warning(
                "GOLDENMATCH_GOLDEN_TUNER_MIN_CORRECTIONS=%r is not an int; "
                "using default %d", raw, MIN_CORRECTIONS,
            )
    return MIN_CORRECTIONS


@dataclass(frozen=True)
class StrategyTuning:
    """Result of a per-(dataset, field) tuner run.

    ``strategy`` is the learned best (one of the candidate strategies),
    or "" when the tuner declined to pick (reason in `below_minimum`,
    `no_memory`, `overfit_guard`).

    ``reason`` is a human-readable explanation:
    - "learned": tuner picked a winner above MIN_CORRECTIONS
    - "below_minimum": < MIN_CORRECTIONS corrections; fall back to heuristics
    - "no_memory": MemoryStore not active for this dataset
    - "overfit_guard": train > heldout by > 5pp; reverted to default
    """

    field: str
    strategy: str
    n_corrections: int
    train_hit_rate: float | None
    heldout_hit_rate: float | None
    reason: str


def _strategy_would_match(
    correction: Correction,
    strategy: str,
    *,
    field: str | None = None,
) -> bool:
    """Whether `strategy` would have produced the user's chosen value.

    Two regimes (#437):

    1. **Field-level correction** (preferred when available).
       `correction.field_name` is set + `corrected_value` is set.
       Predict what `strategy` would have picked given the inline-edit
       evidence on this row, then compare to `corrected_value`.

       Without the cluster snapshot we can't exactly replay
       merge_field, but the original_value vs corrected_value diff
       lets us infer enough about strategy fit:

       - corrected_value == original_value -> strategy that preserved
         the original would have been right (most_complete /
         longest_value / first_non_null all preserve; majority_vote
         preserves when consensus matches).
       - corrected_value != original_value AND corrected_value is
         longer -> longest_value would have been right.
       - corrected_value != original_value AND corrected_value is
         shorter -> NOT longest_value; unanimous_or_null /
         confidence_majority are more likely fits.
       - corrected_value != original_value, similar length -> ambiguous;
         most_recent / source_priority might be right (date / source
         signal) but we can't verify without cluster context.

    2. **Pair-level correction** (the original v1.18.1 heuristic).
       `correction.field_name` is None. Use coarse trust + decision
       signals.

    `field` filter: when set, only count this correction if it's a
    field-level edit on the SAME field. Pair-level corrections (no
    field_name) are evaluated regardless of `field` -- they carry
    dataset-wide signal that applies to every field's tuning.
    """
    # ── Regime 1: field-level correction ────────────────────────────
    fname = getattr(correction, "field_name", None)
    if fname is not None:
        # Field filter: skip corrections on other fields.
        if field is not None and fname != field:
            return False
        orig = getattr(correction, "original_value", None)
        corrected = getattr(correction, "corrected_value", None)
        if corrected is None:
            return False  # malformed field correction; ignore
        # No-edit case: reviewer reviewed + kept the original. Most
        # preserving strategies would have matched.
        if orig == corrected:
            return strategy in {
                "most_complete", "longest_value", "majority_vote",
                "first_non_null",
            }
        # Edit case: reviewer changed the value. The strategy that
        # WOULD have predicted `corrected` is the one that matches.
        if orig is not None:
            # longest_value would have been right iff corrected is
            # longer than original AND non-empty.
            if strategy == "longest_value":
                return len(corrected) > len(orig)
            # unanimous_or_null would have predicted NULL on this
            # disagreement -- which matches only if corrected is empty.
            if strategy == "unanimous_or_null":
                return corrected == "" or corrected is None
            # confidence_majority requires the cluster's pair_scores;
            # without them we conservatively credit this strategy when
            # the reviewer chose a DIFFERENT value (it tends to favor
            # higher-confidence subsets, which matches reviewer overrides).
            if strategy == "confidence_majority":
                return True
            # most_recent / source_priority need date / source signals
            # we don't have at this layer -- defer to the heuristic.
            if strategy in {"most_recent", "source_priority"}:
                return True  # plausibly fits an edit; tuner reranks
            # most_complete / majority_vote / first_non_null all
            # preserve. They would NOT match an edit.
            if strategy in {"most_complete", "majority_vote", "first_non_null"}:
                return False
        # original missing but corrected present -- first_non_null
        # would have predicted some value; treat as hit.
        return strategy in {"first_non_null", "most_complete"}

    # ── Regime 2: pair-level correction ─────────────────────────────
    # Older shape. Fall back to the v1.18.1 heuristic.
    raw_trust = getattr(correction, "trust", None)
    if raw_trust is None:
        return False
    trust = float(raw_trust)
    decision = getattr(correction, "decision", "approve")
    if trust < 0.5:
        return False
    # Approximate strategy preference per decision.
    preserves = {"most_complete", "longest_value", "majority_vote", "first_non_null"}
    drops = {"unanimous_or_null", "confidence_majority"}
    if decision == "approve":
        return strategy in preserves
    else:  # reject
        return strategy in drops


def _hit_rate(
    corrections: list[Correction],
    strategy: str,
    *,
    field: str | None = None,
) -> float:
    if not corrections:
        return 0.0
    hits = sum(1 for c in corrections if _strategy_would_match(c, strategy, field=field))
    return hits / len(corrections)


def tune_field_strategy(
    store: MemoryStore | None,
    dataset: str,
    field: str,
    candidates: tuple[str, ...] = DEFAULT_CANDIDATE_STRATEGIES,
) -> StrategyTuning:
    """Learn the best golden-strategy for `field` from MemoryStore.

    Args:
        store: MemoryStore instance, or None when memory is disabled.
        dataset: dataset key for scoping corrections.
        field: golden-field name (used for logging + filtering).
        candidates: strategies to evaluate. Defaults to v1.18 built-ins
            (excludes `custom:*` plugins; those are explicit, not learned).

    Returns:
        StrategyTuning carrying the learned strategy + diagnostics.
    """
    if store is None:
        return StrategyTuning(
            field=field, strategy="",
            n_corrections=0, train_hit_rate=None, heldout_hit_rate=None,
            reason="no_memory",
        )

    # Two-tier filter (#437):
    # 1. Field-level corrections matching THIS field count toward the
    #    threshold (precise signal -- worth low gates).
    # 2. Pair-level corrections (field_name=None) count as dataset-wide
    #    background signal -- included in the corpus, evaluated by the
    #    older heuristic in `_strategy_would_match`.
    all_corrections = list(store.get_corrections(dataset=dataset))
    field_level = [
        c for c in all_corrections
        if getattr(c, "field_name", None) == field
    ]
    pair_level = [
        c for c in all_corrections
        if getattr(c, "field_name", None) is None
    ]
    # Prefer field-level corrections when we have enough of them.
    # When the field has its own corpus, use ONLY that corpus (precise
    # signal beats noisy dataset-wide signal); pair-level becomes
    # fallback only.
    min_n = _min_corrections()
    if len(field_level) >= min_n:
        corrections = field_level
    else:
        # Combine field-level (if any) with pair-level for a coarser
        # signal when there aren't enough field-specific corrections.
        corrections = field_level + pair_level
    n = len(corrections)
    if n < min_n:
        return StrategyTuning(
            field=field, strategy="",
            n_corrections=n, train_hit_rate=None, heldout_hit_rate=None,
            reason="below_minimum",
        )

    # 90/10 split. Deterministic via correction id sort.
    sorted_corrections = sorted(
        corrections,
        key=lambda c: getattr(c, "id", "") or "",
    )
    n_heldout = max(int(n * HELDOUT_FRACTION), 1)
    train = sorted_corrections[:-n_heldout]
    heldout = sorted_corrections[-n_heldout:]

    # Find the strategy with the highest train hit rate.
    best_strategy = "most_complete"  # safe default
    best_train_rate = -1.0
    for strat in candidates:
        rate = _hit_rate(train, strat, field=field)
        if rate > best_train_rate:
            best_train_rate = rate
            best_strategy = strat

    heldout_rate = _hit_rate(heldout, best_strategy, field=field)
    train_pp = best_train_rate * 100
    heldout_pp = heldout_rate * 100

    if train_pp - heldout_pp > OVERFIT_GUARD_PP:
        logger.info(
            "golden_strategy_tuner: overfit_guard (train=%.3f, heldout=%.3f) "
            "field=%r dataset=%r; reverting to most_complete",
            best_train_rate, heldout_rate, field, dataset,
        )
        return StrategyTuning(
            field=field, strategy="",
            n_corrections=n,
            train_hit_rate=best_train_rate,
            heldout_hit_rate=heldout_rate,
            reason="overfit_guard",
        )

    logger.info(
        "golden_strategy_tuner: learned %s for field=%r dataset=%r "
        "(train=%.3f, heldout=%.3f, n=%d)",
        best_strategy, field, dataset, best_train_rate, heldout_rate, n,
    )
    return StrategyTuning(
        field=field, strategy=best_strategy,
        n_corrections=n,
        train_hit_rate=best_train_rate,
        heldout_hit_rate=heldout_rate,
        reason="learned",
    )
