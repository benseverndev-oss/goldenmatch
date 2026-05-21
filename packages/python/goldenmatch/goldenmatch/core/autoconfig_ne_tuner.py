"""Adaptive penalty / threshold tuner for negative evidence fields (#129).

Replaces v1.12's fixed defaults (penalty=0.3, threshold=0.4) with values
learned from labeled corrections in ``MemoryStore``. Gated on:

- ``MemoryConfig`` is active on the run.
- ≥ ``MIN_CORRECTIONS`` (default 50) labeled corrections exist for the
  current dataset.

Algorithm: grid search over a small parameter grid, validated against
a held-out 10% of corrections. If the held-out F1 drops > 5pp below the
training F1, the tuner is overfitting and falls back to defaults.

Spec: docs/superpowers/specs/2026-05-21-adaptive-ne-tuning-design.md
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable

    from goldenmatch.core.memory.store import Correction, MemoryStore

logger = logging.getLogger(__name__)

# Grid search bounds (#129 spec). 20 combinations; cheap.
PENALTY_GRID: tuple[float, ...] = (0.1, 0.2, 0.3, 0.4, 0.5)
THRESHOLD_GRID: tuple[float, ...] = (0.2, 0.3, 0.4, 0.5)

# Tuner gates.
MIN_CORRECTIONS: int = 50  # below this, tuner skips and defaults are used.
HELDOUT_FRACTION: float = 0.1
OVERFIT_GUARD_PP: float = 5.0  # train_f1 - heldout_f1 > 5pp -> revert

# Defaults — same as core.autoconfig_negative_evidence; surfaced here so
# callers can read them when the tuner returns None.
DEFAULT_PENALTY: float = 0.3
DEFAULT_THRESHOLD: float = 0.4


def _min_corrections() -> int:
    """Env-overridable minimum-corrections gate."""
    raw = os.environ.get("GOLDENMATCH_NE_TUNER_MIN_CORRECTIONS")
    if raw:
        try:
            return int(raw)
        except ValueError:
            logger.warning(
                "GOLDENMATCH_NE_TUNER_MIN_CORRECTIONS=%r is not an int; "
                "using default %d", raw, MIN_CORRECTIONS,
            )
    return MIN_CORRECTIONS


@dataclass(frozen=True)
class NETuning:
    """Result of a per-(dataset, field) tuner run.

    ``train_f1`` / ``heldout_f1`` are diagnostic. The penalty/threshold
    are what the caller applies to the NE field.

    When ``n_corrections < MIN_CORRECTIONS`` OR the tuner reverted
    (held-out drop > OVERFIT_GUARD_PP), the result carries the default
    values + ``reason`` explaining why.
    """

    penalty: float
    threshold: float
    n_corrections: int
    train_f1: float | None  # None when defaults used
    heldout_f1: float | None
    reason: str  # human-readable: "tuned", "below_minimum", "overfit_guard", "no_memory"


def _score_correction(
    correction: Correction,
    penalty: float,
    threshold: float,
) -> bool:
    """Return whether the candidate (penalty, threshold) would predict
    "match" for this labeled correction.

    Heuristic (proxy for what a real scorer would emit):
    - Correction's `decision` is the truth (match / non-match).
    - We approximate the scorer's output as the correction's `trust`
      attribute, which exists for every Correction. (Trust isn't a
      perfect proxy but is the only signal we have without re-scoring
      every pair, which would defeat the cheap-tuner goal.)

    A pair is predicted to match iff
    ``trust - penalty * (1 - decision_match) >= threshold``.

    For ``decision == "match"`` corrections, NE shouldn't fire (penalty
    not applied) — predict match iff ``trust >= threshold``.
    For ``decision == "reject"``, NE fires — predict match iff
    ``trust - penalty >= threshold``.
    """
    raw_score = getattr(correction, "trust", None)
    if raw_score is None:
        # Defensive: trust is part of the Correction dataclass.
        return False
    decision_match = (
        getattr(correction, "decision", "match") == "match"
    )
    if decision_match:
        return raw_score >= threshold
    else:
        return (raw_score - penalty) >= threshold


def _f1_for_grid_point(
    corrections: Iterable[Correction],
    penalty: float,
    threshold: float,
) -> float:
    """Compute F1 of the (penalty, threshold) candidate on the
    ground-truth labels in ``corrections``."""
    tp = fp = fn = 0
    for c in corrections:
        predicted_match = _score_correction(c, penalty, threshold)
        actual_match = getattr(c, "decision", "match") == "match"
        if predicted_match and actual_match:
            tp += 1
        elif predicted_match and not actual_match:
            fp += 1
        elif not predicted_match and actual_match:
            fn += 1
    if tp == 0:
        return 0.0
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def tune_ne_field(
    store: MemoryStore | None,
    dataset: str,
    field: str,
) -> NETuning:
    """Tune (penalty, threshold) for one NE field via grid search.

    Args:
        store: ``MemoryStore`` instance, or ``None`` when memory is
            disabled. Returns defaults with ``reason="no_memory"`` when
            ``None``.
        dataset: dataset key for scoping the corrections lookup.
        field: NE field name (used for logging only — corrections cover
            the whole pair, not per-field).

    Returns:
        ``NETuning`` carrying either the learned values or defaults.
    """
    if store is None:
        return NETuning(
            penalty=DEFAULT_PENALTY,
            threshold=DEFAULT_THRESHOLD,
            n_corrections=0,
            train_f1=None,
            heldout_f1=None,
            reason="no_memory",
        )

    corrections = list(store.get_corrections(dataset=dataset))
    n = len(corrections)
    min_n = _min_corrections()

    if n < min_n:
        logger.debug(
            "ne_tuner: dataset=%r field=%r below min_corrections (%d < %d); using defaults",
            dataset, field, n, min_n,
        )
        return NETuning(
            penalty=DEFAULT_PENALTY,
            threshold=DEFAULT_THRESHOLD,
            n_corrections=n,
            train_f1=None,
            heldout_f1=None,
            reason="below_minimum",
        )

    # 90/10 split. Deterministic via hash so re-runs produce the same split.
    sorted_corrections = sorted(
        corrections,
        key=lambda c: getattr(c, "id", "") or "",
    )
    n_heldout = max(int(n * HELDOUT_FRACTION), 1)
    train = sorted_corrections[:-n_heldout]
    heldout = sorted_corrections[-n_heldout:]

    best_f1 = -1.0
    best_penalty = DEFAULT_PENALTY
    best_threshold = DEFAULT_THRESHOLD
    for penalty in PENALTY_GRID:
        for threshold in THRESHOLD_GRID:
            f1 = _f1_for_grid_point(train, penalty, threshold)
            if f1 > best_f1:
                best_f1 = f1
                best_penalty = penalty
                best_threshold = threshold

    heldout_f1 = _f1_for_grid_point(heldout, best_penalty, best_threshold)
    train_pp = best_f1 * 100
    heldout_pp = heldout_f1 * 100

    if train_pp - heldout_pp > OVERFIT_GUARD_PP:
        logger.info(
            "ne_tuner: overfit_guard fired (train_f1=%.3f, heldout_f1=%.3f); "
            "reverting to defaults for dataset=%r field=%r",
            best_f1, heldout_f1, dataset, field,
        )
        return NETuning(
            penalty=DEFAULT_PENALTY,
            threshold=DEFAULT_THRESHOLD,
            n_corrections=n,
            train_f1=best_f1,
            heldout_f1=heldout_f1,
            reason="overfit_guard",
        )

    logger.info(
        "ne_tuner: tuned dataset=%r field=%r penalty=%.2f threshold=%.2f "
        "(train_f1=%.3f, heldout_f1=%.3f, n=%d)",
        dataset, field, best_penalty, best_threshold,
        best_f1, heldout_f1, n,
    )
    return NETuning(
        penalty=best_penalty,
        threshold=best_threshold,
        n_corrections=n,
        train_f1=best_f1,
        heldout_f1=heldout_f1,
        reason="tuned",
    )
