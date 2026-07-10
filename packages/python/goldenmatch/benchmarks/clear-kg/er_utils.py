"""Shared ER utilities for CLEAR-KG: normalization + a neighborhood set-overlap
scorer, registered into goldenmatch's plugin system.

Deliberately standalone (mirrors the whoiswho-snd harness rather than importing
it) so CLEAR-KG can split into its own public benchmark repo cleanly. The
through-line is real: SND matched authors by co-author *sets*; CLEAR-KG Track B
matches entity mentions by co-mention *sets* (the other entities named in the
same document). Same signal, same scorer.
"""
from __future__ import annotations

import re
import unicodedata

import numpy as np

SET_DELIM = "|"
SCORER_NAME = "comention_jaccard"

_PUNCT = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS = re.compile(r"\s+")


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s: str | None) -> str:
    """Canonicalize a surface string (a name, an org)."""
    if not s:
        return ""
    s = _strip_accents(str(s)).lower()
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def encode_set(items) -> str:
    """Sorted, de-duplicated, "|"-delimited set string (empty tokens dropped)."""
    seen = {t for t in (norm(i) for i in items) if t}
    return SET_DELIM.join(sorted(seen))


def decode_set(cell: str | None) -> set[str]:
    if not cell:
        return set()
    return {t for t in cell.split(SET_DELIM) if t}


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter else 0.0


class CoMentionJaccardScorer:
    """ScorerPlugin: Jaccard overlap of two "|"-delimited co-mention set cells.

    Two mentions of the SAME surface string ("J. Smith") that co-occur with
    DISJOINT other entities are different people -> jaccard 0 -> the weighted
    matchkey keeps them apart. This is the signal that separates homographs, which
    exact-surface / cosine-threshold ER cannot.
    """

    name = SCORER_NAME

    def score_pair(self, val_a, val_b, *, tf_freqs=None) -> float | None:  # noqa: ARG002
        if val_a is None or val_b is None:
            return None
        return jaccard(decode_set(val_a), decode_set(val_b))

    def score_matrix(self, values, *, tf_freqs=None) -> np.ndarray:  # noqa: ARG002
        sets = [decode_set(v if v is not None else "") for v in values]
        vocab = sorted({t for s in sets for t in s})
        n = len(sets)
        if not vocab:
            return np.zeros((n, n), dtype=np.float32)
        idx = {t: i for i, t in enumerate(vocab)}
        inc = np.zeros((n, len(vocab)), dtype=np.float32)
        for i, s in enumerate(sets):
            for t in s:
                inc[i, idx[t]] = 1.0
        inter = inc @ inc.T
        sizes = inc.sum(axis=1)
        union = sizes[:, None] + sizes[None, :] - inter
        with np.errstate(divide="ignore", invalid="ignore"):
            out = np.where(union > 0, inter / union, 0.0)
        return out.astype(np.float32)


_REGISTERED = False


def register(force: bool = False) -> None:
    """Register the co-mention scorer into goldenmatch's PluginRegistry singleton
    BEFORE building any config that references ``comention_jaccard``."""
    global _REGISTERED
    if _REGISTERED and not force:
        return
    from goldenmatch.plugins.registry import PluginRegistry

    PluginRegistry.instance().register_scorer(SCORER_NAME, CoMentionJaccardScorer())
    _REGISTERED = True
