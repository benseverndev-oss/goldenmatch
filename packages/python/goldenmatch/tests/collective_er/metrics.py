"""Pairwise precision/recall/F1 over a clustering vs ground truth."""
from __future__ import annotations
from itertools import combinations


def _pairs(label_of: dict) -> set:
    """Extract all within-group pairs from a label mapping.

    Args:
        label_of: dict mapping record_id -> label (entity or cluster_id)

    Returns:
        set of (a, b) tuples where a < b and both records share the same label
    """
    by_label: dict = {}
    for rid, lab in label_of.items():
        by_label.setdefault(lab, []).append(rid)
    out = set()
    for members in by_label.values():
        for a, b in combinations(sorted(members), 2):
            out.add((a, b))
    return out


def pairwise_prf(clusters: dict, truth: dict) -> tuple[float, float, float]:
    """Compute pairwise precision, recall, and F1 score.

    Pairs are within-group same-entity pairs. A "pair match" occurs when
    two records that should be together (per truth) are predicted together,
    or equivalently when the predicted clustering reproduces a true pair.

    Args:
        clusters: record_id -> predicted cluster_id
        truth: record_id -> true entity label

    Returns:
        (precision, recall, f1) tuple of floats in [0, 1]
    """
    pred = _pairs(clusters)
    gold = _pairs(truth)

    # Edge case: no pairs in either gold or pred
    if not pred and not gold:
        return 1.0, 1.0, 1.0

    # Count true positives (pairs that appear in both)
    tp = len(pred & gold)

    # Precision: of predicted pairs, how many are correct?
    p = tp / len(pred) if pred else 0.0

    # Recall: of true pairs, how many did we predict?
    r = tp / len(gold) if gold else 0.0

    # F1: harmonic mean
    f = 2 * p * r / (p + r) if (p + r) else 0.0

    return p, r, f
