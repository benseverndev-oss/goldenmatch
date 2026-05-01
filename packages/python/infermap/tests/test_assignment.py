"""Tests for infermap.assignment.optimal_assign."""
from __future__ import annotations

import numpy as np

from infermap.assignment import optimal_assign


def test_perfect_diagonal():
    """3x3 identity matrix should yield 3 assignments on the diagonal."""
    matrix = np.eye(3)
    result = optimal_assign(matrix)
    assert len(result) == 3
    assert (0, 0, 1.0) in result
    assert (1, 1, 1.0) in result
    assert (2, 2, 1.0) in result


def test_filters_below_threshold():
    """Row with all scores below min_confidence should be excluded."""
    matrix = np.array([
        [0.9, 0.8],
        [0.1, 0.2],
    ])
    result = optimal_assign(matrix, min_confidence=0.3)
    assert len(result) == 1
    assert result[0][0] == 0  # only source index 0 maps


def test_more_sources_than_targets():
    """3x1 matrix: only 1 assignment is possible."""
    matrix = np.array([[0.9], [0.7], [0.5]])
    result = optimal_assign(matrix)
    assert len(result) == 1


def test_more_targets_than_sources():
    """1x3 matrix: only 1 assignment is possible."""
    matrix = np.array([[0.4, 0.9, 0.6]])
    result = optimal_assign(matrix)
    assert len(result) == 1
    assert result[0] == (0, 1, 0.9)


def test_all_zeros():
    """All-zero 3x3 matrix with min_confidence=0.3 yields no assignments."""
    matrix = np.zeros((3, 3))
    result = optimal_assign(matrix, min_confidence=0.3)
    assert result == []


def test_single_cell():
    """1x1 matrix returns a single assignment."""
    matrix = np.array([[0.85]])
    result = optimal_assign(matrix)
    assert result == [(0, 0, 0.85)]


def test_optimal_not_greedy():
    """Hungarian should prefer total-optimal assignment over greedy max-first.

    Greedy: pick (0,0)=0.9, then (1,1)=0.5  → total 1.40
    Optimal: pick (0,1)=0.8 + (1,0)=0.85    → total 1.65
    """
    matrix = np.array([
        [0.9, 0.8],
        [0.85, 0.5],
    ])
    result = optimal_assign(matrix, min_confidence=0.3)
    assert len(result) == 2
    assignments = {(r, c): s for r, c, s in result}
    assert assignments[(0, 1)] == 0.8
    assert assignments[(1, 0)] == 0.85
