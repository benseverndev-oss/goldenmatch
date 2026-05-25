"""Local/private embedding runtime with a provider-agnostic contract.

One entry point, four interchangeable providers::

    from goldenmatch.embeddings import embed_records

    vecs = embed_records(texts, provider="local")   # sentence-transformers
    vecs = embed_records(texts, provider="vertex")   # Google Vertex AI
    vecs = embed_records(texts, provider="openai")   # OpenAI API
    vecs = embed_records(texts, provider="none")     # zero vectors (no model)

Results are cached by ``model_id + normalized_text_hash`` so identical (after
normalization) records embed once, and a SQLite-backed cache reuses vectors
across runs. ``provider`` also accepts any object implementing the
``EmbeddingProvider`` contract (``model_id`` + ``embed``).
"""
from __future__ import annotations

import hashlib

import numpy as np

from goldenmatch.embeddings.cache import EmbeddingCache
from goldenmatch.embeddings.providers import (
    EmbeddingProvider,
    LocalProvider,
    NoneProvider,
    OpenAIProvider,
    VertexProvider,
    resolve_provider,
)

__all__ = [
    "EmbeddingCache",
    "EmbeddingProvider",
    "LocalProvider",
    "NoneProvider",
    "OpenAIProvider",
    "VertexProvider",
    "embed_records",
    "normalize_text",
    "resolve_provider",
    "text_hash",
]


def normalize_text(text: str | None) -> str:
    """Lowercase, strip, and collapse internal whitespace.

    The cache key is built from this form so trivial formatting differences
    ("Foo  Bar" vs "foo bar") share one embedding.
    """
    if text is None:
        return ""
    return " ".join(str(text).split()).lower()


def text_hash(text: str) -> str:
    """Stable SHA-256 hex digest of ``text`` (already normalized by the caller)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embed_records(
    texts: list[str | None],
    *,
    provider: str | EmbeddingProvider = "local",
    model: str | None = None,
    cache: EmbeddingCache | str | None = None,
    normalize: bool = True,
    dim: int = 384,
) -> np.ndarray:
    """Embed ``texts`` into an ``(n, d)`` matrix, in input order.

    Args:
        texts: Record texts (``None`` is treated as empty string).
        provider: ``"local" | "vertex" | "openai" | "none"`` or a provider object.
        model: Model name override for the provider.
        cache: An :class:`EmbeddingCache`, a path to a SQLite cache file, or
            ``None`` for an ephemeral in-memory cache.
        normalize: Normalize text before hashing/embedding (default ``True``).
        dim: Vector width for the ``none`` provider and the empty-input case.

    Each unique normalized text is embedded at most once; cache hits (in-memory
    or on disk, keyed by ``model_id + text_hash``) skip the provider entirely.
    """
    prov = resolve_provider(provider, model=model, dim=dim)
    model_id = getattr(prov, "model_id", str(provider))

    own_cache = False
    if cache is None:
        ecache = EmbeddingCache()
    elif isinstance(cache, EmbeddingCache):
        ecache = cache
    else:
        ecache = EmbeddingCache(cache)
        own_cache = True

    try:
        if not texts:
            return np.zeros((0, dim), dtype=np.float32)

        prepared = [normalize_text(t) if normalize else ("" if t is None else str(t))
                    for t in texts]
        hashes = [text_hash(t) for t in prepared]

        resolved: dict[str, np.ndarray] = {}
        misses: dict[str, str] = {}  # hash -> text to embed
        for norm, h in zip(prepared, hashes):
            if h in resolved or h in misses:
                continue
            hit = ecache.get(model_id, h)
            if hit is not None:
                resolved[h] = hit
            else:
                misses[h] = norm

        if misses:
            miss_hashes = list(misses)
            vecs = np.asarray(prov.embed([misses[h] for h in miss_hashes]),
                              dtype=np.float32)
            for h, vec in zip(miss_hashes, vecs):
                resolved[h] = ecache.put(model_id, h, vec)

        return np.stack([resolved[h] for h in hashes])
    finally:
        if own_cache:
            ecache.close()
