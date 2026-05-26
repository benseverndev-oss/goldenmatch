"""Deterministic character n-gram featurizer for the in-house embedder.

Turns record text into a fixed-width float32 feature vector using signed
feature hashing over character n-grams — the lexical/typographic signal that
dominates entity resolution on names and addresses. The hash is BLAKE2b keyed
by a seed, so the featurization is byte-stable across processes, platforms, and
(future) the Rust ``goldenembed-rs`` runtime — no reliance on Python's salted
``hash()``.

This is the model's "tokenizer": it stays in Python (string ops don't belong in
the ONNX graph); only the learned projection is exported to ONNX.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class FeaturizerConfig:
    """Char n-gram feature-hashing configuration."""

    n_features: int = 4096
    ngram_min: int = 2
    ngram_max: int = 4
    lowercase: bool = True
    # Boundary marker padded around each text so prefix/suffix n-grams are
    # distinguishable ("^smith$" grams differ from mid-token "smith").
    boundary: str = "\x02"
    seed: int = 0


class CharNGramFeaturizer:
    """Signed feature hashing over character n-grams -> L2-normalized vectors."""

    def __init__(self, config: FeaturizerConfig | None = None) -> None:
        self.config = config or FeaturizerConfig()
        if self.config.n_features <= 0:
            raise ValueError("n_features must be positive")
        if self.config.ngram_min < 1 or self.config.ngram_max < self.config.ngram_min:
            raise ValueError("require 1 <= ngram_min <= ngram_max")

    @property
    def n_features(self) -> int:
        return self.config.n_features

    def _prepare(self, text: str | None) -> str:
        s = "" if text is None else str(text)
        if self.config.lowercase:
            s = s.lower()
        s = " ".join(s.split())  # collapse whitespace
        if not s:
            return ""  # empty/whitespace-only field -> no n-grams -> zero vector
        b = self.config.boundary
        return f"{b}{s}{b}"

    def _ngrams(self, text: str | None):
        s = self._prepare(text)
        cfg = self.config
        for n in range(cfg.ngram_min, cfg.ngram_max + 1):
            if len(s) < n:
                continue
            for i in range(len(s) - n + 1):
                yield s[i : i + n]

    def _hash(self, token: str) -> tuple[int, float]:
        """Map a token to (index, sign). Index in [0, n_features); sign in {-1, +1}."""
        digest = hashlib.blake2b(
            token.encode("utf-8"),
            digest_size=8,
            salt=self.config.seed.to_bytes(8, "little"),
        ).digest()
        h = int.from_bytes(digest, "little")
        idx = h % self.config.n_features
        sign = 1.0 if (h >> 63) & 1 else -1.0
        return idx, sign

    def transform(self, texts: list[str | None]) -> np.ndarray:
        """Featurize ``texts`` into an ``(n, n_features)`` L2-normalized matrix."""
        out = np.zeros((len(texts), self.config.n_features), dtype=np.float32)
        for r, text in enumerate(texts):
            for gram in self._ngrams(text):
                idx, sign = self._hash(gram)
                out[r, idx] += sign
            norm = float(np.linalg.norm(out[r]))
            if norm > 0.0:
                out[r] /= norm
        return out
