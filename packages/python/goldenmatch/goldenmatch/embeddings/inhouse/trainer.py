"""Supervised training for the in-house embedder — numpy only, no torch.

Contrastive metric learning on labeled pairs: learn the projection ``W`` so the
squared Euclidean distance between projected feature vectors is small for
matches and at least ``margin`` for non-matches. Since embeddings are
L2-normalized at inference, Euclidean separation in projection space maps to
cosine separation on the unit sphere.

Fully vectorized and seeded, so a given (pairs, config) trains to the same
weights every time — the roadmap's "deterministic results" requirement.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from goldenmatch.embeddings.inhouse.featurizer import FeaturizerConfig
from goldenmatch.embeddings.inhouse.model import EmbedModelConfig, GoldenEmbedModel

LabeledPair = tuple[str, str, int]


@dataclass
class TrainConfig:
    dim: int = 64
    epochs: int = 100
    lr: float = 0.5
    margin: float = 1.0
    l2: float = 1e-4
    seed: int = 0
    featurizer: FeaturizerConfig = field(default_factory=FeaturizerConfig)


@dataclass
class TrainReport:
    """Per-fit diagnostics. ``separation`` = mean match cosine - mean non-match
    cosine on the training pairs (higher is better)."""

    epochs: int
    final_loss: float
    separation_before: float
    separation_after: float
    n_pairs: int


def _cosine(model: GoldenEmbedModel, fa: np.ndarray, fb: np.ndarray) -> np.ndarray:
    ea = model.project(fa)
    eb = model.project(fb)
    return np.sum(ea * eb, axis=1)


def _separation(
    model: GoldenEmbedModel, fa: np.ndarray, fb: np.ndarray, y: np.ndarray
) -> float:
    cos = _cosine(model, fa, fb)
    pos = cos[y == 1]
    neg = cos[y == 0]
    pos_m = float(pos.mean()) if pos.size else 0.0
    neg_m = float(neg.mean()) if neg.size else 0.0
    return pos_m - neg_m


def train_embedder(
    pairs: list[LabeledPair], config: TrainConfig | None = None
) -> tuple[GoldenEmbedModel, TrainReport]:
    """Train a :class:`GoldenEmbedModel` from labeled ``(text_a, text_b, label)``
    pairs (``label`` 1 = match, 0 = non-match)."""
    cfg = config or TrainConfig()
    if not pairs:
        raise ValueError("train_embedder requires at least one labeled pair")

    model = GoldenEmbedModel(
        EmbedModelConfig(dim=cfg.dim, featurizer=cfg.featurizer), seed=cfg.seed
    )
    fa = model.featurizer.transform([a for a, _b, _y in pairs])
    fb = model.featurizer.transform([b for _a, b, _y in pairs])
    y = np.array([int(label) for _a, _b, label in pairs], dtype=np.float32)
    g = fa - fb  # (n, F) feature-space difference
    n = g.shape[0]

    sep_before = _separation(model, fa, fb, y)
    W = model.weights
    final_loss = 0.0
    for _ in range(cfg.epochs):
        p = g @ W  # (n, D) projected difference
        d = np.linalg.norm(p, axis=1)  # (n,)
        is_match = y == 1
        is_viol = (y == 0) & (d < cfg.margin)  # only non-matches inside the margin

        # loss (for reporting): matches pull d^2 -> 0, non-matches push d -> margin.
        loss = float(np.sum(d[is_match] ** 2) + np.sum((cfg.margin - d[is_viol]) ** 2))
        final_loss = loss / n + cfg.l2 * float(np.sum(W * W))

        grad = np.zeros_like(W)
        if is_match.any():
            grad += 2.0 * g[is_match].T @ p[is_match]
        if is_viol.any():
            dv = d[is_viol]
            coeff = (-2.0 * (cfg.margin - dv) / dv)[:, None]  # d>0 guaranteed by <margin & nonzero
            grad += g[is_viol].T @ (coeff * p[is_viol])
        grad = grad / n + 2.0 * cfg.l2 * W
        W = W - cfg.lr * grad

    model.weights = np.ascontiguousarray(W, dtype=np.float32)
    sep_after = _separation(model, fa, fb, y)
    return model, TrainReport(
        epochs=cfg.epochs,
        final_loss=final_loss,
        separation_before=sep_before,
        separation_after=sep_after,
        n_pairs=n,
    )
