"""Unit tests for unsupervised recall estimation (core/recall_certificate.py)."""
from __future__ import annotations

from goldenmatch.core.recall_certificate import (
    audit_calibrated_bound,
    clusters_to_pairs,
    estimate_recall,
    wilson_ci,
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


def test_wilson_ci_brackets_proportion():
    lo, hi = wilson_ci(45, 50)
    assert lo < 0.9 < hi
    assert 0.0 <= lo <= hi <= 1.0
    assert wilson_ci(0, 0) == (0.0, 1.0)


def test_audit_bound_is_safe_and_ordered():
    # 1000 matched (all true, audited 50/50), 2000 sub-threshold candidates with
    # ~10% true (audited 5/50). true found=1000, missed=200 -> true recall ~0.833.
    cert = audit_calibrated_bound(
        found_size=1000, sub_size=2000,
        a_true=50, a_n=50,        # precision ~1.0
        b_true=5, b_n=50,         # miss rate ~0.10
        c_true=0, c_n=50,         # blocking-completeness check passes
    )
    assert cert.recall_lower <= cert.recall <= cert.recall_upper
    assert cert.recall_lower <= 0.833 + 1e-9          # SAFE: bound at/below truth
    assert abs(cert.recall - 0.833) < 0.05            # point estimate close
    assert cert.blocking_complete is True


def test_audit_bound_flags_blocking_violation():
    cert = audit_calibrated_bound(1000, 2000, 50, 50, 5, 50, c_true=3, c_n=50)
    assert cert.blocking_complete is False
    assert "NOT safe" in cert.note


def test_audit_bound_tightens_with_more_labels():
    # same true rates, more labels in B -> tighter (higher) safe lower bound
    loose = audit_calibrated_bound(1000, 2000, 50, 50, 5, 50).recall_lower
    tight = audit_calibrated_bound(1000, 2000, 600, 600, 60, 600).recall_lower
    assert tight > loose
