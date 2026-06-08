"""Unsupervised recall estimation for a multi-matchkey run (no ground truth).

In production you can estimate PRECISION cheaply (sample matches, check them) but
RECALL is normally unknowable without labels -- you can't sample the true matches
you didn't find. This estimates the recall of a multi-matchkey / multi-pass run
WITHOUT ground truth, via capture-recapture (the dual-system math used for census
undercount): each matchkey/pass is treated as a decorrelated "system"; the
overlap structure of which systems matched each pair estimates how many true
pairs every system missed -> the recall of the run's union.

This is a POINT estimate. A trustworthy *lower bound* additionally needs a small
labelled audit of the sub-threshold candidate stratum (see the recall-assurance
research notes); it cannot be obtained from the capture data alone because the
pairs no system ever proposes are fundamentally invisible.

Method + assumptions (validated in scripts/research/, incl. on real GoldenMatch
output -- RESULTS-phase0-goldenmatch.md):
  * >= 3 decorrelated systems (multi_pass blocking and/or multiple matchkeys).
  * FALSE POSITIVES are ~all singletons: a spurious match by one system is rarely
    reproduced by an independent one, so the multi-capture cells f_k (k>=2) are
    ~FP-free. We fit the true-pair capture model from those cells and IGNORE the
    FP-contaminated singleton cell (naive Chao2 on the raw union is wrecked by FP
    singletons; this fix is what makes it work at scale).
  * Homogeneous capture probability across true pairs. Under the binomial capture
    model, recall of the union = 1 - (1-p)^K, with p fit from the slope of
    log f_k - log C(K,k) over k>=2. Heterogeneity makes the estimate mildly
    optimistic (flagged via the overlap diagnostic).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

Pair = tuple[int, int]


@dataclass
class RecallEstimate:
    """Result of an unsupervised recall estimate. `recall` is None when the
    capture structure can't support an estimate (see `note`)."""
    recall: float | None
    n_systems: int
    found_pairs: int
    per_system_capture_prob: float          # fitted p (per-system capture prob)
    mean_overlap: float                     # decorrelation diagnostic in [0,1]
    capture_histogram: dict[int, int] = field(default_factory=dict)
    estimable: bool = False
    note: str = ""


def _fit_capture_prob(counts: dict[Pair, int], K: int) -> float | None:
    """Fit p from the FP-free higher-order cells: regress log f_k - log C(K,k)
    on k for k>=2; slope = logit(p). Returns None if <2 usable cells."""
    ck = {k: 0 for k in range(1, K + 1)}
    for v in counts.values():
        if 1 <= v <= K:
            ck[v] += 1
    pts = [(k, ck[k]) for k in range(2, K + 1) if ck[k] > 0]
    if len(pts) < 2:
        return None
    xs = [float(k) for k, _ in pts]
    ys = [math.log(c) - math.log(math.comb(K, k)) for k, c in pts]
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return 1.0 / (1.0 + math.exp(-b))


def estimate_recall(pairsets: list[set[Pair]]) -> RecallEstimate:
    """Estimate the recall of the UNION of `pairsets`, where each set is the
    matches found by one decorrelated system (matchkey/pass). No labels needed.

    Returns a `RecallEstimate`; `.recall is None` (with an explanatory `.note`)
    when there are too few decorrelated systems or too few multi-captured pairs
    to support an estimate.
    """
    K = len(pairsets)
    union: set[Pair] = set().union(*pairsets) if pairsets else set()
    counts: dict[Pair, int] = {}
    for ps in pairsets:
        for p in ps:
            counts[p] = counts.get(p, 0) + 1
    hist = {k: sum(1 for v in counts.values() if v == k) for k in range(1, K + 1)}

    overlaps: list[float] = []
    for a in range(K):
        for b in range(a + 1, K):
            A, B = pairsets[a], pairsets[b]
            if A or B:
                overlaps.append(len(A & B) / len(A | B))
    mean_overlap = sum(overlaps) / len(overlaps) if overlaps else 0.0

    if K < 3:
        return RecallEstimate(
            None, K, len(union), 0.0, mean_overlap, hist, False,
            "need >=3 decorrelated systems (enable multi_pass blocking or use "
            ">=3 matchkeys) to estimate recall",
        )
    p = _fit_capture_prob(counts, K)
    if p is None or not (0.0 < p < 1.0):
        return RecallEstimate(
            None, K, len(union), p or 0.0, mean_overlap, hist, False,
            "too few multi-captured pairs to estimate (systems too correlated, "
            "or too few matches)",
        )
    recall = 1.0 - (1.0 - p) ** K
    note = ("point estimate (no labels); a trustworthy lower bound needs a small "
            "labelled audit")
    if mean_overlap > 0.85:
        note += "; WARNING: high system overlap -> systems correlated, estimate may be optimistic"
    return RecallEstimate(recall, K, len(union), p, mean_overlap, hist, True, note)


def clusters_to_pairs(clusters) -> set[Pair]:
    """Convert a dedupe `clusters` dict (cluster -> {'members': [row_id,...]}) to
    the set of within-cluster (row_id, row_id) pairs."""
    out: set[Pair] = set()
    for cl in (clusters or {}).values():
        members = cl.get("members", []) if isinstance(cl, dict) else getattr(cl, "members", [])
        ms = sorted(int(m) for m in members)
        for i in range(len(ms)):
            for j in range(i + 1, len(ms)):
                out.add((ms[i], ms[j]))
    return out
