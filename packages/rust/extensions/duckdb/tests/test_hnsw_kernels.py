"""Tests for the native HNSW ANN-blocking UDF (``goldenmatch_hnsw_pairs``).

Native-direct columnar: aggregate an embedding column with ``list(embedding)``
and get canonical candidate pairs back. Uses the goldenhnsw wheel when present,
else a numpy brute fallback — the assertions hold on both paths.
"""
from __future__ import annotations

import duckdb
import numpy as np
import pytest
from goldenmatch_duckdb.functions import register


@pytest.fixture()
def con():
    c = duckdb.connect()
    register(c)
    return c


def _pairs(con, vectors, k, threshold):
    con.execute("CREATE TABLE v (id BIGINT, emb DOUBLE[])")
    rows = [(i, [float(x) for x in row]) for i, row in enumerate(vectors)]
    if rows:
        con.executemany("INSERT INTO v VALUES (?, ?)", rows)
    res = con.sql(
        f"SELECT goldenmatch_hnsw_pairs(list(emb), {k}, {threshold}) FROM v"
    ).fetchone()[0]
    return res or []


class TestHnswPairs:
    def test_empty(self, con):
        assert _pairs(con, [], 5, 0.0) == []

    def test_finds_near_duplicate_pair(self, con):
        # Two nearly-identical unit vectors + one orthogonal — the dup pair (0,1)
        # must surface with a high inner-product score; (·,2) must not clear a
        # high threshold.
        a = [1.0, 0.0, 0.0]
        b = [0.999, 0.0447, 0.0]  # ~cos 0.999 with a
        c = [0.0, 0.0, 1.0]
        pairs = _pairs(con, [a, b, c], 3, 0.9)
        keys = {(p["a"], p["b"]) for p in pairs}
        assert (0, 1) in keys
        assert (0, 2) not in keys and (1, 2) not in keys
        for p in pairs:
            assert p["a"] < p["b"]  # canonical
            assert p["s"] >= 0.9

    def test_matches_brute_force_neighbor_set(self, con):
        rng = np.random.default_rng(0)
        n, dim = 60, 8
        x = rng.standard_normal((n, dim)).astype(np.float32)
        x /= np.linalg.norm(x, axis=1, keepdims=True)
        k = 5
        pairs = _pairs(con, x.tolist(), k, -1.0)
        got = {(p["a"], p["b"]) for p in pairs}

        # brute reference — SAME semantics as the UDF: top-k search results
        # (self is the rank-0 hit), then drop self => up to k-1 neighbors.
        ip = x @ x.T
        want = set()
        for i in range(n):
            topk = [int(j) for j in np.argsort(-ip[i])[:k] if int(j) != i]
            for j in topk:
                want.add((min(i, j), max(i, j)))
        # HNSW (or numpy) recall of the brute candidate set should be very high.
        recall = len(got & want) / len(want)
        assert recall >= 0.95, f"recall {recall}"

    def test_numpy_fallback_is_exact(self, con, monkeypatch):
        # Force the wheel-absent path; the numpy fallback is exact, so it must
        # reproduce the brute top-k neighbor set exactly.
        import goldenmatch_duckdb.hnsw_kernels as hk

        monkeypatch.setattr(hk, "_HAS_HNSW", False)
        rng = np.random.default_rng(1)
        n, dim, k = 40, 6, 4
        x = rng.standard_normal((n, dim)).astype(np.float32)
        x /= np.linalg.norm(x, axis=1, keepdims=True)
        got = {(p["a"], p["b"]) for p in _pairs(con, x.tolist(), k, -1.0)}
        ip = x @ x.T
        want = set()
        for i in range(n):
            for j in (int(t) for t in np.argsort(-ip[i])[:k] if int(t) != i):
                want.add((min(i, j), max(i, j)))
        assert got == want
