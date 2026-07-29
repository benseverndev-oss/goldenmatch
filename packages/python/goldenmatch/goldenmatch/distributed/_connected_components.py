"""Owned weakly-connected-components over an undirected edge list.

GoldenMatch's distributed clustering fallback (``clustering.py``) used
``scipy.sparse.csgraph.connected_components`` to label components on the driver.
scipy was NOT a declared goldenmatch dependency, so that path was a latent
``ImportError`` for anyone without scipy installed. This owns the one graph
primitive it needed -- "own it, don't clone it" -- so the fallback is scipy-free
and self-contained.

``connected_components_undirected`` is byte-identical to
``connected_components(graph, directed=False)`` (same ``(n_components, labels)``,
labels numbered 0..k-1 by ascending minimum node index -- scipy's convention),
verified against scipy in ``tests/test_connected_components_parity.py``. It is
vectorized (min-label propagation with pointer jumping, Awerbuch-Shiloach), a
handful of numpy passes rather than a per-edge Python union-find, to preserve the
vectorized wall the scipy path was chosen for.
"""
from __future__ import annotations

import numpy as np


def connected_components_undirected(
    row: np.ndarray, col: np.ndarray, n: int
) -> tuple[int, np.ndarray]:
    """Weakly-connected components of the undirected graph on ``n`` nodes with
    edges ``(row[i], col[i])``.

    Returns ``(n_components, labels)`` where ``labels`` is an ``int64`` array of
    length ``n`` and ``labels[i]`` is node ``i``'s component id in ``0..k-1``,
    numbered by ascending minimum node index (identical to
    ``scipy.sparse.csgraph.connected_components(..., directed=False)``). Self-loops
    and duplicate edges are ignored; isolated nodes are their own component.
    """
    n = int(n)
    row = np.asarray(row, dtype=np.int64)
    col = np.asarray(col, dtype=np.int64)
    # Each node starts as its own label; propagation drives every node's label to
    # the minimum node index reachable from it (its canonical component root).
    labels = np.arange(n, dtype=np.int64)
    if row.size:
        # Symmetrize (undirected): an edge constrains both endpoints.
        u = np.concatenate([row, col])
        v = np.concatenate([col, row])
        while True:
            m = np.minimum(labels[u], labels[v])
            new = labels.copy()
            np.minimum.at(new, u, m)
            np.minimum.at(new, v, m)
            new = new[new]  # pointer jumping: collapse chains toward the root
            if np.array_equal(new, labels):
                break
            labels = new
    # Each distinct surviving label is a component's min node index. Densify to
    # 0..k-1 in ascending order == scipy's numbering.
    uniq, inv = np.unique(labels, return_inverse=True)
    return int(uniq.size), inv.astype(np.int64).reshape(-1)
