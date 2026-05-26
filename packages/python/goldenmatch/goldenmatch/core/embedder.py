"""Embedder for GoldenMatch — sentence-transformer embedding and caching."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

logger = logging.getLogger(__name__)


class Embedder:
    """Wraps a sentence-transformer model with lazy loading and caching."""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self.model_name = model_name
        self._model = None
        self._cache: dict[str, np.ndarray] = {}

    def _load_model(self):
        """Lazy-load the sentence-transformer model."""
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            raise ImportError(
                "Embedding features require sentence-transformers. "
                "Install with: pip install goldenmatch[embeddings]"
            )
        self._model = SentenceTransformer(self.model_name)

    def embed_column(self, values: list[str], cache_key: str) -> np.ndarray:
        """Embed a list of string values. Returns (n, dim) array. Cached by cache_key."""
        if cache_key in self._cache:
            return self._cache[cache_key]
        if self._model is None:
            self._load_model()
        # Replace None/empty with empty string
        clean = [str(v) if v is not None and str(v).strip() else "" for v in values]
        embeddings = self._model.encode(
            clean, show_progress_bar=False, normalize_embeddings=True,
        )
        self._cache[cache_key] = embeddings
        return embeddings

    def cosine_similarity_matrix(self, embeddings: np.ndarray) -> np.ndarray:
        """NxN cosine similarity matrix. Embeddings must be L2-normalized."""
        return embeddings @ embeddings.T

    def save_cache(self, path: Path) -> None:
        """Persist embedding cache to disk as .npy files."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        for key, arr in self._cache.items():
            file_path = path / f"{key}.npy"
            np.save(file_path, arr)
        logger.info("Saved %d cached embeddings to %s", len(self._cache), path)

    def load_cache(self, path: Path) -> None:
        """Load embedding cache from disk (.npy files)."""
        path = Path(path)
        if not path.is_dir():
            return
        loaded = 0
        for npy_file in path.glob("*.npy"):
            key = npy_file.stem
            if key not in self._cache:
                self._cache[key] = np.load(npy_file)
                loaded += 1
        if loaded:
            logger.info("Loaded %d cached embeddings from %s", loaded, path)


# ---------------------------------------------------------------------------
# Module-level cache for embedder instances
# ---------------------------------------------------------------------------

_embedders: dict[str, Embedder | _ProviderEmbedder] = {}


class _ProviderEmbedder:
    """Adapts a ``goldenmatch.embeddings`` provider to the ``Embedder`` interface
    the scorer uses (``embed_column`` + ``cosine_similarity_matrix``).

    Lets the in-house embedder (and any other provider) back the
    ``embedding`` / ``record_embedding`` scorers without changing the scorer.
    """

    def __init__(self, provider: object) -> None:
        self._provider = provider
        self._cache: dict[str, np.ndarray] = {}
        self.model_name = getattr(provider, "model_id", "provider")

    def embed_column(self, values: list[str], cache_key: str) -> np.ndarray:
        if cache_key in self._cache:
            return self._cache[cache_key]
        clean = [str(v) if v is not None and str(v).strip() else "" for v in values]
        emb = np.asarray(self._provider.embed(clean), dtype=np.float32)  # type: ignore[attr-defined]
        self._cache[cache_key] = emb
        return emb

    def cosine_similarity_matrix(self, embeddings: np.ndarray) -> np.ndarray:
        # Normalize defensively — not every provider returns unit vectors.
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0.0] = 1.0
        unit = embeddings / norms
        return unit @ unit.T


def _make_inhouse_embedder(model_name: str) -> _ProviderEmbedder:
    """Build a `_ProviderEmbedder` for the in-house model.

    ``model_name`` is ``"inhouse:<path>"`` (path to a saved GoldenEmbedModel) or
    bare ``"inhouse"`` (path from ``GOLDENMATCH_INHOUSE_MODEL``).
    """
    from goldenmatch.embeddings.providers import InHouseProvider

    path = model_name.split(":", 1)[1] if ":" in model_name else os.environ.get(
        "GOLDENMATCH_INHOUSE_MODEL"
    )
    if not path:
        raise ValueError(
            "in-house embedder requires a model path: set the matchkey field "
            "`model` to 'inhouse:/path/to/model', or set GOLDENMATCH_INHOUSE_MODEL. "
            "Train one with goldenmatch.embeddings.inhouse.train_embedder(...)."
        )
    return _ProviderEmbedder(InHouseProvider(path))


def get_embedder(model_name: str = "all-MiniLM-L6-v2") -> Embedder | _ProviderEmbedder:
    """Return a cached Embedder instance, using GPU routing when available.

    Checks GOLDENMATCH_GPU_MODE to select the right backend:
    - vertex: uses VertexEmbedder (Google Vertex AI, no local GPU needed)
    - remote: uses RemoteEmbedder (custom endpoint)
    - local/cpu_safe: uses local sentence-transformers Embedder

    A ``model_name`` of ``"inhouse"`` / ``"inhouse:<path>"`` routes to the local,
    in-house ER embedder (`goldenmatch.embeddings.inhouse`) — no cloud or torch.
    """
    # In-house embedder: explicit, config-driven (the matchkey field's `model`).
    if model_name == "inhouse" or model_name.startswith("inhouse:"):
        if model_name not in _embedders:
            _embedders[model_name] = _make_inhouse_embedder(model_name)
        return _embedders[model_name]

    if model_name not in _embedders:
        try:
            from goldenmatch.core.gpu import detect_gpu_mode
            mode = detect_gpu_mode()
        except Exception:
            logger.warning("GPU detection failed, defaulting to local embedder.", exc_info=True)
            mode = None

        if mode is not None and mode.value == "vertex":
            try:
                from goldenmatch.core.vertex_embedder import VertexEmbedder
                logger.info("GPU mode=vertex: using VertexEmbedder (ignoring model_name=%s)", model_name)
                _embedders[model_name] = VertexEmbedder()
            except ImportError:
                logger.error(
                    "GOLDENMATCH_GPU_MODE=vertex but google-cloud-aiplatform is not installed. "
                    "Install with: pip install goldenmatch[vertex]. Falling back to local embedder."
                )
                _embedders[model_name] = Embedder(model_name)
            except Exception as e:
                logger.error(
                    "VertexEmbedder initialization failed: %s. Falling back to local embedder.", e,
                )
                _embedders[model_name] = Embedder(model_name)
        else:
            _embedders[model_name] = Embedder(model_name)
    return _embedders[model_name]
