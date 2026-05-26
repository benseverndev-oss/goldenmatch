"""Correctness tests for the Native Core pair primitives (pure-Python path).

These force GOLDENMATCH_NATIVE=0 so they exercise the Python reference
regardless of whether the compiled extension is present — the native path is
covered by the parity tests in test_native_parity.py.
"""
from __future__ import annotations

import pytest
from goldenmatch.core.pairs import (
    block_histogram,
    candidate_pair_count,
    canonicalize_pairs,
    connected_components,
    dedup_pairs_max_score,
)


@pytest.fixture(autouse=True)
def _force_python(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")


def test_canonicalize_orients_and_preserves_order():
    assert canonicalize_pairs([(2, 1, 0.5), (1, 2, 0.9), (5, 5, 0.3)]) == [
        (1, 2, 0.5),
        (1, 2, 0.9),
        (5, 5, 0.3),
    ]


def test_canonicalize_empty():
    assert canonicalize_pairs([]) == []


def test_dedup_keeps_max_and_sorts():
    out = dedup_pairs_max_score([(2, 1, 0.5), (1, 2, 0.9), (3, 4, 0.2), (4, 3, 0.7)])
    assert out == [(1, 2, 0.9), (3, 4, 0.7)]


def test_dedup_tie_keeps_first():
    # Equal scores -> strict-> guard never replaces, so the first wins.
    out = dedup_pairs_max_score([(1, 2, 0.5), (2, 1, 0.5)])
    assert out == [(1, 2, 0.5)]


def test_dedup_output_is_sorted_by_key():
    out = dedup_pairs_max_score([(9, 8, 0.1), (1, 2, 0.2), (5, 5, 0.3)])
    assert [k[:2] for k in out] == [(1, 2), (5, 5), (8, 9)]


def test_candidate_pair_count_basic():
    # C(3,2)=3, C(1,2)=0, C(4,2)=6, C(2,2)=1 -> 10
    assert candidate_pair_count([3, 1, 4, 2]) == 10


def test_candidate_pair_count_empty_and_singletons():
    assert candidate_pair_count([]) == 0
    assert candidate_pair_count([1, 1, 0, 1]) == 0


def test_candidate_pair_count_large_no_overflow():
    # Single big block: C(200_000_000, 2) ~ 2e16, well within int.
    n = 200_000_000
    assert candidate_pair_count([n]) == n * (n - 1) // 2


def test_block_histogram_empty():
    assert block_histogram([]) == {
        "count": 0,
        "total_records": 0,
        "max": 0,
        "p50": 0,
        "p95": 0,
        "p99": 0,
    }


def test_block_histogram_stats():
    h = block_histogram([1, 2, 3, 4, 5])
    assert h["count"] == 5
    assert h["total_records"] == 15
    assert h["max"] == 5
    # nearest-rank: p50 -> ceil(0.5*5)-1 = 2 -> sizes[2] = 3
    assert h["p50"] == 3
    assert h["p95"] == 5
    assert h["p99"] == 5


def test_connected_components_groups_and_singletons():
    comps = connected_components(
        [(1, 2, 0.9), (2, 3, 0.8), (10, 11, 0.7)], [1, 2, 3, 10, 11, 42]
    )
    as_sets = {frozenset(c) for c in comps}
    assert as_sets == {frozenset({1, 2, 3}), frozenset({10, 11}), frozenset({42})}


def test_connected_components_derives_ids_from_pairs():
    comps = connected_components([(1, 2, 0.9), (3, 4, 0.5)])
    as_sets = {frozenset(c) for c in comps}
    assert as_sets == {frozenset({1, 2}), frozenset({3, 4})}
