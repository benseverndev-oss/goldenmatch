"""Byte-parity gate for the owned connected-components kernel.

Asserts ``goldenmatch.distributed._connected_components.connected_components_undirected``
returns EXACTLY the same ``(n_components, labels)`` as
``scipy.sparse.csgraph.connected_components(..., directed=False)`` across random +
structured graphs (self-loops, duplicate edges, chains, stars, isolated nodes).
The distributed clustering fallback consumes the label integers directly as
cluster ids, so the labeling convention (0..k-1 by ascending min node index) must
match. scipy is a test-only oracle; the runtime is scipy-free.
"""
from __future__ import annotations

import numpy as np
import pytest
from goldenmatch.distributed._connected_components import connected_components_undirected

csgraph = pytest.importorskip("scipy.sparse.csgraph")
sparse = pytest.importorskip("scipy.sparse")


def _scipy_labels(row: np.ndarray, col: np.ndarray, n: int) -> tuple[int, np.ndarray]:
    data = np.ones(np.asarray(row).shape[0], dtype=np.int8)
    graph = sparse.csr_matrix((data, (row, col)), shape=(n, n))
    return csgraph.connected_components(graph, directed=False)


def _assert_match(row, col, n) -> None:
    row = np.asarray(row, dtype=np.int64)
    col = np.asarray(col, dtype=np.int64)
    nc_s, lab_s = _scipy_labels(row, col, n)
    nc_o, lab_o = connected_components_undirected(row, col, n)
    assert nc_o == nc_s
    assert np.array_equal(lab_o, lab_s)


@pytest.mark.parametrize("seed", range(50))
def test_random_graphs_match_scipy(seed: int) -> None:
    rng = np.random.default_rng(seed)
    n = int(rng.integers(1, 80))
    e = int(rng.integers(0, n * 2 + 1))
    if e and n > 1:
        row = rng.integers(0, n, e)
        col = rng.integers(0, n, e)
    else:
        row = np.array([], dtype=np.int64)
        col = np.array([], dtype=np.int64)
    _assert_match(row, col, n)


def test_structured_graphs_match_scipy() -> None:
    _assert_match([], [], 10)              # all isolated
    _assert_match([], [], 1)               # single node
    _assert_match([0], [0], 1)             # self-loop only
    _assert_match([0, 1, 2, 2], [0, 1, 2, 3], 5)   # self-loops + one edge
    _assert_match([0, 0, 0, 1], [1, 1, 1, 2], 4)   # duplicate edges
    _assert_match(list(range(199)), list(range(1, 200)), 200)  # chain
    _assert_match([0] * 99, list(range(1, 100)), 100)          # star


def test_two_components_labels_by_min_index() -> None:
    # Nodes {0,2} and {1,3} -> component of node 0 is label 0, node 1 is label 1.
    nc, labels = connected_components_undirected(
        np.array([0, 1]), np.array([2, 3]), 4
    )
    assert nc == 2
    assert labels.tolist() == [0, 1, 0, 1]
