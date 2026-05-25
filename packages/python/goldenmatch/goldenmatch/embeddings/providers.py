"""Embedding providers behind a single contract.

Every provider exposes ``model_id: str`` (used as the cache namespace) and
``embed(texts: list[str]) -> np.ndarray`` returning an ``(n, dim)`` array. Heavy
backends (sentence-transformers, Vertex, OpenAI) are imported lazily so the
``none`` provider and the cache/dispatch layer work with no optional deps.
"""
from __future__ import annotations

import json
import os
import urllib.request
import uuid
from typing import Protocol, runtime_checkable

import numpy as np

_DEFAULT_LOCAL_MODEL = "all-MiniLM-L6-v2"
_DEFAULT_OPENAI_MODEL = "text-embedding-3-small"


@runtime_checkable
class EmbeddingProvider(Protocol):
    model_id: str

    def embed(self, texts: list[str]) -> np.ndarray: ...


class NoneProvider:
    """Returns deterministic zero vectors — the ``provider="none"`` contract.

    Lets callers run the embedding code path with no model and no network; the
    embedding signal is neutral rather than a hard dependency.
    """

    def __init__(self, dim: int = 384) -> None:
        self.dim = dim
        self.model_id = "none"

    def embed(self, texts: list[str]) -> np.ndarray:
        return np.zeros((len(texts), self.dim), dtype=np.float32)


class LocalProvider:
    """Local sentence-transformers embeddings (no cloud dependency)."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model or _DEFAULT_LOCAL_MODEL
        self.model_id = f"local:{self.model}"
        self._embedder = None

    def embed(self, texts: list[str]) -> np.ndarray:
        if self._embedder is None:
            from goldenmatch.core.embedder import Embedder

            self._embedder = Embedder(self.model)
        # Bypass Embedder's whole-array cache (we cache per-text ourselves) by
        # handing it a unique key each call.
        arr = self._embedder.embed_column(list(texts), cache_key=uuid.uuid4().hex)
        return np.asarray(arr, dtype=np.float32)


class VertexProvider:
    """Google Vertex AI embeddings."""

    def __init__(self, model: str | None = None) -> None:
        self.model = model
        self.model_id = f"vertex:{model}" if model else "vertex"
        self._embedder = None

    def embed(self, texts: list[str]) -> np.ndarray:
        if self._embedder is None:
            from goldenmatch.core.vertex_embedder import VertexEmbedder

            self._embedder = (
                VertexEmbedder(model=self.model) if self.model else VertexEmbedder()
            )
        arr = self._embedder.embed_column(list(texts), cache_key=uuid.uuid4().hex)
        return np.asarray(arr, dtype=np.float32)


class OpenAIProvider:
    """OpenAI embeddings via the REST API (stdlib only, no SDK required)."""

    def __init__(self, model: str | None = None, api_key: str | None = None) -> None:
        self.model = model or _DEFAULT_OPENAI_MODEL
        self.model_id = f"openai:{self.model}"
        self._api_key = api_key

    def embed(self, texts: list[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        api_key = self._api_key or os.environ.get("OPENAI_API_KEY")
        if not api_key:
            raise RuntimeError(
                "provider='openai' requires an API key (pass api_key= or set "
                "OPENAI_API_KEY)"
            )
        payload = json.dumps({"model": self.model, "input": list(texts)}).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/embeddings",
            data=payload,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:  # noqa: S310 - fixed https endpoint
            body = json.loads(resp.read().decode())
        rows = sorted(body["data"], key=lambda d: d["index"])
        return np.asarray([r["embedding"] for r in rows], dtype=np.float32)


def resolve_provider(
    provider: str | EmbeddingProvider,
    *,
    model: str | None = None,
    dim: int = 384,
) -> EmbeddingProvider:
    """Turn a provider name into a provider object, or pass an object through."""
    if not isinstance(provider, str):
        return provider
    name = provider.lower()
    if name == "none":
        return NoneProvider(dim=dim)
    if name == "local":
        return LocalProvider(model)
    if name == "vertex":
        return VertexProvider(model)
    if name == "openai":
        return OpenAIProvider(model)
    raise ValueError(
        f"unknown embedding provider {provider!r} "
        "(expected 'local', 'vertex', 'openai', 'none', or a provider object)"
    )
