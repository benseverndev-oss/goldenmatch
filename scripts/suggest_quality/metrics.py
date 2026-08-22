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
from collections.abc import Iterable, Sequence


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


def candidate_metrics(
    block_members: Iterable[Sequence[int]],
    gt_pairs: set[tuple[int, int]],
) -> dict:
    """Blocking-stage metrics: the ceiling the candidate set imposes, and its cost.

    Every other metric in this module is downstream of blocking. These two are
    the blocking stage itself -- ``candidate_recall`` is the hard ceiling no
    scorer can exceed, because a pair blocking never emitted cannot be scored.

    ``candidate_recall``
        Fraction of ground-truth pairs that share at least one block.
        ``nan`` when ``gt_pairs`` is empty (the blocking-shape anchors carry no
        truth), mirroring how ``baseline_f1`` reports "not applicable".

    ``candidate_pairs``
        Within-block COMPARISONS, ``sum(n*(n-1)/2)`` over block sizes -- the
        same identity ``block_analyzer.score_candidate`` reports as
        ``total_comparisons``. A pair co-blocked by two passes counts twice,
        deliberately: this is the cost signal, and the scorer really does pay
        for it twice.

    The two must be read together. Recall alone is trivially gamed -- one block
    holding every record scores 1.0 -- which is not hypothetical: it is the
    shape the parked recall floor produced, at 22.5x the comparisons for no
    measured gain. Recall is the ceiling, pairs is what the ceiling costs.

    Cost is O(sum of block SIZES) to index plus O(|gt_pairs|) to test -- never
    O(candidate pairs). Enumerating the candidate set would be quadratic in
    block size and is unnecessary: membership answers the recall question, and
    the closed form answers the cost question.
    """
    row_blocks: dict[int, set[int]] = {}
    comparisons = 0
    for bid, members in enumerate(block_members):
        n = 0
        for rid in members:
            row_blocks.setdefault(rid, set()).add(bid)
            n += 1
        comparisons += n * (n - 1) // 2

    if not gt_pairs:
        return {"candidate_recall": float("nan"), "candidate_pairs": comparisons}

    hits = 0
    for a, b in gt_pairs:
        ba = row_blocks.get(a)
        if ba and not ba.isdisjoint(row_blocks.get(b) or ()):
            hits += 1

    return {
        "candidate_recall": hits / len(gt_pairs),
        "candidate_pairs": comparisons,
    }
