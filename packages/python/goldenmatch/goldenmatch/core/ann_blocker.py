"""ANN (Approximate Nearest Neighbor) blocker for GoldenMatch.

Three interchangeable backends behind one surface, resolved per ``build_index``:

* **native HNSW** -- the ``goldenmatch-hnsw`` wheel (``goldenmatch_hnsw``), a
  pure-Rust ``IndexHNSWFlat`` with zero C dependencies. Sub-linear ANN queries
  for large corpora, installs everywhere the pure-Python package does.
* **FAISS** -- ``IndexFlatIP`` (the ``goldenmatch[embeddings]`` extra). Exact
  inner product, O(N) per probe.
* **numpy** -- a pure-numpy all-pairs inner-product fallback, zero deps.

Scores are the raw inner product on the FAISS and HNSW paths (byte-identical
between them) and the range-safe cosine on the numpy path; on the normal
GoldenMatch path the embedder emits L2-normalized vectors, so the three agree.

Backend selection (``_resolve_backend``) prefers **native HNSW -> FAISS ->
numpy**, but in ``auto`` mode HNSW is chosen only where it actually wins:
``n_vectors >= GOLDENMATCH_ANN_HNSW_MIN`` (default 4096) AND
``top_k <= GOLDENMATCH_ANN_HNSW_MAX_K`` (default 512). Below the size gate,
brute force is both faster and exact, so small N keeps the exact path and the
numpy-fallback parity contract holds unchanged. ``GOLDENMATCH_ANN_BACKEND`` in
``{auto, hnsw, faiss, numpy}`` forces a specific backend (gates ignored for an
explicit ``hnsw``).
"""

from __future__ import annotations

import importlib.util
import os

import numpy as np

# Detected once at import; tests flip these to exercise a specific backend.
_HAS_FAISS = importlib.util.find_spec("faiss") is not None
_HAS_HNSW = importlib.util.find_spec("goldenmatch_hnsw") is not None

# auto-mode size gates (env-overridable) — HNSW only earns its keep when the
# corpus is large AND only a few neighbors per probe are requested.
_HNSW_MIN_DEFAULT = 4096
_HNSW_MAX_K_DEFAULT = 512


def _resolve_backend(n_vectors: int, top_k: int) -> str:
    """Pick ``"hnsw"`` / ``"faiss"`` / ``"numpy"`` for this corpus.

    Honors ``GOLDENMATCH_ANN_BACKEND`` (forced), else auto-selects with the
    HNSW size gate. Always degrades to an available backend (never returns a
    backend whose library is absent).
    """
    forced = os.environ.get("GOLDENMATCH_ANN_BACKEND", "auto").strip().lower()
    if forced == "hnsw":
        if _HAS_HNSW:
            return "hnsw"
        return "faiss" if _HAS_FAISS else "numpy"
    if forced == "faiss":
        return "faiss" if _HAS_FAISS else "numpy"
    if forced == "numpy":
        return "numpy"
    # auto: prefer HNSW only above the size gate (its win is asymptotic); exact
    # below, which keeps small-N results identical to the brute-force paths.
    if _HAS_HNSW:
        try:
            min_n = int(os.environ.get("GOLDENMATCH_ANN_HNSW_MIN", _HNSW_MIN_DEFAULT))
            max_k = int(os.environ.get("GOLDENMATCH_ANN_HNSW_MAX_K", _HNSW_MAX_K_DEFAULT))
        except ValueError:
            min_n, max_k = _HNSW_MIN_DEFAULT, _HNSW_MAX_K_DEFAULT
        if n_vectors >= min_n and top_k <= max_k:
            return "hnsw"
    if _HAS_FAISS:
        return "faiss"
    return "numpy"


