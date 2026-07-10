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


TFIDF_SCORER_NAME = "tfidf_cosine"


class TfidfCosineScorer:
    """ScorerPlugin: TF-IDF cosine over a text field -- the TOPICAL bridge.

    Co-author overlap can't link two same-author papers that share NO co-author
    (the recall ceiling). Topical similarity can: a person tends to publish in one
    subfield, so their papers share domain vocabulary even with disjoint
    collaborators. This is a word-level TF-IDF cosine (torch-free, deterministic)
    -- the lexical-topical proxy for a semantic embedding; `record_embedding`
    (sentence-transformers) is a drop-in upgrade once that dep is present.

    IDF is computed WITHIN the name-block (the vectorized ``score_matrix`` sees the
    whole block), so terms common across the block downweight and distinctive terms
    drive the similarity -- exactly the per-name topical signal we want.
    """

    name = TFIDF_SCORER_NAME

    def _tfidf(self, values) -> np.ndarray:
        docs = [(v or "").split() for v in values]
        vocab: dict[str, int] = {}
        for d in docs:
            for t in d:
                if t not in vocab:
                    vocab[t] = len(vocab)
        n, V = len(docs), len(vocab)
        if V == 0:
            return np.zeros((n, 0), dtype=np.float32)
        tf = np.zeros((n, V), dtype=np.float32)
        df = np.zeros(V, dtype=np.float32)
        for i, d in enumerate(docs):
            for t in d:
                tf[i, vocab[t]] += 1.0
            for t in set(d):
                df[vocab[t]] += 1.0
        idf = np.log((n + 1.0) / (df + 1.0)) + 1.0  # smoothed
        x = tf * idf[None, :]
        norms = np.linalg.norm(x, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        return (x / norms).astype(np.float32)

    def score_matrix(self, values, *, tf_freqs=None) -> np.ndarray:  # noqa: ARG002
        x = self._tfidf(list(values))
        n = len(values) if hasattr(values, "__len__") else x.shape[0]
        if x.shape[1] == 0:
            return np.zeros((n, n), dtype=np.float32)
        s = x @ x.T
        np.clip(s, 0.0, 1.0, out=s)
        return s.astype(np.float32)

    def score_pair(self, val_a, val_b, *, tf_freqs=None) -> float | None:  # noqa: ARG002
        if val_a is None or val_b is None:
            return None
        return float(self.score_matrix([val_a, val_b])[0, 1])


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

    reg = PluginRegistry.instance()
    reg.register_scorer(SCORER_NAME, SetJaccardScorer())
    reg.register_scorer(TFIDF_SCORER_NAME, TfidfCosineScorer())
    _REGISTERED = True
