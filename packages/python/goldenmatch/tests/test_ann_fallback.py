"""Tests for the owned numpy all-pairs (flat) exact index in the ANN blocker.

The numpy path is the exact-search reference for medium-scale corpora (the tier
that used to route to faiss ``IndexFlatIP``). It ranks top-k by descending raw
inner product with the same neighbor set an exact ``IndexFlatIP`` returns; these
tests pin that against an independent brute-force reference (and, when faiss
happens to be installed, cross-check against faiss as an oracle).
"""

from __future__ import annotations

import numpy as np
from goldenmatch.core.ann_blocker import ANNBlocker


def _vecs():
    rng = np.random.default_rng(0)
    return rng.standard_normal((20, 8)).astype(np.float32)


def _brute_force_topk_ids(corpus: np.ndarray, top_k: int) -> list[set[int]]:
    """Reference top-k neighbor IDs per row by descending raw inner product.

    Mirrors an exact ``IndexFlatIP.search`` (self NOT excluded, raw IP ranking).
    """
    ip = corpus @ corpus.T
    k = min(top_k, corpus.shape[0])
    return [set(np.argsort(-ip[i])[:k].tolist()) for i in range(corpus.shape[0])]


def test_numpy_exact_index_runs(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ANN_BACKEND", "numpy")
    b = ANNBlocker(top_k=5)
    b.build_index(_vecs())
    assert b._backend == "numpy"
    pairs = b.query_with_scores(_vecs())
    assert pairs, "exact index should produce candidate pairs"
    assert all(a < c for a, c, _ in pairs)                 # canonical (a<b)
    assert all(-1.0001 <= s <= 1.0001 for *_, s in pairs)  # cosine range


def test_owned_flat_search_matches_brute_force(monkeypatch):
    """The owned flat search returns the exact brute-force top-k neighbor set."""
    monkeypatch.setenv("GOLDENMATCH_ANN_BACKEND", "numpy")
    v = _vecs()
    top_k = 5
    b = ANNBlocker(top_k=top_k)
    b.build_index(v)
    _scores, indices = b._search(v)

    want = _brute_force_topk_ids(v, top_k)
    for i in range(len(v)):
        got = {int(j) for j in indices[i] if j >= 0}
        assert got == want[i], f"row {i}: owned top-k {got} != brute-force {want[i]}"


def test_owned_flat_search_matches_faiss_oracle():
    """If faiss is installed, the owned flat search matches faiss IndexFlatIP."""
    import importlib.util

    import pytest
    if importlib.util.find_spec("faiss") is None:
        pytest.skip("faiss not installed")
    import faiss  # noqa: WPS433

    v = _vecs()
    top_k = 5
    index = faiss.IndexFlatIP(v.shape[1])
    index.add(v)
    _fscores, findices = index.search(v, top_k)

    b = ANNBlocker(top_k=top_k)
    b._backend = "numpy"
    b._corpus = v
    _scores, indices = b._np_search(v)

    for i in range(len(v)):
        want = set(findices[i].tolist())
        got = {int(j) for j in indices[i] if j >= 0}
        assert got == want, f"row {i}: owned {got} != faiss {want}"
