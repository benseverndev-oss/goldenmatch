"""Tests for the native HNSW backend of :class:`ANNBlocker`.

Two layers:

* **Resolver logic** (no wheel needed) — monkeypatch the ``_HAS_*`` flags and
  assert :func:`_resolve_backend` honors the forced env var + the ``auto`` size
  gate (native HNSW -> FAISS -> numpy).
* **Integration** (skipped unless ``goldenmatch-hnsw`` is installed) — build a
  corpus large enough to trip the size gate and assert the HNSW path returns
  high-recall, correctly-scored, incrementally-extensible results matching the
  exact numpy path.
"""

from __future__ import annotations

import goldenmatch.core.ann_blocker as ab
import numpy as np
import pytest
from goldenmatch.core.ann_blocker import ANNBlocker, _resolve_backend

_HNSW_MIN = 4096  # keep in sync with _HNSW_MIN_DEFAULT


# ── resolver logic (no native wheel required) ───────────────────────────────


def test_resolve_forced_backends(monkeypatch):
    monkeypatch.setattr(ab, "_HAS_HNSW", True)
    monkeypatch.setattr(ab, "_HAS_FAISS", True)
    monkeypatch.setenv("GOLDENMATCH_ANN_BACKEND", "numpy")
    assert _resolve_backend(10_000, 20) == "numpy"
    monkeypatch.setenv("GOLDENMATCH_ANN_BACKEND", "faiss")
    assert _resolve_backend(10, 5) == "faiss"
    monkeypatch.setenv("GOLDENMATCH_ANN_BACKEND", "hnsw")
    # forced hnsw ignores the size gate
    assert _resolve_backend(1, 1) == "hnsw"


def test_resolve_forced_degrades_when_absent(monkeypatch):
    monkeypatch.setattr(ab, "_HAS_HNSW", False)
    monkeypatch.setattr(ab, "_HAS_FAISS", False)
    monkeypatch.setenv("GOLDENMATCH_ANN_BACKEND", "hnsw")
    assert _resolve_backend(10_000, 20) == "numpy"  # no hnsw, no faiss
    monkeypatch.setattr(ab, "_HAS_FAISS", True)
    assert _resolve_backend(10_000, 20) == "faiss"  # hnsw absent -> faiss


def test_resolve_auto_size_gate(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_ANN_BACKEND", raising=False)
    monkeypatch.setattr(ab, "_HAS_HNSW", True)
    monkeypatch.setattr(ab, "_HAS_FAISS", True)
    # below the row floor -> exact (faiss)
    assert _resolve_backend(_HNSW_MIN - 1, 20) == "faiss"
    # at/above the floor with a small top_k -> hnsw
    assert _resolve_backend(_HNSW_MIN, 20) == "hnsw"
    # large top_k (retrieve-nearly-all) is HNSW's bad case -> exact
    assert _resolve_backend(100_000, 100_000) == "faiss"


def test_resolve_auto_prefers_hnsw_over_faiss(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_ANN_BACKEND", raising=False)
    monkeypatch.setattr(ab, "_HAS_HNSW", True)
    monkeypatch.setattr(ab, "_HAS_FAISS", True)
    assert _resolve_backend(10_000, 20) == "hnsw"  # HNSW wins when both present


def test_resolve_auto_falls_back_to_numpy(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_ANN_BACKEND", raising=False)
    monkeypatch.setattr(ab, "_HAS_HNSW", False)
    monkeypatch.setattr(ab, "_HAS_FAISS", False)
    assert _resolve_backend(10_000, 20) == "numpy"


def test_env_min_override(monkeypatch):
    monkeypatch.delenv("GOLDENMATCH_ANN_BACKEND", raising=False)
    monkeypatch.setattr(ab, "_HAS_HNSW", True)
    monkeypatch.setattr(ab, "_HAS_FAISS", False)
    monkeypatch.setenv("GOLDENMATCH_ANN_HNSW_MIN", "100")
    assert _resolve_backend(150, 20) == "hnsw"
    assert _resolve_backend(50, 20) == "numpy"


# ── integration (requires the native wheel) ─────────────────────────────────

pytestmark_native = pytest.mark.skipif(
    not ab._HAS_HNSW, reason="goldenmatch-hnsw wheel not installed"
)


def _corpus(n: int, dim: int, seed: int = 3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((n, dim)).astype(np.float32)
    x /= np.linalg.norm(x, axis=1, keepdims=True)
    return x


@pytestmark_native
def test_hnsw_backend_selected_and_high_recall(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ANN_BACKEND", "hnsw")
    x = _corpus(5000, 32)
    b = ANNBlocker(top_k=20)
    b.build_index(x)
    assert b._backend == "hnsw"
    assert b.index_size == 5000

    # exact reference via the numpy path
    monkeypatch.setenv("GOLDENMATCH_ANN_BACKEND", "numpy")
    ref = ANNBlocker(top_k=20)
    ref.build_index(x)

    hits = tot = 0
    for qi in range(0, 5000, 137):
        got = {i for i, _ in b.query_one(x[qi])}
        want = {i for i, _ in ref.query_one(x[qi])}
        hits += len(got & want)
        tot += len(want)
    assert hits / tot >= 0.95


@pytestmark_native
def test_hnsw_scores_are_inner_product(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ANN_BACKEND", "hnsw")
    x = _corpus(5000, 24)
    b = ANNBlocker(top_k=10)
    b.build_index(x)
    res = b.query_one(x[0])
    # self is the top hit, score == 1 on unit vectors
    assert res[0][0] == 0
    assert abs(res[0][1] - 1.0) < 1e-4
    # scores descending, each equals the true inner product
    scores = [s for _, s in res]
    assert scores == sorted(scores, reverse=True)
    for i, s in res:
        assert abs(s - float(x[i] @ x[0])) < 1e-3


@pytestmark_native
def test_hnsw_incremental_add(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ANN_BACKEND", "hnsw")
    x = _corpus(5000, 16)
    b = ANNBlocker(top_k=10)
    b.build_index(x)
    n0 = b.index_size
    new = _corpus(1, 16, seed=99)[0]
    pos = b.add_to_index(new)
    assert pos == n0
    assert b.index_size == n0 + 1
    # the freshly added vector is findable as its own nearest neighbor
    hits = {i for i, _ in b.query_one(new)}
    assert pos in hits


@pytestmark_native
def test_hnsw_query_with_scores_canonical(monkeypatch):
    monkeypatch.setenv("GOLDENMATCH_ANN_BACKEND", "hnsw")
    x = _corpus(5000, 16)
    b = ANNBlocker(top_k=10)
    b.build_index(x)
    pairs = b.query_with_scores(x[:200])
    assert pairs
    assert all(a < c for a, c, _ in pairs)  # (min, max) canonicalization
    assert all(-1.0001 <= s <= 1.0001 for *_, s in pairs)
