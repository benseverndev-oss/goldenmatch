"""GoldenDB -- matrix-native entity resolution backend (JAX). **WORK IN PROGRESS.**

.. warning::

   This package is EXPERIMENTAL / WORK IN PROGRESS and is **not** production
   ready. It implements the spike described in
   ``docs/superpowers/specs/2026-06-07-goldendb-matrix-native-entity-resolution-design.md``:
   entity resolution as a fuzzy self-join expressed as a matrix multiply
   (``M @ M^T`` + threshold), with a GA2M-style additive combine that keeps the
   per-field attribution exact.

   What it does today (CPU JAX validated):

   * char-ngram hashed encoding of each matchkey field -> L2-normalised vectors
   * per-field cosine similarity via a JAX ``matmul`` (the GPU path when a GPU
     is present; JAX transparently runs on CPU otherwise)
   * a GA2M combine that defaults to the interpretable weighted-average (the
     untrained, identity-shape-function special case) with **exact** additive
     per-field attribution and a monotonicity guarantee
   * a real ``jax.grad`` training step for the probabilistic combine

   What it deliberately does NOT do yet (see the spec's "Future direction"):

   * cross-block ANN recall (today it scores within the existing blocks, so
     recall is whatever the blocker produces -- Stage A is not wired)
   * trained shape functions / pairwise interaction terms by default
     (structure is present; the default is linear/weighted-average)
   * negative-evidence penalties (ignored with a warning if configured)
   * GPU wall-clock validation (no GPU in the dev/CI environment)

   Scores from this backend are produced by char-ngram cosine + an UNTRAINED
   combine and are NOT calibrated against the production scorers. Treat any
   output as experimental.

The backend conforms to the existing block-scorer contract
(``goldenmatch.core.scorer.score_blocks_parallel``) and is selected with
``config.backend = "gpu"``.
"""

from __future__ import annotations

__status__ = "work-in-progress"
EXPERIMENTAL = True

# A short banner other surfaces (CLI, telemetry) can surface verbatim.
WIP_BANNER = (
    "GoldenDB matrix-native (backend='gpu') is EXPERIMENTAL / WORK IN PROGRESS -- "
    "scores use char-ngram cosine + an untrained GA2M combine and are not "
    "production-validated."
)


def jax_available() -> bool:
    """Return True if JAX can be imported (CPU or GPU)."""
    try:
        import jax  # noqa: F401

        return True
    except Exception:
        return False


def require_jax():
    """Import and return ``jax`` / ``jax.numpy`` or raise a clear install error."""
    try:
        import jax
        import jax.numpy as jnp

        return jax, jnp
    except Exception as exc:  # pragma: no cover - exercised only without jax
        raise ImportError(
            "The GoldenDB matrix-native backend (backend='gpu') requires JAX. "
            "Install it with: pip install 'goldenmatch[goldendb]'  (or: pip install 'jax[cpu]'). "
            "For GPU acceleration follow the JAX CUDA install instructions."
        ) from exc


from goldenmatch.core.goldendb._combine import (  # noqa: E402
    GA2MCombiner,
    combine_matrices,
)
from goldenmatch.core.goldendb._encode import (  # noqa: E402
    char_ngram_hashed,
    cosine_matrix,
)
from goldenmatch.core.goldendb.scorer import (  # noqa: E402
    find_matches_gpu,
    score_blocks_gpu,
)

__all__ = [
    "EXPERIMENTAL",
    "WIP_BANNER",
    "__status__",
    "jax_available",
    "require_jax",
    "char_ngram_hashed",
    "cosine_matrix",
    "combine_matrices",
    "GA2MCombiner",
    "find_matches_gpu",
    "score_blocks_gpu",
]
