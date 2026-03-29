"""Optimal assignment via the Hungarian algorithm."""
from __future__ import annotations
import numpy as np
from scipy.optimize import linear_sum_assignment


def optimal_assign(score_matrix: np.ndarray, min_confidence: float = 0.3) -> list[tuple[int, int, float]]:
    """Find optimal 1:1 assignment from score matrix.
    Args:
        score_matrix: M x N matrix of combined scores (higher = better).
        min_confidence: Minimum score to keep a mapping.
    Returns:
        List of (source_idx, target_idx, score) tuples, filtered by min_confidence.
    """
    if score_matrix.size == 0:
        return []
    cost_matrix = 1.0 - score_matrix
    row_indices, col_indices = linear_sum_assignment(cost_matrix)
    results = []
    for r, c in zip(row_indices, col_indices):
        score = float(score_matrix[r, c])
        if score >= min_confidence:
            results.append((int(r), int(c), round(score, 4)))
    return results