class ANNBlocker:
    """Build an inner-product index and query for top-K neighbors.

    Backed by FAISS (`IndexFlatIP`) when available; otherwise a numpy all-pairs
    inner-product fallback with byte-compatible top-K / self-exclusion /
    canonicalization semantics.
    """

    def __init__(self, top_k: int = 20):
        self.top_k = top_k
        self._index = None
        # numpy-fallback corpus (stored on build_index so query_* can use it)
        self._corpus: np.ndarray | None = None
        # native HNSW index (goldenmatch_hnsw.HnswIndex) when that backend wins.
        self._hnsw = None
        # Resolved on build_index; "numpy" until then.
        self._backend: str = "numpy"

    # HNSW graph parameters (env-overridable). Defaults mirror FAISS
    # IndexHNSWFlat(d, M=16) / hnswlib presets.
    @staticmethod
    def _hnsw_params() -> tuple[int, int, int]:
        def _env_int(name: str, default: int) -> int:
            try:
                return int(os.environ.get(name, default))
            except ValueError:
                return default

        m = _env_int("GOLDENMATCH_ANN_HNSW_M", 16)
        ef_c = _env_int("GOLDENMATCH_ANN_HNSW_EF_CONSTRUCTION", 200)
        ef_s = _env_int("GOLDENMATCH_ANN_HNSW_EF_SEARCH", 64)
        return m, ef_c, ef_s

    def build_index(self, embeddings: np.ndarray):
        """Build the inner-product index from (ideally L2-normalized) embeddings.

        The backend is resolved from the corpus size + ``top_k`` (see
        :func:`_resolve_backend`): native HNSW (``IndexHNSWFlat``) at scale,
        FAISS ``IndexFlatIP`` for exact medium-scale, else the numpy all-pairs
        fallback. All three share the ``_search`` result contract.
        """
        corpus = np.ascontiguousarray(embeddings.astype(np.float32))
        self._corpus = corpus
        self._index = None
        self._hnsw = None
        n = corpus.shape[0]
        self._backend = _resolve_backend(n, self.top_k)

        if self._backend == "hnsw":
            from goldenmatch_hnsw import HnswIndex

            dim = corpus.shape[1]
            m, ef_c, ef_s = self._hnsw_params()
            # ef_search must cover top_k to return k good neighbors.
            self._hnsw = HnswIndex(
                dim, m=m, ef_construction=ef_c, ef_search=max(ef_s, self.top_k)
            )
            if n:
                self._hnsw.add_batch(corpus.tobytes(), n)
            return
        if self._backend == "numpy":
            # numpy fallback: keep the corpus; scoring happens at query time.
            return
        import faiss

        dim = corpus.shape[1]
        self._index = faiss.IndexFlatIP(dim)  # inner product = cosine on normalized vectors
        self._index.add(corpus)

    def _hnsw_search(self, query_embeddings: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """HNSW replacement for ``faiss.IndexFlatIP.search``.

        Returns ``(scores, indices)`` of shape ``(n_query, k)`` where
        ``k = min(top_k, n_corpus)``. Scores are the raw inner product (FAISS
        ``IndexFlatIP`` order), descending per row. Self is NOT excluded here
        (mirrors faiss); callers drop it.
        """
        if self._hnsw is None:
            raise RuntimeError("Index not built. Call build_index first.")
        q = np.ascontiguousarray(query_embeddings.astype(np.float32))
        n_query = q.shape[0]
        n_corpus = len(self._hnsw)
        k = min(self.top_k, n_corpus)
        scores = np.full((n_query, k), -np.inf, dtype=np.float32)
        indices = np.full((n_query, k), -1, dtype=np.int64)
        if k == 0:
            return scores, indices
        rows = self._hnsw.search_batch(q.tobytes(), n_query, k)
        for i, row in enumerate(rows):
            for j, (idx, score) in enumerate(row):
                scores[i, j] = score
                indices[i, j] = idx
        return scores, indices

    def _np_search(self, query_embeddings: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """numpy replacement for ``faiss.IndexFlatIP.search``.

        Returns ``(scores, indices)`` of shape ``(n_query, k)`` where
        ``k = min(top_k, n_corpus)``.

        Ranking matches faiss's `IndexFlatIP`: top-k by descending RAW inner
        product against the stored corpus (faiss does NOT normalize internally,
        so the neighbor SET is identical to faiss given the same input). The
        emitted SCORE is the true cosine similarity (raw IP divided by the
        vector norms) so it stays in ``[-1, 1]`` even when the caller passes
        un-normalized vectors -- on the normal path the inputs are already
        normalized, so cosine == the raw IP faiss would return. Self is NOT
        excluded here (mirrors faiss); callers drop it, exactly as on the faiss
        path.
        """
        if self._corpus is None:
            raise RuntimeError("Index not built. Call build_index first.")
        q = query_embeddings.astype(np.float32)
        corpus = self._corpus
        ip = q @ corpus.T  # (n_query, n_corpus) raw inner-product matrix
        n_corpus = ip.shape[1]
        k = min(self.top_k, n_corpus)
        # Top-k per row by descending raw IP (argpartition for the cut, then
        # sort the k survivors so order matches faiss's sorted output).
        part = np.argpartition(-ip, k - 1, axis=1)[:, :k]
        part_ip = np.take_along_axis(ip, part, axis=1)
        order = np.argsort(-part_ip, axis=1)
        indices = np.take_along_axis(part, order, axis=1)
        # Cosine score for the emitted neighbors (range-safe, ranking-neutral).
        q_norm = np.linalg.norm(q, axis=1, keepdims=True)
        c_norm = np.linalg.norm(corpus, axis=1)
        q_norm = np.where(q_norm == 0.0, 1.0, q_norm)
        c_norm = np.where(c_norm == 0.0, 1.0, c_norm)
        cos = (q @ corpus.T) / q_norm / c_norm[np.newaxis, :]
        scores = np.take_along_axis(cos, indices, axis=1)
        return scores, indices

    def _search(self, query_embeddings: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Dispatch to the resolved backend's search.

        Returns ``(scores, indices)`` of shape ``(n_query, k)`` with consistent
        ranking semantics across all three backends.
        """
        if self._backend == "hnsw":
            return self._hnsw_search(query_embeddings)
        if self._backend == "numpy":
            return self._np_search(query_embeddings)
        if self._index is None:
            raise RuntimeError("Index not built. Call build_index first.")
        return self._index.search(query_embeddings.astype(np.float32), self.top_k)

    def query(self, query_embeddings: np.ndarray) -> list[tuple[int, int]]:
        """Find top-K neighbors for each query. Returns (query_idx, neighbor_idx) pairs."""
        scores, indices = self._search(query_embeddings)
        n_neighbors = indices.shape[1]
        pairs: set[tuple[int, int]] = set()
        for i in range(len(query_embeddings)):
            for j_idx in range(n_neighbors):
                neighbor = int(indices[i][j_idx])
                if neighbor != i and neighbor >= 0:
                    pairs.add((min(i, neighbor), max(i, neighbor)))
        return list(pairs)

    @property
    def index_size(self) -> int:
        """Number of vectors currently in the index."""
        if self._backend == "hnsw":
            return len(self._hnsw) if self._hnsw is not None else 0
        if self._backend == "numpy":
            return 0 if self._corpus is None else int(self._corpus.shape[0])
        return self._index.ntotal if self._index is not None else 0

    def add_to_index(self, embedding: np.ndarray) -> int:
        """Add a single embedding vector to the index (incremental).

        Args:
            embedding: (dim,) or (1, dim) numpy array, L2-normalized.

        Returns:
            The index position of the new vector.
        """
        vec = embedding.astype(np.float32)
        if vec.ndim == 1:
            vec = vec.reshape(1, -1)
        if self._backend == "hnsw":
            if self._hnsw is None:
                raise RuntimeError("Index not built. Call build_index first.")
            pos = len(self._hnsw)
            self._hnsw.add(np.ascontiguousarray(vec[0]).tolist())
            # keep the corpus mirror in sync for callers that read it back
            if self._corpus is not None:
                self._corpus = np.vstack([self._corpus, vec]).astype(np.float32)
            return pos
        if self._backend == "numpy":
            if self._corpus is None:
                raise RuntimeError("Index not built. Call build_index first.")
            pos = int(self._corpus.shape[0])
            self._corpus = np.vstack([self._corpus, vec]).astype(np.float32)
            return pos
        if self._index is None:
            raise RuntimeError("Index not built. Call build_index first.")
        pos = self._index.ntotal
        self._index.add(vec)
        return pos

    def query_one(self, embedding: np.ndarray) -> list[tuple[int, float]]:
        """Query top-K neighbors for a single vector.

        Args:
            embedding: (dim,) or (1, dim) numpy array, L2-normalized.

        Returns:
            List of (neighbor_index, similarity_score) tuples.
        """
        vec = embedding.astype(np.float32)
        if vec.ndim == 1:
            vec = vec.reshape(1, -1)
        scores, indices = self._search(vec)
        results = []
        for j in range(indices.shape[1]):
            neighbor = int(indices[0][j])
            if neighbor >= 0:
                results.append((neighbor, float(scores[0][j])))
        return results

    def query_with_scores(self, query_embeddings: np.ndarray) -> list[tuple[int, int, float]]:
        """Find top-K neighbors with similarity scores.

        Returns (idx_a, idx_b, cosine_similarity) tuples, ordered so idx_a < idx_b.
        """
        scores_matrix, indices = self._search(query_embeddings)
        n_neighbors = indices.shape[1]
        pairs: dict[tuple[int, int], float] = {}
        for i in range(len(query_embeddings)):
            for j_idx in range(n_neighbors):
                neighbor = int(indices[i][j_idx])
                if neighbor != i and neighbor >= 0:
                    pair = (min(i, neighbor), max(i, neighbor))
                    score = float(scores_matrix[i][j_idx])
                    if pair not in pairs or score > pairs[pair]:
                        pairs[pair] = score
        return [(a, b, s) for (a, b), s in pairs.items()]
