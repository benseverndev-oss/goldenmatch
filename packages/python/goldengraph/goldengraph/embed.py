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


def seed_by_query(slice_graph, query: str, embedder: Embedder, *, k: int = 5, index=None) -> list[int]:
    """Top-`k` entity ids in `slice_graph` (a `PyGraph` from `as_of`) nearest the
    query by cosine over canonical-name embeddings. Tie-break: ascending
    `entity_id` (deterministic — stub/zero vectors tie often).

    When `index` (a `goldengraph.entity_index.EntityIndex` built once over the graph) is
    given, query the PREBUILT index -- embeds ONLY the query, not every entity name -- the
    O(1)-embed-per-query path for scale. `index=None` (default) keeps the re-embed path below
    (fine for small graphs; back-compat).

    Only real ENTITY nodes are seed candidates: literal-attribute value leaves
    (`typ` starts with ``literal:``, from GOLDENGRAPH_LITERAL_ATTRS) are excluded --
    they are answers reached by walking an edge FROM a seed entity, not query
    anchors, and embedding a raw value (a bare date / amount) both wastes budget and
    risks an empty/over-long input that 400s the WHOLE provider batch. Empty /
    whitespace names are dropped for the same reason (a provider rejects an empty
    input). Without this, a literal-attrs run 400s on every answer at seed time."""
    if index is not None:
        return index.query(query, embedder, k=k)
    ents = [
        e
        for e in slice_graph.entities()
        if not str(e.get("typ", "")).startswith("literal:")
        and str(e.get("canonical_name", "")).strip()
    ]
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
