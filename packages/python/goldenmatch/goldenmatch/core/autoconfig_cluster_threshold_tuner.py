"""Cluster-decision threshold tuner (v1.20.x).

Consumes ``decision="cluster_decision"`` corrections from
``MemoryStore`` and proposes an updated auto-approve threshold for a
dataset. Mirrors the shape of
``goldenmatch.core.autoconfig_golden_strategy_tuner.tune_field_strategy``
so downstream consumers (e.g. print-modernization's match-review
calibration dashboard) can render cluster-threshold suggestions
alongside field-strategy suggestions without a second result schema.

Source: RFC from Ben Severn (MJH Print Modernization, 2026-05-22).
Algorithm lifted verbatim from print-modernization's working
``_persist_threshold_suggestion`` impl.

Spec: docs/superpowers/specs/2026-05-22-cluster-decision-tuner-design.md
"""
from __future__ import annotations

import hashlib
import logging
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from goldenmatch.core.memory.store import MemoryStore

log = logging.getLogger(__name__)


__all__ = [
    "ThresholdSuggestion",
    "tune_decision_threshold",
]


@dataclass(frozen=True)
class ThresholdSuggestion:
    """Result of a cluster-decision threshold sweep.

    Mirrors the field-strategy tuner's `StrategyTuning` so a single
    dashboard surface can render both tuner outputs.

    Attributes:
        threshold: suggested cutoff in [0, 1], or None when the tuner
            declined to propose (see `reason`).
        n_total: total cluster_decision corrections for the dataset.
        n_train: count in the 90% training split.
        n_heldout: count in the 10% held-out split.
        train_approve_rate: approve-rate on the training set at
            `threshold`. None when no qualifying band found.
        heldout_approve_rate: approve-rate on the held-out set at
            `threshold`. None when no qualifying band found.
        reason: one-line rationale. One of:
            "ok": threshold valid; safe to surface.
            "below_minimum": fewer than 2 * min_band_n total samples.
            "no_qualifying_band": sweep found no t meeting target.
            "overfit": heldout failed validation vs train.
    """

    threshold: float | None
    n_total: int
    n_train: int
    n_heldout: int
    train_approve_rate: float | None
    heldout_approve_rate: float | None
    reason: str


