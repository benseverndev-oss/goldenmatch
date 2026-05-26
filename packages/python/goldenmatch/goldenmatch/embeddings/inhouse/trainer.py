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
    # ``cosine`` (default) optimizes the inference metric directly: pull match
    # cosine toward 1, push non-match cosine below ``neg_margin``. ``euclidean``
    # is the legacy margin loss on un-normalized projection distance — it can
    # *degrade* below the lexical baseline on semantic data (matches with low
    # surface overlap), where the train/inference mismatch hurts. See #507.
    loss: str = "cosine"
    margin: float = 1.0  # euclidean: non-match target distance
    neg_margin: float = 0.3  # cosine: non-match cosine ceiling
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

    if cfg.loss not in ("cosine", "euclidean"):
        raise ValueError(f"loss must be 'cosine' or 'euclidean', got {cfg.loss!r}")

    model = GoldenEmbedModel(
        EmbedModelConfig(dim=cfg.dim, featurizer=cfg.featurizer), seed=cfg.seed
    )
    fa = model.featurizer.transform([a for a, _b, _y in pairs])
    fb = model.featurizer.transform([b for _a, b, _y in pairs])
    y = np.array([int(label) for _a, _b, label in pairs], dtype=np.float32)
    n = fa.shape[0]

    sep_before = _separation(model, fa, fb, y)
    if cfg.loss == "cosine":
        W, final_loss = _train_cosine(model.weights, fa, fb, y, cfg)
    else:
        W, final_loss = _train_euclidean(model.weights, fa - fb, y, cfg)

    model.weights = np.ascontiguousarray(W, dtype=np.float32)
    sep_after = _separation(model, fa, fb, y)
    return model, TrainReport(
        epochs=cfg.epochs,
        final_loss=final_loss,
        separation_before=sep_before,
        separation_after=sep_after,
        n_pairs=n,
    )


def _train_euclidean(
    W: np.ndarray, g: np.ndarray, y: np.ndarray, cfg: TrainConfig
) -> tuple[np.ndarray, float]:
    """Margin loss on un-normalized projection distance ``||(fa-fb) @ W||``."""
    n = g.shape[0]
    final_loss = 0.0
    is_match = y == 1
    for _ in range(cfg.epochs):
        p = g @ W  # (n, D) projected difference
        d = np.linalg.norm(p, axis=1)  # (n,)
        is_viol = (y == 0) & (d < cfg.margin)  # only non-matches inside the margin

        loss = float(np.sum(d[is_match] ** 2) + np.sum((cfg.margin - d[is_viol]) ** 2))
        final_loss = loss / n + cfg.l2 * float(np.sum(W * W))

        grad = np.zeros_like(W)
        if is_match.any():
            grad += 2.0 * g[is_match].T @ p[is_match]
        if is_viol.any():
            dv = d[is_viol]
            coeff = (-2.0 * (cfg.margin - dv) / dv)[:, None]  # d>0 by <margin & nonzero
            grad += g[is_viol].T @ (coeff * p[is_viol])
        grad = grad / n + 2.0 * cfg.l2 * W
        W = W - cfg.lr * grad
    return W, final_loss


def _train_cosine(
    W: np.ndarray, fa: np.ndarray, fb: np.ndarray, y: np.ndarray, cfg: TrainConfig
) -> tuple[np.ndarray, float]:
    """Contrastive loss on the cosine of the (separately L2-normalized) projected
    embeddings — the exact quantity used at inference. Matches minimize ``1-cos``;
    non-matches with ``cos > neg_margin`` are pushed down. Backprop runs through
    the per-side normalization."""
    n = fa.shape[0]
    is_match = y == 1
    final_loss = 0.0
    for _ in range(cfg.epochs):
        za = fa @ W
        zb = fb @ W
        na = np.linalg.norm(za, axis=1, keepdims=True)
        nb = np.linalg.norm(zb, axis=1, keepdims=True)
        na[na == 0.0] = 1.0
        nb[nb == 0.0] = 1.0
        ea = za / na
        eb = zb / nb
        cos = np.sum(ea * eb, axis=1)  # (n,)

        is_viol = (y == 0) & (cos > cfg.neg_margin)
        loss = float(np.sum(1.0 - cos[is_match]) + np.sum(cos[is_viol] - cfg.neg_margin))
        final_loss = loss / n + cfg.l2 * float(np.sum(W * W))

        # dL/dcos: +1 for a match (minimize 1-cos), -1 for a violating non-match.
        dcos = np.where(is_match, 1.0, np.where(is_viol, -1.0, 0.0)).astype(np.float32)
        # d cos / d z = (e_other - cos * e_self) / ||z||  (cosine-sim gradient).
        dza = (dcos[:, None] * (eb - cos[:, None] * ea)) / na
        dzb = (dcos[:, None] * (ea - cos[:, None] * eb)) / nb
        # Maximizing cos for matches -> ascend, so subtract the (negated) grad below.
        grad = -(fa.T @ dza + fb.T @ dzb) / n + 2.0 * cfg.l2 * W
        W = W - cfg.lr * grad
    return W, final_loss
