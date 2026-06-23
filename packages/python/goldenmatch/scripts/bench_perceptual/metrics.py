"""Reusable ER evaluation metrics (stdlib-only, no goldenmatch import).

The framework core of the perceptual bench harness, kept generic so other ER
stages (blocking, fuzzy/FS scoring) can reuse it: a *scorer* is anything that
yields ``(is_match, score)`` per pair; a *blocker* yields candidate pairs. These
functions turn those into the metrics that actually drive iteration — precision /
recall / F1, the best operating point, candidate-reduction, and the discrimination
margin between matched and non-matched pairs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass
class PRF:
    threshold: float
    precision: float
    recall: float
    f1: float
    tp: int
    fp: int
    fn: int

    def as_dict(self) -> dict:
        return asdict(self)


def prf_at_threshold(labeled_scores: list[tuple[bool, float]], threshold: float) -> PRF:
    """Precision/recall/F1 treating ``score >= threshold`` as a predicted match."""
    tp = fp = fn = 0
    for is_match, score in labeled_scores:
        pred = score >= threshold
        if pred and is_match:
            tp += 1
        elif pred and not is_match:
            fp += 1
        elif not pred and is_match:
            fn += 1
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return PRF(threshold, precision, recall, f1, tp, fp, fn)


def threshold_sweep(
    labeled_scores: list[tuple[bool, float]], thresholds: list[float]
) -> tuple[list[PRF], PRF]:
    """Sweep thresholds; return all points and the best-F1 operating point."""
    points = [prf_at_threshold(labeled_scores, t) for t in thresholds]
    best = max(points, key=lambda p: (p.f1, p.recall))
    return points, best


@dataclass
class Discrimination:
    match_min: float
    match_mean: float
    match_max: float
    nonmatch_min: float
    nonmatch_mean: float
    nonmatch_max: float
    separation: float  # match_mean - nonmatch_mean (higher = cleaner signal)
    overlap_count: int  # non-match scores >= the lowest match score

    def as_dict(self) -> dict:
        return asdict(self)


def discrimination(labeled_scores: list[tuple[bool, float]]) -> Discrimination:
    """Separation of the matched vs non-matched score distributions — the signal
    quality the threshold then has to split."""
    m = [s for is_m, s in labeled_scores if is_m]
    n = [s for is_m, s in labeled_scores if not is_m]
    if not m or not n:
        raise ValueError("need both matched and non-matched pairs")
    mlo = min(m)
    overlap = sum(1 for s in n if s >= mlo)
    mm, nm = sum(m) / len(m), sum(n) / len(n)
    return Discrimination(mlo, mm, max(m), min(n), nm, max(n), mm - nm, overlap)


@dataclass
class BlockingEval:
    gt_pairs: int
    candidate_pairs: int
    total_pairs: int
    recall: float  # fraction of true pairs that share a candidate block
    reduction_ratio: float  # 1 - candidates / total possible pairs

    def as_dict(self) -> dict:
        return asdict(self)


def blocking_eval(
    candidate_pairs: set[tuple[int, int]], gt_pairs: set[tuple[int, int]], n_items: int
) -> BlockingEval:
    """Recall (true pairs covered) vs candidate-reduction for a blocker output.

    Pairs are canonical ``(min, max)`` tuples — the project-wide invariant."""
    total = n_items * (n_items - 1) // 2
    covered = len(candidate_pairs & gt_pairs)
    recall = covered / len(gt_pairs) if gt_pairs else 0.0
    reduction = 1.0 - (len(candidate_pairs) / total) if total else 0.0
    return BlockingEval(len(gt_pairs), len(candidate_pairs), total, recall, reduction)


def per_group_recall(
    labeled_scores_by_group: dict[str, list[tuple[bool, float]]], threshold: float
) -> dict[str, float]:
    """Recall at a threshold, broken down by group (e.g. per transform) — shows
    exactly which perturbations the hash survives and which break it."""
    out: dict[str, float] = {}
    for group, scores in labeled_scores_by_group.items():
        prf = prf_at_threshold(scores, threshold)
        out[group] = prf.recall
    return out