def tune_decision_threshold(
    store: MemoryStore,
    dataset: str,
    *,
    target_approve_rate: float = 0.99,
    min_band_n: int = 50,
    holdout_frac: float = 0.10,
    max_overfit_drop_pp: float = 1.0,
    seed: int | None = None,
) -> ThresholdSuggestion:
    """Sweep cluster_decision corrections for a threshold that hits
    `target_approve_rate` on training while remaining valid on held-out.

    Algorithm (verbatim from RFC):

    1. Load all cluster_decision corrections for the dataset.
       Reason="below_minimum" + threshold=None if fewer than
       `min_band_n * 2`.
    2. Deterministic shuffle. Default seed: first 8 bytes of
       `sha256(dataset)` (stable across processes; avoids
       PYTHONHASHSEED non-determinism). Override via `seed`.
    3. Split into 90% train, 10% heldout.
    4. Sort train descending by `cluster_score`. Sweep: find the
       lowest `t` such that approve-rate over scores >= t is
       >= `target_approve_rate` AND the band has >= `min_band_n`
       samples.
    5. Compute heldout approve-rate at the same threshold.
    6. Reject (threshold=None, reason="overfit") if heldout < target
       OR heldout drops > `max_overfit_drop_pp` from train.
    7. Return ThresholdSuggestion.

    Args:
        store: MemoryStore handle (open).
        dataset: dataset namespace to tune.
        target_approve_rate: approve-rate floor for the threshold.
        min_band_n: minimum samples in the at-or-above band.
        holdout_frac: fraction held out for overfit guard.
        max_overfit_drop_pp: max allowed drop (percentage points)
            from train approve-rate to heldout approve-rate.
        seed: optional override for the deterministic shuffle seed.

    Returns:
        ThresholdSuggestion with the proposal (or refusal reason).
    """
    corrections = [
        c for c in store.get_corrections(dataset=dataset)
        if c.decision == "cluster_decision"
        and c.cluster_score is not None
        and c.cluster_outcome in ("approve", "reject")
    ]
    n_total = len(corrections)

    if n_total < min_band_n * 2:
        return ThresholdSuggestion(
            threshold=None,
            n_total=n_total,
            n_train=0,
            n_heldout=0,
            train_approve_rate=None,
            heldout_approve_rate=None,
            reason="below_minimum",
        )

    # Deterministic shuffle. Default seed is sha256(dataset) so the
    # same store + dataset reproduce the same split across processes.
    if seed is None:
        digest = hashlib.sha256(dataset.encode("utf-8")).digest()[:8]
        seed = int.from_bytes(digest, "big")
    rng = random.Random(seed)
    shuffled = list(corrections)
    rng.shuffle(shuffled)

    n_heldout = max(1, int(round(n_total * holdout_frac)))
    n_train = n_total - n_heldout
    heldout = shuffled[:n_heldout]
    train = shuffled[n_heldout:]

    # Sort train descending by score for the sweep.
    train_sorted = sorted(
        train,
        key=lambda c: float(c.cluster_score),  # type: ignore[arg-type]
        reverse=True,
    )

    # Sweep: walk down the sorted list, expanding the at-or-above
    # band one record at a time. Find the LARGEST band (lowest
    # threshold) that meets target_approve_rate AND has >= min_band_n.
    best_threshold: float | None = None
    best_train_rate: float | None = None
    approves = 0
    for i, c in enumerate(train_sorted):
        if c.cluster_outcome == "approve":
            approves += 1
        band_n = i + 1
        if band_n < min_band_n:
            continue
        rate = approves / band_n
        if rate >= target_approve_rate:
            # This band qualifies. The threshold is THIS record's
            # cluster_score (the lowest score still in the qualifying
            # band). Keep going to find the LOWEST threshold (largest
            # band) that still qualifies.
            best_threshold = float(c.cluster_score)  # type: ignore[arg-type]
            best_train_rate = rate
        else:
            # Once we drop below target, further expansion only
            # lowers the rate. Stop.
            break

    if best_threshold is None or best_train_rate is None:
        return ThresholdSuggestion(
            threshold=None,
            n_total=n_total,
            n_train=n_train,
            n_heldout=n_heldout,
            train_approve_rate=None,
            heldout_approve_rate=None,
            reason="no_qualifying_band",
        )

    # Evaluate heldout at the same threshold.
    heldout_at = [
        c for c in heldout
        if float(c.cluster_score) >= best_threshold  # type: ignore[arg-type]
    ]
    if not heldout_at:
        # Heldout has no samples >= threshold. We can't validate;
        # treat as overfit (conservative).
        return ThresholdSuggestion(
            threshold=None,
            n_total=n_total,
            n_train=n_train,
            n_heldout=n_heldout,
            train_approve_rate=best_train_rate,
            heldout_approve_rate=None,
            reason="overfit",
        )
    heldout_approves = sum(1 for c in heldout_at if c.cluster_outcome == "approve")
    heldout_rate = heldout_approves / len(heldout_at)

    train_pp = best_train_rate * 100.0
    heldout_pp = heldout_rate * 100.0
    if (
        heldout_rate < target_approve_rate
        or train_pp - heldout_pp > max_overfit_drop_pp
    ):
        return ThresholdSuggestion(
            threshold=None,
            n_total=n_total,
            n_train=n_train,
            n_heldout=n_heldout,
            train_approve_rate=best_train_rate,
            heldout_approve_rate=heldout_rate,
            reason="overfit",
        )

    return ThresholdSuggestion(
        threshold=best_threshold,
        n_total=n_total,
        n_train=n_train,
        n_heldout=n_heldout,
        train_approve_rate=best_train_rate,
        heldout_approve_rate=heldout_rate,
        reason="ok",
    )
