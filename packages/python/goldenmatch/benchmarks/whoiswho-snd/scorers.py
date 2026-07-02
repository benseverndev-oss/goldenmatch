"""A first-class set-overlap (Jaccard) scorer for the relational SND signal.

goldenmatch's built-in scorers compare string *similarity*; none of them consume
a precomputed set-overlap. But co-author overlap is THE make-or-break signal for
name disambiguation, so we register it as a real positive-weight field scorer via
the plugin surface (``PluginRegistry.register_scorer``) rather than faking it with
``token_sort`` on a concatenated string (which degrades badly as the co-author
count grows and rewards spurious substring overlap).

The scorer decodes the "|"-delimited set cells produced by ``normalize.encode_set``
and returns exact Jaccard. It exposes both ``score_pair`` (the required contract)
and the optional vectorized ``score_matrix`` so goldenmatch's block scorer avoids
the O(N^2) Python double-loop on large name-blocks.
"""
from __future__ import annotations

import numpy as np
from normalize import decode_set, jaccard

SCORER_NAME = "set_jaccard"


class SetJaccardScorer:
    """ScorerPlugin: Jaccard overlap of two "|"-delimited set cells."""

    name = SCORER_NAME

    def score_pair(self, val_a, val_b, *, tf_freqs=None) -> float | None:  # noqa: ARG002
        if val_a is None or val_b is None:
            return None
        return jaccard(decode_set(val_a), decode_set(val_b))

    def score_matrix(self, values, *, tf_freqs=None) -> np.ndarray:  # noqa: ARG002
        """NxN float32 Jaccard matrix.

        Built from a records x vocab boolean incidence matrix so intersections
        and unions are two integer matmuls -- the same trick the PPRL bloom
        scorer uses -- instead of N^2 python set ops.
        """
        sets = [decode_set(v if v is not None else "") for v in values]
        vocab = sorted({t for s in sets for t in s})
        n = len(sets)
        if not vocab:
            # every set empty -> all-zero (two empty sets share no evidence)
            return np.zeros((n, n), dtype=np.float32)
        idx = {t: i for i, t in enumerate(vocab)}
        inc = np.zeros((n, len(vocab)), dtype=np.float32)
        for i, s in enumerate(sets):
            for t in s:
                inc[i, idx[t]] = 1.0
        inter = inc @ inc.T                      # |A n B|
        sizes = inc.sum(axis=1)                  # |A|
        union = sizes[:, None] + sizes[None, :] - inter  # |A u B|
        with np.errstate(divide="ignore", invalid="ignore"):
            out = np.where(union > 0, inter / union, 0.0)
        return out.astype(np.float32)


_REGISTERED = False


def register(force: bool = False) -> None:
    """Register the set-Jaccard scorer into the shared PluginRegistry singleton.

    Must be called BEFORE constructing any ``GoldenMatchConfig`` that references
    ``set_jaccard`` -- the schema validator resolves unknown scorer names through
    ``PluginRegistry.instance().has_scorer`` at config-construction time.
    """
    global _REGISTERED
    if _REGISTERED and not force:
        return
    from goldenmatch.plugins.registry import PluginRegistry

    PluginRegistry.instance().register_scorer(SCORER_NAME, SetJaccardScorer())
    _REGISTERED = True
