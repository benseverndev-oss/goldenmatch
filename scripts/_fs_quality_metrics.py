"""Ranking-quality metrics for the GM-vs-Splink comparison.

## Why this is a shared module rather than a function in each harness

The two engines are compared on the quality of the model each one TRAINS, so
the metric must not also differ between them. Two hand-written average-precision
implementations that disagree by a percent would be indistinguishable from a
model that is a percent better, and the whole point of the exercise is to tell
those apart. One implementation, imported by both arms.

## Why average precision and not F1 at a threshold

The engines calibrate differently, and a threshold comparison would measure the
calibration rather than the model. Average precision integrates over every
threshold, so it answers the question that actually matters -- does this model
RANK true pairs above false ones -- without either side being credited or
penalised for where it happens to put a cut. Best-F1 is reported alongside it
because it is the number practitioners recognise, with the threshold that
achieved it so it cannot be read as a threshold-free result.

## The base rate is reported too, and it is not decoration

Average precision is bounded below by the positive rate, so 0.90 means something
very different at a 1% base rate than at a 60% one. A comparison that omits it
invites reading a number that a constant classifier could reach.
"""
from __future__ import annotations


def ranking_metrics(scored: list[tuple[float, bool]]) -> dict:
    """Average precision, best F1, and the base rate, from (score, is_true).

    Ties are handled by grouping equal scores into a single threshold step.
    Treating tied scores as if the true ones came first inflates average
    precision, and comparison vectors collide HEAVILY here -- millions of pairs
    share a few hundred distinct weights -- so tie handling is not a detail on
    this data, it is most of the ranking.
    """
    if not scored:
        return {"average_precision": None, "best_f1": None,
                "best_f1_threshold": None, "n_pairs": 0, "n_true": 0,
                "base_rate": None}

    n = len(scored)
    n_true = sum(1 for _s, t in scored if t)
    if n_true == 0:
        return {"average_precision": None, "best_f1": None,
                "best_f1_threshold": None, "n_pairs": n, "n_true": 0,
                "base_rate": 0.0}

    # Descending by score; ties adjacent so they can be consumed as one step.
    scored = sorted(scored, key=lambda st: -st[0])

    tp = fp = 0
    prev_recall = 0.0
    ap = 0.0
    best_f1 = 0.0
    best_thr = None

    i = 0
    while i < n:
        thr = scored[i][0]
        j = i
        while j < n and scored[j][0] == thr:
            if scored[j][1]:
                tp += 1
            else:
                fp += 1
            j += 1
        # Precision/recall AFTER admitting the whole tie group -- the only
        # defensible point, since a threshold cannot separate equal scores.
        precision = tp / (tp + fp)
        recall = tp / n_true
        ap += precision * (recall - prev_recall)
        prev_recall = recall
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        if f1 > best_f1:
            best_f1, best_thr = f1, thr
        i = j

    return {
        "average_precision": round(ap, 6),
        "best_f1": round(best_f1, 6),
        "best_f1_threshold": (round(best_thr, 6) if best_thr is not None else None),
        "n_pairs": n,
        "n_true": n_true,
        "base_rate": round(n_true / n, 6),
    }
