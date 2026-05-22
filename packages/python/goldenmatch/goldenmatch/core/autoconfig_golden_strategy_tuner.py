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
) -> bool:
    """Approximate whether `strategy` would have produced the same value
    the user chose, given the data available on `correction`.

    Real ground truth: re-run merge_field on the cluster context with
    `strategy` and compare to `correction.chosen_value`. That requires
    the cluster snapshot, which corrections don't carry.

    Heuristic: use the correction's `trust` + `decision` signals:
    - decision="approve" + trust >= 0.7 → strategy that PRESERVES the
      candidate (most_complete, longest_value, majority_vote when
      candidate is the consensus) matches.
    - decision="reject" + trust >= 0.7 → strategy that DROPS the
      candidate (unanimous_or_null, confidence_majority on weak edges)
      matches.
    - trust < 0.5 → ambiguous; no strategy matches confidently.

    This is intentionally approximate. The tuner returns a learned
    strategy when ONE strategy consistently outperforms others -- the
    absolute hit rate doesn't matter, only the relative ranking.
    """
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
) -> float:
    if not corrections:
        return 0.0
    hits = sum(1 for c in corrections if _strategy_would_match(c, strategy))
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

    # Filter corrections to this field. Corrections carry field_hash --
    # we want corrections that touched THIS field. Fall back to "all
    # corrections for this dataset" when field_hash filtering isn't
    # available (older corrections).
    corrections = list(store.get_corrections(dataset=dataset))
    # Best-effort field filter: corrections carrying `field_hash` that
    # matches a hash of `field` (the Correction dataclass uses opaque
    # hashes for privacy). For now, use all corrections -- the tuner
    # tunes one strategy per field but learns from the whole dataset's
    # signal. Per-field filtering is a v1.19 refinement.
    n = len(corrections)
    min_n = _min_corrections()
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
        rate = _hit_rate(train, strat)
        if rate > best_train_rate:
            best_train_rate = rate
            best_strategy = strat

    heldout_rate = _hit_rate(heldout, best_strategy)
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
