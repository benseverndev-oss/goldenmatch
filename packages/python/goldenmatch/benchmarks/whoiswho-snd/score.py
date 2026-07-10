"""Pairwise-F1 scoring for SND -- the official WhoIsWho metric.

For each ambiguous name we compare the predicted partition of its papers to the
true partition using *pairwise* precision/recall/F1 over same-cluster pairs:

    TP = # pairs that are together in BOTH prediction and truth
    FP = # pairs together in prediction but NOT in truth
    FN = # pairs together in truth but NOT in prediction
    P = TP/(TP+FP)   R = TP/(TP+FN)   F1 = 2PR/(P+R)

The headline number is the **macro-average of per-name F1** (each name weighted
equally), which is exactly the WhoIsWho SND leaderboard metric.

This standalone implementation is parity-tested against goldenmatch's own
``core.evaluate.evaluate_clusters`` (see ``tests/test_score.py``) so the number
we report is defensible without a dispute over "you scored it your way".
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations


@dataclass
class PairwiseScore:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if (self.tp + self.fp) else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if (self.tp + self.fn) else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if (p + r) else 0.0


def _same_cluster_pairs(clusters: list[list[str]]) -> set[frozenset]:
    """The set of unordered paper pairs that share a cluster."""
    pairs: set[frozenset] = set()
    for members in clusters:
        for a, b in combinations(sorted(set(members)), 2):
            pairs.add(frozenset((a, b)))
    return pairs


def pairwise_score_one(pred: list[list[str]], truth: list[list[str]]) -> PairwiseScore:
    """Pairwise TP/FP/FN for ONE name's predicted vs true partition."""
    pp = _same_cluster_pairs(pred)
    tp_pairs = _same_cluster_pairs(truth)
    tp = len(pp & tp_pairs)
    return PairwiseScore(tp=tp, fp=len(pp) - tp, fn=len(tp_pairs) - tp)


def pairwise_f1_macro(
    predictions: dict[str, list[list[str]]],
    ground_truth: dict[str, list[list[str]]],
) -> dict:
    """Macro-averaged Pairwise-F1 over names (the SND headline).

    ``predictions[name]`` and ``ground_truth[name]`` are each a list of
    paper-id lists (one per real author). Only names present in ``ground_truth``
    are scored; a missing prediction for a scored name is treated as all-
    singletons (F1 0 for that name).
    """
    per_name: dict[str, dict] = {}
    f1s: list[float] = []
    ps: list[float] = []
    rs: list[float] = []
    # micro accumulators (pair-weighted) reported alongside for context
    micro = PairwiseScore()
    for name, truth in ground_truth.items():
        pred = predictions.get(name, [])
        if not pred:
            # all-singletons fallback: flatten truth to singletons
            pred = [[pid] for cluster in truth for pid in cluster]
        s = pairwise_score_one(pred, truth)
        per_name[name] = {
            "precision": s.precision, "recall": s.recall, "f1": s.f1,
            "tp": s.tp, "fp": s.fp, "fn": s.fn,
        }
        f1s.append(s.f1)
        ps.append(s.precision)
        rs.append(s.recall)
        micro.tp += s.tp
        micro.fp += s.fp
        micro.fn += s.fn

    n = len(f1s) or 1
    return {
        "pairwise_f1_macro": sum(f1s) / n,
        "pairwise_precision_macro": sum(ps) / n,
        "pairwise_recall_macro": sum(rs) / n,
        "pairwise_f1_micro": micro.f1,
        "n_names": len(f1s),
        "per_name": per_name,
    }


def ground_truth_clusters(gt: dict) -> dict[str, list[list[str]]]:
    """Normalize a WhoIsWho ground-truth blob to ``name -> [[pid...], ...]``.

    Handles both valid-set shape (``name -> [[pid...], ...]`` already) and
    train-set shape (``name -> {author_id -> [pid...]}``).
    """
    out: dict[str, list[list[str]]] = {}
    for name, val in gt.items():
        if isinstance(val, dict):  # train: aid -> [pid]
            out[name] = [list(pids) for pids in val.values()]
        else:  # valid: [[pid], ...]
            out[name] = [list(c) for c in val]
    return out
