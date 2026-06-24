"""Pure-function suggester quality metrics.

Sign convention for rank_correlation
-------------------------------------
Spearman correlation between rank position (0-indexed, ascending) and the
NEGATED lifts, so that "highest-lift suggestion ranked first" = +1.0.

Equivalently: rank position 0 should have the LARGEST lift. If the suggester is
perfect (lifts descending), rank position 0 has the largest lift, correlation
between positions and (-lifts) is +1.0. If the suggester ranks worst first
(lifts ascending), correlation is -1.0. A random suggester gives ~0.

Edge cases:
- 0 suggestions  -> float('nan')
- 1 suggestion   -> float('nan')  (Spearman is undefined for n=1)
- all lifts tied -> float('nan')  (scipy returns nan on zero-variance input)
"""
from __future__ import annotations

import math


def rank_correlation(suggested_order_lifts: list[float]) -> float:
    """Spearman rank correlation between suggester rank and measured F1 lift.

    Convention: "best-suggestion-first" => +1.0, "worst-first" => -1.0.

    Args:
        suggested_order_lifts: Measured F1 lifts in the order the suggester
            ranked them (index 0 = top-ranked suggestion).

    Returns:
        Spearman rho in [-1, 1], or float('nan') when undefined (n < 2).
    """
    n = len(suggested_order_lifts)
    if n < 2:
        return float("nan")

    from scipy.stats import spearmanr  # noqa: PLC0415

    ranks = list(range(n))                        # [0, 1, 2, ...] ascending
    neg_lifts = [-x for x in suggested_order_lifts]

    result = spearmanr(ranks, neg_lifts)
    # scipy < 1.9 returns a named tuple; >= 1.9 returns SpearmanrResult
    rho = float(result.statistic if hasattr(result, "statistic") else result[0])
    if math.isnan(rho):
        return float("nan")
    return rho


def suggester_precision(lifts: list[float]) -> float:
    """Fraction of suggestions with lift >= 0 (i.e. do not regress F1).

    A lift of exactly 0.0 counts as "not harmful" (no regression).

    Args:
        lifts: Measured F1 lift per suggestion (any order).

    Returns:
        Value in [0, 1].  Returns 1.0 for an empty list (vacuously true).
    """
    if not lifts:
        return 1.0
    non_negative = sum(1 for x in lifts if x >= 0.0)
    return non_negative / len(lifts)


def convergence(steps: list[tuple[str, float]]) -> dict:
    """Summarize a greedy-convergence trail.

    Args:
        steps: List of (suggestion_id, f1_after_applying_it) in application
            order.  May be empty (no suggestion had positive lift).

    Returns:
        dict with keys:
            final_f1 (float):  F1 after the last step, or 0.0 if empty.
            steps (int):       Number of greedy steps taken.
            improved (bool):   True iff at least one step was taken.
    """
    return {
        "final_f1": steps[-1][1] if steps else 0.0,
        "steps": len(steps),
        "improved": len(steps) > 0,
    }


DAMAGE_EPS = 0.005  # min ceiling-minus-degraded gap for recovery% to be meaningful


def recovery_pct(f1_degraded: float, f1_recovered: float, f1_ceiling: float) -> float:
    """Fraction of the damage the suggester recovered.

    (f1_recovered - f1_degraded) / (f1_ceiling - f1_degraded).
    1.0 = fully undid the damage; >1.0 = beat the zero-config ceiling;
    <0.0 = made it worse. Returns nan when the damage gap < DAMAGE_EPS
    (no meaningful damage to recover). Not clamped.
    """
    denom = f1_ceiling - f1_degraded
    if denom < DAMAGE_EPS:
        return float("nan")
    return (f1_recovered - f1_degraded) / denom
