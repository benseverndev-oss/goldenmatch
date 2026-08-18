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


def ranking_metrics_grouped(groups: list[tuple[float, int, int]]) -> dict:
    """Identical metrics, from PRE-GROUPED (score, n_true, n_total) rows.

    ## Why this exists: `ranking_metrics` cannot reach 50M

    The caller built its input by collecting one `(score, is_true)` tuple PER
    CANDIDATE PAIR to the driver. At the 1M scale that harness was written for
    this is 5.5M tuples and merely wasteful. At 50M rows it is **275M** tuples
    -- tens of GB of Python objects on a driver running in a container -- so the
    quality arm cannot run at the scale the comparison is actually about, and a
    head-to-head with no accuracy number is not a head-to-head.

    ## Why grouping is EXACT here, not an approximation

    `ranking_metrics` already consumes its input in tie-groups: it advances to
    the next distinct score, admits every pair at that score, and only then
    computes precision/recall. So the per-pair identity of the rows inside a tie
    group is never used -- only how many there are and how many are true. That
    is precisely `(score, n_true, n_total)`.

    And the distinct-score count is SMALL by construction. A pair's weight is a
    sum of per-field match weights over a bounded set of gamma levels, so the
    reachable score set is bounded by `prod(levels + 1)` -- the same bound that
    makes GoldenMatch's counting GROUP BY small, which is the property this
    whole benchmark exists to demonstrate. Grouping in the engine turns a 275M
    collect into a few hundred rows.

    Floating-point equality is the grouping key, matching the `==` tie-check in
    `ranking_metrics`. Two scores that differ in the last bit are two groups
    here and two tie-groups there, so the two functions agree exactly rather
    than approximately -- `test_fs_quality_metrics_grouped.py` asserts that on
    shared inputs.

    Args:
        groups: `(score, n_true, n_total)` per DISTINCT score, any order.
            `n_total` counts every pair at that score, true and false.
    """
    if not groups:
        return {"average_precision": None, "best_f1": None,
                "best_f1_threshold": None, "n_pairs": 0, "n_true": 0,
                "base_rate": None}

    n = sum(int(g[2]) for g in groups)
    n_true = sum(int(g[1]) for g in groups)
    if n == 0:
        return {"average_precision": None, "best_f1": None,
                "best_f1_threshold": None, "n_pairs": 0, "n_true": 0,
                "base_rate": None}
    if n_true == 0:
        return {"average_precision": None, "best_f1": None,
                "best_f1_threshold": None, "n_pairs": n, "n_true": 0,
                "base_rate": 0.0}

    # Descending by score -- the same order the ungrouped walk induces.
    rows = sorted(groups, key=lambda g: -g[0])

    tp = fp = 0
    prev_recall = 0.0
    ap = 0.0
    best_f1 = 0.0
    best_thr = None

    for thr, g_true, g_total in rows:
        g_true = int(g_true)
        tp += g_true
        fp += int(g_total) - g_true
        # Precision/recall AFTER admitting the whole tie group -- the only
        # defensible point, since a threshold cannot separate equal scores.
        precision = tp / (tp + fp)
        recall = tp / n_true
        ap += precision * (recall - prev_recall)
        prev_recall = recall
        f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
        if f1 > best_f1:
            best_f1, best_thr = f1, thr

    return {
        "average_precision": round(ap, 6),
        "best_f1": round(best_f1, 6),
        "best_f1_threshold": (round(best_thr, 6) if best_thr is not None else None),
        "n_pairs": n,
        "n_true": n_true,
        "base_rate": round(n_true / n, 6),
    }
