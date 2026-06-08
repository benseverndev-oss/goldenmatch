"""Unit tests for unsupervised recall estimation (core/recall_certificate.py)."""
from __future__ import annotations

from goldenmatch.core.recall_certificate import (
    clusters_to_pairs,
    estimate_recall,
)


def _build(K: int, spec: dict[int, int]) -> list[set]:
    """Build K system pairsets with `spec[k]` pairs captured by exactly k systems.
    (Which systems doesn't affect the recall fit, only the overlap diagnostic.)"""
    sets: list[set] = [set() for _ in range(K)]
    pid = 0
    for k, n in spec.items():
        for _ in range(n):
            pair = (pid, pid + 1)
            pid += 2
            for s in range(k):
                sets[s].add(pair)
    return sets


def test_needs_three_systems():
    est = estimate_recall(_build(2, {1: 50, 2: 50}))
    assert est.recall is None
    assert not est.estimable
    assert ">=3" in est.note


def test_recovers_known_recall():
    # capture counts ~ Binomial(K=4, p=0.6) * 1000 -> recall = 1-(1-0.6)^4 ~= 0.974
    est = estimate_recall(_build(4, {1: 154, 2: 346, 3: 346, 4: 130}))
    assert est.estimable
    assert est.recall is not None
    assert abs(est.recall - 0.974) < 0.05
    assert 0.5 < est.per_system_capture_prob < 0.7


def test_fp_robust_ignores_singleton_cell():
    # The singleton cell is FP-contaminated; flooding it must not move the estimate
    # (the estimator fits from k>=2 only).
    base = estimate_recall(_build(4, {2: 346, 3: 346, 4: 130})).recall
    flooded = estimate_recall(_build(4, {1: 20000, 2: 346, 3: 346, 4: 130})).recall
    assert base is not None and flooded is not None
    assert abs(base - flooded) < 0.03


def test_too_few_multicaptured_not_estimable():
    # all singletons -> no k>=2 cells -> cannot estimate
    est = estimate_recall(_build(3, {1: 100}))
    assert est.recall is None
    assert not est.estimable


def test_clusters_to_pairs():
    clusters = {0: {"members": [3, 1, 2]}, 1: {"members": [5]}}
    assert clusters_to_pairs(clusters) == {(1, 2), (1, 3), (2, 3)}
