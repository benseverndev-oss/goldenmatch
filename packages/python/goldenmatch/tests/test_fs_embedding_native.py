"""Embedding FS scorer on the native Rust kernel (increment 7a).

`embedding` fields used to force the WHOLE probabilistic matchkey onto the numpy
vectorized path (no native kernel id). The native kernel now scores an embedding
field (id 7) as the cosine (dot) of the two rows' host-precomputed L2-normalized
vectors, so a mixed string+embedding matchkey runs natively. This pins native ==
numpy on the embedding path, using a deterministic fake embedder (torch-free).
"""

from __future__ import annotations

import hashlib

import numpy as np
import polars as pl
import pytest
from goldenmatch.config.schemas import MatchkeyConfig, MatchkeyField


class _FakeEmbedder:
    """Deterministic, L2-normalized, hash-based embeddings. Identical strings map
    to identical vectors (cosine 1.0); different strings ~orthogonal. Mirrors the
    real embedder's null coercion (None/empty -> "")."""

    dim = 8

    def embed_column(self, values, cache_key=None):
        clean = [str(v) if v is not None and str(v).strip() else "" for v in values]
        out = np.zeros((len(clean), self.dim), dtype=np.float64)
        for i, s in enumerate(clean):
            h = hashlib.sha256(s.encode("utf-8")).digest()
            v = np.frombuffer(h[: self.dim], dtype=np.uint8).astype(np.float64)
            v = v - v.mean()
            norm = np.linalg.norm(v)
            out[i] = v / norm if norm > 0 else v
        return out

    def cosine_similarity_matrix(self, embeddings):
        return embeddings @ embeddings.T


def _new_kernel_available() -> bool:
    try:
        from goldenmatch.core._native_loader import native_module

        mod = native_module()
        return bool(mod) and bool(getattr(mod, "FS_SUPPORTS_EMBEDDING", False))
    except Exception:
        return False


@pytest.fixture
def fake_embedder(monkeypatch):
    emb = _FakeEmbedder()
    monkeypatch.setattr(
        "goldenmatch.core.embedder.get_embedder", lambda *a, **k: emb
    )
    return emb


def test_embedding_eligible_only_with_capability(fake_embedder):
    from goldenmatch.core import probabilistic as P

    fE = MatchkeyField(field="name", scorer="embedding", levels=2, partial_threshold=0.9)
    mk = MatchkeyConfig(name="m", type="probabilistic", fields=[fE], link_threshold=0.0)
    # record_embedding stays on numpy (declined).
    fR = MatchkeyField(field="name", scorer="record_embedding", levels=2,
                       partial_threshold=0.9, columns=["name"])
    mk_rec = MatchkeyConfig(name="r", type="probabilistic", fields=[fR],
                            link_threshold=0.0)
    assert P._NATIVE_FS_SCORER_IDS["embedding"] == 7
    if _new_kernel_available():
        # embedding admitted; record_embedding declined by design.
        import os

        os.environ["GOLDENMATCH_FS_NATIVE"] = "1"
        try:
            assert P._fs_native_eligible(mk) is True
            assert P._fs_native_eligible(mk_rec) is False
        finally:
            os.environ.pop("GOLDENMATCH_FS_NATIVE", None)


@pytest.mark.skipif(
    not _new_kernel_available(),
    reason="native kernel without FS_SUPPORTS_EMBEDDING",
)
def test_native_numpy_parity_embedding(monkeypatch, fake_embedder):
    """A probabilistic matchkey with an `embedding` field + an `exact` field
    scores IDENTICALLY on the native kernel and the numpy path, on clean data
    (identical vs very-different values, so no pair sits at a level boundary)."""
    from goldenmatch.core import probabilistic as P

    name = ["alpha", "alpha", "bravo", "bravo", "charlie", "zzzzz"]
    zipc = ["10001", "10001", "20002", "20002", "30003", "99999"]
    fN = MatchkeyField(field="name", scorer="embedding", levels=2, partial_threshold=0.9)
    fZ = MatchkeyField(field="zip", scorer="exact", levels=2, partial_threshold=0.9)
    mk = MatchkeyConfig(name="m", type="probabilistic", fields=[fN, fZ],
                        link_threshold=0.0)
    df = pl.DataFrame({
        "__row_id__": list(range(len(name))),
        "name": name, "zip": zipc,
    })
    em = P.train_em(df, mk, n_sample_pairs=2000)

    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "0")
    assert P._fs_native_eligible(mk) is False
    numpy_pairs = P.score_probabilistic_vectorized(df, mk, em)
    monkeypatch.setenv("GOLDENMATCH_FS_NATIVE", "1")
    assert P._fs_native_eligible(mk) is True
    native_pairs = P.score_probabilistic_native(df, mk, em)

    n0 = {(a, b): s for a, b, s in numpy_pairs}
    n1 = {(a, b): s for a, b, s in native_pairs}
    assert n0.keys() == n1.keys() and len(n0) > 0
    for pair, s in n0.items():
        assert n1[pair] == pytest.approx(s, abs=1e-6)
