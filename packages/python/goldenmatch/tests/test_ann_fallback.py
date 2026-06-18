"""Tests for the numpy all-pairs cosine fallback in the ANN blocker.

The fallback path runs when faiss is absent (or explicitly forced off via the
module-level ``_HAS_FAISS`` flag). It must produce the SAME neighbor set as
faiss for small N (parity) with zero new dependencies.
"""

from __future__ import annotations

import numpy as np
from goldenmatch.core.ann_blocker import ANNBlocker


def _vecs():
    rng = np.random.default_rng(0)
    return rng.standard_normal((20, 8)).astype(np.float32)


def test_numpy_fallback_runs_without_faiss(monkeypatch):
    monkeypatch.setattr("goldenmatch.core.ann_blocker._HAS_FAISS", False, raising=False)
    b = ANNBlocker(top_k=5)
    b.build_index(_vecs())
    pairs = b.query_with_scores(_vecs())
    assert pairs, "fallback should produce candidate pairs"
    assert all(a < c for a, c, _ in pairs)                 # canonical (a<b)
    assert all(-1.0001 <= s <= 1.0001 for *_, s in pairs)  # cosine range


def test_fallback_parity_with_faiss_small_n():
    import importlib.util

    import pytest
    if importlib.util.find_spec("faiss") is None:
        pytest.skip("faiss not installed")
    v = _vecs()
    faiss_b = ANNBlocker(top_k=5); faiss_b.build_index(v)
    faiss_pairs = {(a, c) for a, c, _ in faiss_b.query_with_scores(v)}
    # force numpy on a fresh blocker
    import goldenmatch.core.ann_blocker as m
    orig = m._HAS_FAISS
    try:
        m._HAS_FAISS = False
        np_b = ANNBlocker(top_k=5); np_b.build_index(v)
        np_pairs = {(a, c) for a, c, _ in np_b.query_with_scores(v)}
    finally:
        m._HAS_FAISS = orig
    assert faiss_pairs == np_pairs                          # same neighbor SET
