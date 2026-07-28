"""Tests for FS-score-driven soft confidence targets (honest-yardstick Task 2).

Covers monotonicity, clamping to the hi/lo/mid_hi/mid_lo boundary constants, and
the never-0/1 invariant -- pure stdlib, box-safe (no torch/scipy/numpy/network)."""
from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(__file__))


from fs_enrich import select_hard_negatives, soft_confidence  # noqa: E402


def _cand(a, b, score, gold):  # helper: a scored candidate with its gold label
    return {"a_id": a, "b_id": b, "score": score, "gold_match": gold}


def test_select_keeps_near_threshold_gold_nonmatches_only():
    cands = [
        _cand("a", "b", 0.52, False),  # near tau, gold NON-match -> KEEP (hard neg)
        _cand("c", "d", 0.99, True),  # gold MATCH -> reject (not a negative)
        _cand("e", "f", 0.05, False),  # far below band -> reject (easy)
        _cand("g", "h", 0.48, False),  # near tau, gold NON-match -> KEEP
        _cand("i", "j", 0.51, True),  # near tau but gold MATCH -> reject (proves gold gate)
    ]
    out = select_hard_negatives(cands, tau=0.5, delta=0.1, cap=10)
    kept = {(c["a_id"], c["b_id"]) for c in out}
    assert kept == {("a", "b"), ("g", "h")}


def test_select_caps_and_is_deterministic():
    cands = [_cand(f"a{i}", f"b{i}", 0.5, False) for i in range(20)]
    out = select_hard_negatives(cands, tau=0.5, delta=0.1, cap=5)
    assert len(out) == 5
    assert out == select_hard_negatives(cands, tau=0.5, delta=0.1, cap=5)  # deterministic


def test_select_is_order_independent():
    cands = [_cand(f"a{i}", f"b{i}", 0.5, False) for i in range(20)]
    shuffled = cands[:]
    random.Random(0).shuffle(shuffled)
    assert select_hard_negatives(shuffled, tau=0.5, delta=0.1, cap=5) == select_hard_negatives(
        cands, tau=0.5, delta=0.1, cap=5
    )


def test_soft_confidence_monotonic_and_clamped():
    # match: higher FS score -> higher confidence, never > 0.97, floor 0.55 near/below tau
    assert soft_confidence(1.0, True, tau=0.5) == 0.97
    assert soft_confidence(0.5, True, tau=0.5) == 0.55  # at threshold -> uncertain
    assert soft_confidence(0.1, True, tau=0.5) == 0.55  # matcher missed it -> floor, not 0
    mid = soft_confidence(0.75, True, tau=0.5)
    assert 0.55 < mid < 0.97
    # non-match: lower FS score -> lower confidence, never < 0.03, ceiling 0.45 near/above tau
    assert soft_confidence(0.0, False, tau=0.5) == 0.03
    assert soft_confidence(0.5, False, tau=0.5) == 0.45  # at threshold -> uncertain
    assert soft_confidence(0.9, False, tau=0.5) == 0.45  # matcher wrongly high -> ceiling
    nm = soft_confidence(0.25, False, tau=0.5)
    assert 0.03 < nm < 0.45


def test_soft_confidence_never_hits_0_or_1():
    for s in (0.0, 0.5, 1.0):
        for m in (True, False):
            c = soft_confidence(s, m, tau=0.5)
            assert 0.0 < c < 1.0
