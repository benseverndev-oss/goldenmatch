"""Embedder for GoldenMatch — sentence-transformer embedding and caching."""

from __future__ import annotations

import logging
import os
import threading
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
# Guards the check-then-set in get_embedder so concurrent first-use from the
# block-scoring threads can't construct (and load) the same heavy model twice.
_embedders_lock = threading.Lock()


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

    def embed(self, values: list[str]) -> np.ndarray:
        """Embed a list of strings (uncached passthrough to the wrapped provider)."""
        return np.asarray(self._provider.embed(values), dtype=np.float32)  # type: ignore[attr-defined]

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

    ``model_name`` is ``"inhouse:<path>"`` (path to a saved GoldenEmbedModel),
    bare ``"inhouse"`` with ``GOLDENMATCH_INHOUSE_MODEL`` set (path to a saved
    model), or bare ``"inhouse"`` with neither set — the zero-config default,
    which builds an untrained ``GoldenEmbedModel`` (fixed-seed random projection
    whose embeddings already approximate char-n-gram overlap; no path, no env,
    no cloud, no torch).
    """
    from goldenmatch.embeddings.providers import InHouseProvider

    path = model_name.split(":", 1)[1] if ":" in model_name else os.environ.get(
        "GOLDENMATCH_INHOUSE_MODEL"
    )
    if not path:
        # Zero-config: a freshly-constructed GoldenEmbedModel is already a usable
        # embedder (untrained random projection). Provider accepts the instance.
        from goldenmatch.embeddings.inhouse.model import GoldenEmbedModel

        return _ProviderEmbedder(InHouseProvider(GoldenEmbedModel()))
    return _ProviderEmbedder(InHouseProvider(path))


def _make_llama_embedder(model_name: str) -> _ProviderEmbedder:
    """Build a `_ProviderEmbedder` backed by a local GGUF model via llama.cpp.

    ``model_name`` is ``"llama:<path-to-gguf>"`` or bare ``"llama"`` (path from
    ``GOLDENMATCH_LLAMA_GGUF``). Path slicing preserves any ``:`` in the path.
    """
    from goldenmatch.embeddings.providers import LlamaGGUFProvider

    path = model_name[len("llama:"):] if model_name.startswith("llama:") else None
    return _ProviderEmbedder(LlamaGGUFProvider(path))


def inhouse_embedding_available() -> bool:
    """Whether the local in-house embedding model is reachable without cloud.

    True when a model path is discoverable via ``GOLDENMATCH_INHOUSE_MODEL`` AND
    the in-house inference stack is importable. Spec 2026-06-06 §Phase 3
    (availability probe) — lets the auto-config brain treat embedding/ANN as an
    eligible local candidate instead of a cloud drift-risk. Cheap + side-effect
    free; never raises.
    """
    if not os.environ.get("GOLDENMATCH_INHOUSE_MODEL"):
        return False
    try:
        from goldenmatch.embeddings.providers import InHouseProvider  # noqa: F401
    except Exception:
        return False
    return True


def get_embedder(model_name: str = "all-MiniLM-L6-v2") -> Embedder | _ProviderEmbedder:
    """Return a cached Embedder instance, using GPU routing when available.

    Checks GOLDENMATCH_GPU_MODE to select the right backend:
    - vertex: uses VertexEmbedder (Google Vertex AI, no local GPU needed)
    - remote: uses RemoteEmbedder (custom endpoint)
    - local/cpu_safe: uses local sentence-transformers Embedder

    A ``model_name`` of ``"inhouse"`` / ``"inhouse:<path>"`` routes to the local,
    in-house ER embedder (`goldenmatch.embeddings.inhouse`) — no cloud or torch.
    """
    # Fast path: already cached (lock-free).
    cached = _embedders.get(model_name)
    if cached is not None:
        return cached
    # Double-checked locking: under concurrent first-use from the block-scoring
    # threads an unguarded check-then-set would construct (and load) the heavy
    # model twice. Build once under the lock; re-check in case a peer beat us.
    with _embedders_lock:
        cached = _embedders.get(model_name)
        if cached is not None:
            return cached
        embedder = _build_embedder(model_name)
        _embedders[model_name] = embedder
        return embedder


def _build_embedder(model_name: str) -> Embedder | _ProviderEmbedder:
    """Construct (NOT cache) the embedder for ``model_name``. The caller holds
    ``_embedders_lock`` and is responsible for caching the result."""
    # In-house embedder: explicit, config-driven (the matchkey field's `model`).
    if model_name == "inhouse" or model_name.startswith("inhouse:"):
        return _make_inhouse_embedder(model_name)

    # Local GGUF embedder via llama.cpp (offline, no cloud/torch). Opt-in via
    # `model='llama:/path.gguf'` or GOLDENMATCH_LLAMA_GGUF.
    if model_name == "llama" or model_name.startswith("llama:"):
        return _make_llama_embedder(model_name)

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
            return VertexEmbedder()
        except ImportError:
            logger.error(
                "GOLDENMATCH_GPU_MODE=vertex but google-cloud-aiplatform is not installed. "
                "Install with: pip install goldenmatch[vertex]. Falling back to local embedder."
            )
            return Embedder(model_name)
        except Exception as e:
            logger.error(
                "VertexEmbedder initialization failed: %s. Falling back to local embedder.", e,
            )
            return Embedder(model_name)
    return Embedder(model_name)
