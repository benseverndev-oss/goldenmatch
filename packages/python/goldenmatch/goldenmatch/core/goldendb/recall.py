"""Stage A recall: coarse vector -> top-k candidate shortlist (the spec's ANN stage).

**WORK IN PROGRESS** -- part of the experimental GoldenDB matrix-native backend.

The dense path in :mod:`goldenmatch.core.goldendb.scorer` materialises a full
``[N, N]`` similarity matrix per block -- fine for small blocks, quadratic in memory
for large ones. This module is the spec's Stage A: encode each record to a single
coarse vector (the weighted sum of its per-field char-ngram embeddings,
renormalised), then take top-k nearest neighbours by cosine to produce a candidate
shortlist. Only that shortlist is scored (Stage B), so the N^2 cost is bounded by
the recall step.

Recall here is brute-force top-k via a tiled JAX ``matmul`` -- per the design doc,
GPU brute force is fine to ~1e5-1e6 vectors; a true GPU-ANN index (FAISS / DiskANN)
is the next step beyond that. With ``k >= N-1`` the shortlist is every pair, so the
recall path is exactly equivalent to the dense path (used for parity testing).
"""

from __future__ import annotations

import numpy as np

# Tile size for the brute-force neighbour scan -- bounds the transient
# ``[tile, N]`` similarity block so a large block doesn't allocate ``N*N`` at once.
DEFAULT_TILE = 2048


def coarse_encode(field_embeddings: list[np.ndarray], weights: np.ndarray) -> np.ndarray:
    """Combine per-field embedding matrices into one coarse ``[N, dim]`` matrix.

    Args:
        field_embeddings: list of ``[N, dim]`` L2-normalised per-field matrices
            (fuzzy fields only -- exact fields don't contribute to coarse recall).
        weights: ``[K]`` field weights aligned with ``field_embeddings``.

    Returns:
        ``[N, dim]`` L2-normalised coarse matrix (weighted sum, renormalised).
    """
    if not field_embeddings:
        raise ValueError("coarse_encode needs at least one field embedding")
    dim = field_embeddings[0].shape[1]
    n = field_embeddings[0].shape[0]
    acc = np.zeros((n, dim), dtype=np.float32)
    for emb, w in zip(field_embeddings, weights):
        acc += float(w) * emb
    norms = np.linalg.norm(acc, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return acc / norms


def topk_candidates(
    coarse: np.ndarray,
    k: int,
    min_sim: float = 0.0,
    tile: int = DEFAULT_TILE,
) -> list[tuple[int, int]]:
    """Top-k nearest-neighbour candidate index pairs by cosine (brute force, JAX).

    Returns canonical ``(i, j)`` index pairs with ``i < j``, deduplicated. Each
    record contributes its ``k`` nearest neighbours (excluding itself) above
    ``min_sim``; the union is symmetric-deduplicated.
    """
    from goldenmatch.core.goldendb import require_jax

    jax, jnp = require_jax()
    n = coarse.shape[0]
    if n < 2:
        return []
    kk = min(k + 1, n)  # +1: self is always the top hit, dropped below
    C = jnp.asarray(coarse)

    pairs: set[tuple[int, int]] = set()
    for start in range(0, n, tile):
        block = C[start : start + tile]
        sims = block @ C.T  # [t, n]
        vals, idx = jax.lax.top_k(sims, kk)
        idx = np.asarray(idx)
        vals = np.asarray(vals)
        for r in range(idx.shape[0]):
            i = start + r
            for c in range(kk):
                j = int(idx[r, c])
                if j == i or vals[r, c] < min_sim:
                    continue
                pairs.add((i, j) if i < j else (j, i))
    return sorted(pairs)
