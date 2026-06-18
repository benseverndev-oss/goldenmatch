"""ANN (Approximate Nearest Neighbor) blocker for GoldenMatch.

Uses FAISS when available (the ``goldenmatch[embeddings]`` extra). When faiss is
absent -- or when explicitly forced off via the module-level ``_HAS_FAISS`` flag
-- a pure-numpy all-pairs inner-product fallback runs instead. The fallback
produces the SAME neighbor set as faiss for small N (parity), so in-house ANN
blocking works with zero new dependencies at bench / medium scale; faiss stays
the path for scale.
"""

from __future__ import annotations

import importlib.util

import numpy as np

# Detected once at import; tests flip it to exercise the numpy fallback.
_HAS_FAISS = importlib.util.find_spec("faiss") is not None


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

    def build_index(self, embeddings: np.ndarray):
        """Build the inner-product index from (ideally L2-normalized) embeddings.

        FAISS path uses `IndexFlatIP`; numpy fallback stores the corpus for
        all-pairs scoring at query time.
        """
        corpus = embeddings.astype(np.float32)
        if not _HAS_FAISS:
            # numpy fallback: keep the corpus; scoring happens at query time.
            self._corpus = corpus
            self._index = None
            return
        import faiss

        dim = corpus.shape[1]
        self._index = faiss.IndexFlatIP(dim)  # inner product = cosine on normalized vectors
        self._index.add(corpus)
        self._corpus = corpus

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
        """Dispatch to faiss `IndexFlatIP.search` or the numpy fallback.

        Returns ``(scores, indices)`` of shape ``(n_query, k)`` with identical
        ranking semantics on both paths.
        """
        if not _HAS_FAISS:
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
        return self._index.ntotal if self._index is not None else 0

    def add_to_index(self, embedding: np.ndarray) -> int:
        """Add a single embedding vector to the FAISS index.

        Args:
            embedding: (dim,) or (1, dim) numpy array, L2-normalized.

        Returns:
            The index position of the new vector.
        """
        if self._index is None:
            raise RuntimeError("Index not built. Call build_index first.")
        vec = embedding.astype(np.float32)
        if vec.ndim == 1:
            vec = vec.reshape(1, -1)
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
        if not _HAS_FAISS:
            scores, indices = self._np_search(vec)
        else:
            if self._index is None:
                raise RuntimeError("Index not built. Call build_index first.")
            scores, indices = self._index.search(vec, self.top_k)
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
