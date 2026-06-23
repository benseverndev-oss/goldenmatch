"""Embedding-seeded retrieval: turn a query into entity seeds.

Provider-agnostic `Embedder` protocol (mirrors `LLMClient`); the default
`GoldenmatchEmbedder` lazily wraps a goldenmatch embedding provider. Tests inject
a deterministic stub. `seed_by_query` operates on the `PyGraph` returned by
`as_of(v,t)` — entity ids are slice-specific, so seeds must be valid on the same
slice they query.

Recompute-per-query (embed every entity each call): correctness-first; a
persisted embedding sidecar + ANN index is the scale optimization, not built.
"""

from __future__ import annotations

import os
from typing import Protocol

import numpy as np

#: Max texts per provider embedding request. `seed_by_query` embeds EVERY entity
#: name in the graph in one call; past a few thousand entities that exceeds the
#: provider's per-request input cap (OpenAI: 2048) -> HTTP 400. Chunk under it.
_MAX_EMBED_BATCH = max(1, int(os.environ.get("GOLDENGRAPH_EMBED_BATCH", "1000")))


class Embedder(Protocol):
    """Embed texts → an array of shape (len(texts), dim)."""

    def embed(self, texts: list[str]) -> np.ndarray: ...


class GoldenmatchEmbedder:
    """Default embedder — wraps a goldenmatch embedding provider (lazy import).

    `provider` is a goldenmatch provider name ('local', 'inhouse', 'vertex',
    'openai', ...) or any object with `.embed(texts) -> np.ndarray`.
    """

    def __init__(self, provider: str = "local", *, model: str | None = None):
        self._provider_name = provider
        self._model = model
        self._provider = None

    def _ensure(self):
        if self._provider is None:
            from goldenmatch.embeddings.providers import resolve_provider

            self._provider = resolve_provider(self._provider_name, model=self._model)
        return self._provider

    def embed(self, texts: list[str]) -> np.ndarray:
        texts = list(texts)
        if not texts:
            return np.zeros((0, 0), dtype=float)
        prov = self._ensure()
        if len(texts) <= _MAX_EMBED_BATCH:
            return np.asarray(prov.embed(texts), dtype=float)
        # Chunk large batches so no single request exceeds the provider input cap.
        parts = [
            np.asarray(prov.embed(texts[i : i + _MAX_EMBED_BATCH]), dtype=float)
            for i in range(0, len(texts), _MAX_EMBED_BATCH)
        ]
        return np.vstack(parts)


def seed_by_query(slice_graph, query: str, embedder: Embedder, *, k: int = 5) -> list[int]:
    """Top-`k` entity ids in `slice_graph` (a `PyGraph` from `as_of`) nearest the
    query by cosine over canonical-name embeddings. Tie-break: ascending
    `entity_id` (deterministic — stub/zero vectors tie often)."""
    ents = slice_graph.entities()
    if not ents:
        return []
    ids = [int(e["entity_id"]) for e in ents]
    names = [str(e["canonical_name"]) for e in ents]
    vecs = np.asarray(embedder.embed([query] + names), dtype=float)
    q = vecs[0]
    mat = vecs[1:]
    qn = q / (np.linalg.norm(q) + 1e-12)
    mn = mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)
    sims = mn @ qn
    order = sorted(range(len(ids)), key=lambda i: (-float(sims[i]), ids[i]))
    return [ids[i] for i in order[:k]]
