"""Stage A recall: coarse vector -> top-k candidate shortlist (the spec's ANN stage).

**WORK IN PROGRESS** -- part of the experimental GoldenDB matrix-native backend.

The dense path in :mod:`goldenmatch.core.goldendb.scorer` materialises a full
``[N, N]`` similarity matrix per block -- fine for small blocks, quadratic in memory
for large ones. This module is the spec's Stage A: encode each record to a single
coarse vector (the weighted sum of its per-field char-ngram embeddings,
renormalised), then take top-k nearest neighbours by cosine to produce a candidate
shortlist. Only that shortlist is scored (Stage B), so the N^2 cost is bounded by
the recall step.

Two recall backends (auto-selected, override via
``GOLDENMATCH_GOLDENDB_RECALL_BACKEND`` = ``faiss`` | ``bruteforce`` | ``auto``):

* **faiss** -- a FAISS index (the spec's GPU-ANN step). ``IndexFlatIP`` is exact
  (cosine on L2-normalised vectors); for large N an ``IndexIVFFlat`` gives
  approximate sub-quadratic recall. FAISS runs the search in optimised C++/BLAS and
  on GPU when a GPU build is installed.
* **bruteforce** -- a tiled JAX ``matmul`` top-k (the dependency-free fallback). Good
  to ~1e5-1e6 vectors per the design doc.

With ``k >= N-1`` (and exact ``IndexFlatIP``) the shortlist is every pair, so the
recall path is exactly equivalent to the dense path (used for parity testing).
"""

from __future__ import annotations

import os

import numpy as np

# Tile size for the brute-force neighbour scan -- bounds the transient
# ``[tile, N]`` similarity block so a large block doesn't allocate ``N*N`` at once.
DEFAULT_TILE = 2048

# Above this many vectors the faiss path defaults to an approximate IVF index
# instead of exact flat search.
IVF_MIN_ROWS = 50_000


def faiss_available() -> bool:
    """Return True if FAISS can be imported."""
    try:
        import faiss  # noqa: F401

        return True
    except Exception:
        return False


def _resolve_backend(backend: str | None) -> str:
    """Resolve the recall backend: explicit arg > env var > auto (faiss if present)."""
    choice = (backend or os.environ.get("GOLDENMATCH_GOLDENDB_RECALL_BACKEND", "auto")).lower()
    if choice == "auto":
        return "faiss" if faiss_available() else "bruteforce"
    return choice


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


def _pairs_from_neighbours(idx: np.ndarray, sims: np.ndarray, min_sim: float) -> list[tuple[int, int]]:
    """Turn an ``[N, kk]`` neighbour-index/sim result into canonical ``(i<j)`` pairs."""
    n, kk = idx.shape
    pairs: set[tuple[int, int]] = set()
    for i in range(n):
        for c in range(kk):
            j = int(idx[i, c])
            if j < 0 or j == i or sims[i, c] < min_sim:
                continue
            pairs.add((i, j) if i < j else (j, i))
    return sorted(pairs)


def _topk_bruteforce(
    coarse: np.ndarray, k: int, min_sim: float, tile: int = DEFAULT_TILE,
) -> list[tuple[int, int]]:
    from goldenmatch.core.goldendb import require_jax

    jax, jnp = require_jax()
    n = coarse.shape[0]
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


def _topk_faiss(
    coarse: np.ndarray,
    k: int,
    min_sim: float,
    use_ivf: bool | None = None,
    nlist: int | None = None,
    nprobe: int | None = None,
) -> list[tuple[int, int]]:
    import faiss

    n, d = coarse.shape
    x = np.ascontiguousarray(coarse.astype(np.float32))
    kk = min(k + 1, n)  # +1: self hit dropped below
    if use_ivf is None:
        use_ivf = n >= IVF_MIN_ROWS

    if use_ivf and n > 1:
        nlist = nlist or max(1, min(n // 39, int(4 * np.sqrt(n))))
        quantizer = faiss.IndexFlatIP(d)
        index = faiss.IndexIVFFlat(quantizer, d, nlist, faiss.METRIC_INNER_PRODUCT)
        index.train(x)
        index.add(x)
        index.nprobe = nprobe or min(nlist, 16)
    else:
        index = faiss.IndexFlatIP(d)  # exact inner product = cosine (normalised)
        index.add(x)

    sims, idx = index.search(x, kk)
    return _pairs_from_neighbours(idx, sims, min_sim)


def topk_candidates(
    coarse: np.ndarray,
    k: int,
    min_sim: float = 0.0,
    tile: int = DEFAULT_TILE,
    backend: str | None = None,
    use_ivf: bool | None = None,
) -> list[tuple[int, int]]:
    """Top-k nearest-neighbour candidate index pairs by cosine.

    Returns canonical ``(i, j)`` index pairs with ``i < j``, deduplicated. ``backend``
    selects ``"faiss"`` / ``"bruteforce"`` / ``"auto"`` (default: faiss if installed,
    else brute force; overridable via ``GOLDENMATCH_GOLDENDB_RECALL_BACKEND``).
    """
    n = coarse.shape[0]
    if n < 2:
        return []
    resolved = _resolve_backend(backend)
    if resolved == "faiss":
        if not faiss_available():
            raise ImportError(
                "recall backend 'faiss' requested but faiss is not installed "
                "(pip install 'goldenmatch[embeddings]' or faiss-cpu)."
            )
        return _topk_faiss(coarse, k, min_sim, use_ivf=use_ivf)
    return _topk_bruteforce(coarse, k, min_sim, tile=tile)
