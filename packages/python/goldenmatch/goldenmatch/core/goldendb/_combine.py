"""Score + audit: per-field similarities -> match score (the "up" translator).

**WORK IN PROGRESS** -- part of the experimental GoldenDB matrix-native backend.

Two faces of the same GA2M idea:

* :func:`combine_matrices` -- the default, interpretable, **untrained** combine
  used by the block scorer. It is the GA2M special case with identity shape
  functions and no interaction terms: a weighted average of the per-field
  similarities. Its per-field attribution is **exact** (the contributions sum to
  the score), and with non-negative weights it is monotone (more agreement never
  lowers the score). This deliberately mirrors goldenmatch's weighted-matchkey
  numerator/denominator semantics so the matrix path is comparable to the
  existing scorer.

* :class:`GA2MCombiner` -- the trainable probabilistic model (JAX). Weights are
  passed through ``softplus`` so they stay non-negative (monotonicity preserved),
  and the whole thing is differentiable, so field weights / gain / threshold are
  learned by gradient descent (:meth:`GA2MCombiner.train_step`) -- the
  "gradient-based probabilistic ER" the spec calls for. Trained shape functions
  and pairwise interaction terms are future work; the structure is linear today.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


def combine_matrices(
    sim_stack: np.ndarray,
    weights: np.ndarray,
    valid_stack: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """Weighted-average combine of per-field similarity matrices, with attribution.

    Args:
        sim_stack: ``[K, N, N]`` per-field similarity matrices (K = n_fields).
        weights: ``[K]`` non-negative field weights.
        valid_stack: optional ``[K, N, N]`` validity mask (1.0 where both values
            are non-null, else 0.0). Defaults to all-valid.

    Returns:
        ``(score, attribution)`` where ``score`` is ``[N, N]`` and
        ``attribution`` is ``[K, N, N]`` with the invariant
        ``score == attribution.sum(axis=0)`` (exact additive decomposition --
        this IS the audit).
    """
    sim_stack = np.asarray(sim_stack, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    if sim_stack.ndim != 3:
        raise ValueError("sim_stack must be [K, N, N]")
    k = sim_stack.shape[0]
    if weights.shape != (k,):
        raise ValueError("weights must be shape [K] matching sim_stack")
    if valid_stack is None:
        valid_stack = np.ones_like(sim_stack)
    else:
        valid_stack = np.asarray(valid_stack, dtype=np.float64)

    w = weights[:, None, None]
    weighted = w * sim_stack * valid_stack          # [K, N, N]
    den = np.sum(w * valid_stack, axis=0)           # [N, N]
    safe_den = np.where(den > 0.0, den, 1.0)
    attribution = weighted / safe_den               # [K, N, N]
    attribution = np.where(den > 0.0, attribution, 0.0)
    score = attribution.sum(axis=0)                 # [N, N]
    return score.astype(np.float32), attribution.astype(np.float32)


@dataclass
class GA2MParams:
    """Trainable parameters for :class:`GA2MCombiner` (plain numpy for storage)."""

    w: np.ndarray      # [K] raw field weights (pre-softplus)
    tau: float         # decision offset
    gain: float        # logit sharpness


class GA2MCombiner:
    """Differentiable, monotone, probabilistic GA2M combine (JAX).

    ``predict(sims)`` maps per-pair per-field similarities ``[P, K]`` to a match
    probability ``[P]``. Weights are ``softplus``-constrained to be non-negative,
    so the score is monotone non-decreasing in every similarity. ``train_step``
    runs one gradient-descent step on binary cross-entropy against labels.
    """

    def __init__(self, n_fields: int, seed: int = 0):
        self.n_fields = n_fields
        rng = np.random.default_rng(seed)
        # Init raw weights so softplus(w) ~ 1.0 (softplus(0.54)=1.0); small jitter.
        self.params = GA2MParams(
            w=np.full(n_fields, 0.541324854, dtype=np.float64)
            + rng.normal(0, 1e-3, n_fields),
            tau=0.5,
            gain=6.0,
        )

    # -- functional core (static so jax can trace it cleanly) --------------
    @staticmethod
    def _predict(params: dict, sims):
        from goldenmatch.core.goldendb import require_jax

        jax, jnp = require_jax()
        w = jax.nn.softplus(params["w"])                  # [K] >= 0  (monotone)
        wsum = jnp.sum(w) + 1e-9
        z = jnp.sum(sims * w[None, :], axis=1) / wsum     # weighted avg in [0,1]
        return jax.nn.sigmoid(params["gain"] * (z - params["tau"]))

    @staticmethod
    def _loss(params: dict, sims, labels):
        from goldenmatch.core.goldendb import require_jax

        _jax, jnp = require_jax()
        p = GA2MCombiner._predict(params, sims)
        eps = 1e-7
        p = jnp.clip(p, eps, 1.0 - eps)
        return -jnp.mean(labels * jnp.log(p) + (1.0 - labels) * jnp.log(1.0 - p))

    def _jax_params(self) -> dict:
        from goldenmatch.core.goldendb import require_jax

        _jax, jnp = require_jax()
        return {
            "w": jnp.asarray(self.params.w),
            "tau": jnp.asarray(self.params.tau),
            "gain": jnp.asarray(self.params.gain),
        }

    # -- public API --------------------------------------------------------
    def predict(self, sims: np.ndarray) -> np.ndarray:
        """``[P, K] -> [P]`` match probabilities."""
        return np.asarray(self._predict(self._jax_params(), np.asarray(sims, np.float64)))

    def loss(self, sims: np.ndarray, labels: np.ndarray) -> float:
        return float(self._loss(self._jax_params(), np.asarray(sims, np.float64),
                                np.asarray(labels, np.float64)))

    def attribution(self, sims: np.ndarray) -> np.ndarray:
        """Exact additive per-field contribution to the (pre-sigmoid) weighted
        average. ``contributions.sum(axis=1)`` == the weighted-average term that
        feeds the link function -- the audit decomposition.
        """
        from goldenmatch.core.goldendb import require_jax

        jax, jnp = require_jax()
        w = np.asarray(jax.nn.softplus(jnp.asarray(self.params.w)))
        wsum = w.sum() + 1e-9
        sims = np.asarray(sims, np.float64)
        return (sims * w[None, :]) / wsum

    def train_step(self, sims: np.ndarray, labels: np.ndarray, lr: float = 0.1) -> float:
        """One gradient-descent step on BCE. Returns the loss BEFORE the step.

        This is the real ``jax.grad`` training loop -- proof the scorer is
        differentiable end to end.
        """
        from goldenmatch.core.goldendb import require_jax

        jax, _jnp = require_jax()
        sims_j = jax.numpy.asarray(np.asarray(sims, np.float64))
        labels_j = jax.numpy.asarray(np.asarray(labels, np.float64))
        params = self._jax_params()
        loss_val, grads = jax.value_and_grad(self._loss)(params, sims_j, labels_j)
        new = {k: params[k] - lr * grads[k] for k in params}
        self.params = GA2MParams(
            w=np.asarray(new["w"]),
            tau=float(new["tau"]),
            gain=float(new["gain"]),
        )
        return float(loss_val)
