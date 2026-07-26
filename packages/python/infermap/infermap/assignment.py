"""Optimal 1:1 assignment via the Hungarian algorithm.

Single-sourced from the Rust reference ``infermap-core::linear_sum_assignment``
(fed here via ``infermap-native``) with a byte-identical pure-Python fallback.
The TS reference is ``core/assignment/hungarian.ts``; all three implement the
same O(n^3) Jonker-Volgenant-lite shortest-path Hungarian with index-order
(deterministic) tie-breaking, so Python and TS now agree on ties -- resolving the
prior scipy-vs-hungarian.ts divergence (scipy picks a different, equally-optimal
assignment when costs tie).

``INFERMAP_NATIVE=auto`` (default) uses the native kernel when the wheel exports
``linear_sum_assignment``; ``=0`` forces the pure-Python port. Both produce
identical output by construction (the pure port mirrors the Rust arithmetic +
iteration order). scipy is no longer on the runtime path.
"""
from __future__ import annotations

import math

from infermap._native_loader import native_enabled, native_module


def _lsa_pure(cost: list[list[float]]) -> list[tuple[int, int]]:
    """Byte-identical reference for ``infermap-core::linear_sum_assignment``.

    MINIMIZES total cost over a (possibly rectangular) matrix, padding to
    ``n = max(rows, cols)`` with a scale-derived big-M so padded slots are only
    taken when forced; drops pairs touching a padded/non-finite cell. Mirrors the
    Rust/TS loop order exactly for cross-language bit-parity.
    """
    rows = len(cost)
    if rows == 0:
        return []
    cols = len(cost[0])
    if cols == 0:
        return []
    n = max(rows, cols)

    max_abs = 0.0
    for i in range(rows):
        for j in range(cols):
            v = cost[i][j]
            if math.isfinite(v):
                a = abs(v)
                if a > max_abs:
                    max_abs = a
    inf = (max_abs + 1.0) * (n + 1) * 4.0 + 1.0

    c = [
        [
            (cost[i][j] if (i < rows and j < cols and math.isfinite(cost[i][j])) else inf)
            for j in range(n)
        ]
        for i in range(n)
    ]

    u = [0.0] * (n + 1)
    v = [0.0] * (n + 1)
    p = [0] * (n + 1)  # p[j] = row assigned to col j (1-indexed; 0 = dummy)
    way = [0] * (n + 1)

    for i in range(1, n + 1):
        p[0] = i
        j0 = 0
        minv = [math.inf] * (n + 1)
        used = [False] * (n + 1)
        while True:
            used[j0] = True
            i0 = p[j0]
            delta = math.inf
            j1 = 0
            for j in range(1, n + 1):
                if not used[j]:
                    cur = c[i0 - 1][j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur
                        way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]
                        j1 = j
            for j in range(0, n + 1):
                if used[j]:
                    u[p[j]] += delta
                    v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]
            p[j0] = p[j1]
            j0 = j1
            if j0 == 0:
                break

    pairs: list[tuple[int, int]] = []
    for j in range(1, n + 1):
        i = p[j]
        if i >= 1:
            ri = i - 1
            cj = j - 1
            if ri < rows and cj < cols and math.isfinite(cost[ri][cj]):
                pairs.append((ri, cj))
    pairs.sort(key=lambda t: (t[0], t[1]))
    return pairs


def _linear_sum_assignment(cost: list[list[float]]) -> list[tuple[int, int]]:
    """Native kernel when available (the reference), else the pure-Python port."""
    if native_enabled("linear_sum_assignment"):
        return [tuple(pair) for pair in native_module().linear_sum_assignment(cost)]
    return _lsa_pure(cost)


def optimal_assign(score_matrix, min_confidence: float = 0.2) -> list[tuple[int, int, float]]:
    """Find optimal 1:1 assignment from a score matrix (higher = better).

    Args:
        score_matrix: M x N matrix of combined scores (higher = better). Accepts a
            numpy array or a nested list.
        min_confidence: Minimum score to keep a mapping. Default 0.2 (was 0.3
            before v0.3). The lower default was chosen empirically: on the
            combined Valentine + synthetic benchmark corpus, 0.2 gives combined
            F1 0.765 vs 0.657 at 0.3. See docs/benchmark.md.

    Returns:
        List of (source_idx, target_idx, score) tuples, filtered by min_confidence.
    """
    # Accept numpy arrays without importing numpy (the kernel takes plain lists).
    outer = list(score_matrix)
    if len(outer) == 0:
        return []
    score = [[float(x) for x in row] for row in outer]
    if len(score[0]) == 0:
        return []

    cost = [[1.0 - s for s in row] for row in score]
    pairs = _linear_sum_assignment(cost)
    results: list[tuple[int, int, float]] = []
    for r, c in pairs:
        s = score[r][c]
        if s >= min_confidence:
            results.append((int(r), int(c), round(s, 4)))
    return results
