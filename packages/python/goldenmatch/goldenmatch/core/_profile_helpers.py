"""Numeric helpers for profile emission. Used by scorer + cluster instrumentation."""
from __future__ import annotations


def histogram_20(scores: list[float]) -> list[int]:
    """20 fixed bins over [0, 1]. Score >= 1.0 lands in bin 19."""
    bins = [0] * 20
    for s in scores:
        idx = min(19, max(0, int(s * 20)))
        bins[idx] += 1
    return bins


def hartigan_dip(scores: list[float]) -> float:
    """Hartigan's dip statistic. Returns value in [0, 0.25]; small=unimodal.

    Hard-requires the ``diptest`` package (added as dep in Task 2.2).
    """
    if not scores:
        return 0.0
    import diptest
    import numpy as np
    return float(diptest.dipstat(np.asarray(scores)))


def mass_above(scores: list[float], threshold: float) -> float:
    if not scores:
        return 0.0
    return sum(1 for s in scores if s >= threshold) / len(scores)


def mass_borderline(scores: list[float], threshold: float, band: float = 0.1) -> float:
    if not scores:
        return 0.0
    lo, hi = threshold - band, threshold + band
    return sum(1 for s in scores if lo <= s <= hi) / len(scores)


def transitivity_rate(
    members_by_cluster: dict[int, list[int]],
    pair_scores: dict[tuple[int, int], float],
    threshold: float,
    *,
    max_samples: int = 1000,
    seed: int = 0,
) -> float:
    """Fraction of in-cluster (a,b,c) triples where all three of (a,b), (b,c),
    (a,c) score >= threshold.

    Pair lookup canonicalizes (a,b) as (min,max) per project convention.
    Returns 1.0 when no clusters have >= 3 members (vacuously transitive).
    Samples up to ``max_samples`` triples for cost control.
    """
    import random
    from itertools import combinations as _combinations

    rng = random.Random(seed)
    triples: list[tuple[int, int, int]] = []
    for members in members_by_cluster.values():
        if len(members) < 3:
            continue
        n = len(members)
        if n <= 20:
            triples.extend(_combinations(sorted(members), 3))
        else:
            for _ in range(min(max_samples, 100)):
                a, b, c = sorted(rng.sample(members, 3))
                triples.append((a, b, c))
    if not triples:
        return 1.0
    if len(triples) > max_samples:
        triples = rng.sample(triples, max_samples)

    def edge(x: int, y: int) -> float:
        return pair_scores.get((min(x, y), max(x, y)), 0.0)

    agree = sum(
        1 for a, b, c in triples
        if edge(a, b) >= threshold
        and edge(b, c) >= threshold
        and edge(a, c) >= threshold
    )
    return agree / len(triples)
