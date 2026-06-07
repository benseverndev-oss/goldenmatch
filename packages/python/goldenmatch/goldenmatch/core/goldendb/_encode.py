"""Block encoding: schema field -> matrix (the "down" translator).

**WORK IN PROGRESS** -- part of the experimental GoldenDB matrix-native backend.

Each matchkey field is encoded into a fixed-width vector via the char-ngram
hashing trick, L2-normalised so that a plain dot product (matrix multiply) is the
cosine similarity. This is the spec's per-field block encoder for high-cardinality
string fields ("char-ngram embed -> cosine (matmul)"). The cosine is computed with
JAX so it runs on a GPU when one is present and on CPU otherwise.

The hash is :func:`zlib.crc32` (stable across processes), NOT the builtin
``hash()`` -- the latter is salted per-process via ``PYTHONHASHSEED`` and would
make encodings non-reproducible run to run.
"""

from __future__ import annotations

import zlib
from collections.abc import Sequence

import numpy as np

# Default hashed-embedding width. Wide enough that 2/3-gram collisions are rare
# for person-name / address fields; small enough that an NxN matmul on a single
# block stays cheap. Tunable per call.
DEFAULT_DIM = 256
DEFAULT_NGRAMS = (2, 3)


def _char_ngrams(s: str, ns: Sequence[int]) -> list[str]:
    """Character n-grams of ``s`` with boundary padding (so prefixes/suffixes
    are distinguished). ``"abc"`` -> for n=2: ``" a","ab","bc","c "``."""
    padded = f" {s} "
    grams: list[str] = []
    for n in ns:
        if n <= 0:
            continue
        for i in range(len(padded) - n + 1):
            grams.append(padded[i : i + n])
    return grams


def char_ngram_hashed(
    values: Sequence,
    dim: int = DEFAULT_DIM,
    ns: Sequence[int] = DEFAULT_NGRAMS,
) -> np.ndarray:
    """Encode a column of values into an ``[N, dim]`` L2-normalised float32 matrix.

    None / empty values become all-zero rows (their cosine with anything is 0).
    Use :func:`numpy`-built matrix here; the similarity matmul (which is the GPU
    hot path) lives in :func:`cosine_matrix`.
    """
    n = len(values)
    mat = np.zeros((n, dim), dtype=np.float32)
    for i, v in enumerate(values):
        if v is None:
            continue
        s = str(v).lower().strip()
        if not s:
            continue
        for g in _char_ngrams(s, ns):
            h = zlib.crc32(g.encode("utf-8")) % dim
            mat[i, h] += 1.0
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return mat / norms


def cosine_matrix(mat: np.ndarray) -> np.ndarray:
    """``[N, dim] -> [N, N]`` cosine similarity via a JAX matmul.

    Rows are assumed L2-normalised (as produced by :func:`char_ngram_hashed`), so
    ``mat @ mat.T`` is the cosine. Runs on GPU when JAX sees one, CPU otherwise.
    Returns a float32 numpy array clamped to ``[0, 1]`` (count vectors are
    non-negative, so cosine is already in range; the clamp guards fp drift).
    """
    from goldenmatch.core.goldendb import require_jax

    _jax, jnp = require_jax()
    m = jnp.asarray(mat)
    sim = jnp.matmul(m, m.T)
    sim = jnp.clip(sim, 0.0, 1.0)
    return np.asarray(sim, dtype=np.float32)
