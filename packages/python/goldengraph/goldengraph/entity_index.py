"""SP1: a persisted ANN index over entity canonical-name embeddings, keyed by entity_id.

Embed each entity name ONCE (batched by the embedder), keep an ANNBlocker (FAISS IndexFlatIP + numpy
fallback) over the L2-normalized vectors, and answer per-query top-k WITHOUT re-embedding the corpus --
the fix for seed_by_query's O(N)-embed-per-query blocker. EntityIndex OWNS the embedding array, so
persistence is backend-agnostic (np.save) and nothing reaches into ANNBlocker internals.
See docs/superpowers/specs/2026-07-02-goldengraph-entity-index-design.md.
"""
from __future__ import annotations

import json
import os

import numpy as np


def _l2(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype=np.float32)
    if mat.ndim == 1:
        return mat / (np.linalg.norm(mat) + 1e-12)
    return mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)


class EntityIndex:
    def __init__(self, corpus: np.ndarray, row_to_entity_id, top_k: int, dim: int):
        self._corpus = np.asarray(corpus, dtype=np.float32)
        self._row_to_entity_id = [int(x) for x in row_to_entity_id]
        self._top_k = int(top_k)
        self._dim = int(dim)
        self._blocker = None
        self._build_blocker()

    def _build_blocker(self) -> None:
        from goldenmatch.core.ann_blocker import ANNBlocker  # lazy: keep import off the hot path

        self._blocker = ANNBlocker(top_k=self._top_k)
        if len(self._row_to_entity_id):
            self._blocker.build_index(self._corpus)

    @classmethod
    def build(cls, entities, embedder, *, top_k: int = 50) -> EntityIndex:
        """Filter to real entity nodes (typ not 'literal:*', non-empty name -- mirrors seed_by_query),
        embed all names ONCE, L2-normalize, index. `top_k` = index capacity (max neighbors per query)."""
        ids, names = [], []
        for e in entities:
            typ = str(e.get("typ", ""))
            name = str(e.get("canonical_name", "")).strip()
            if typ.startswith("literal:") or not name:
                continue
            ids.append(int(e["entity_id"]))
            names.append(name)
        if not names:
            return cls(np.zeros((0, 1), dtype=np.float32), [], top_k, 1)
        vecs = _l2(np.asarray(embedder.embed(names), dtype=np.float32))
        return cls(vecs, ids, top_k, vecs.shape[1])

    def query(self, query: str, embedder, *, k: int = 5) -> list[int]:
        """Embed the QUERY only, ANN top-k, map rows->entity_ids. Requires k <= top_k.

        NOTE (SP2 callers): the returned entity_ids are those of the graph slice the index was BUILT
        from. entity_ids are slice-specific (see embed.py); do not reuse an index across a different
        `as_of` slice or the ids may not resolve there."""
        if k > self._top_k:
            raise ValueError(f"k={k} exceeds index top_k={self._top_k}; rebuild with a larger top_k")
        if not self._row_to_entity_id:
            return []
        q = _l2(np.asarray(embedder.embed([query]), dtype=np.float32)[0])
        rows = self._blocker.query_one(q)   # [(row, score)] rank-ordered, capped at top_k
        return [self._row_to_entity_id[r] for r, _ in rows[:k]]

    def save(self, path: str) -> None:
        """Backend-agnostic: np.save the (normalized) corpus + row_to_entity_id + a meta.json. NO
        faiss.write_index -- EntityIndex owns the array, so load rebuilds the ANNBlocker from it."""
        os.makedirs(path, exist_ok=True)
        np.save(os.path.join(path, "corpus.npy"), self._corpus)
        np.save(os.path.join(path, "row_to_entity_id.npy"),
                np.asarray(self._row_to_entity_id, dtype=np.int64))
        with open(os.path.join(path, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"top_k": self._top_k, "dim": self._dim}, fh)

    @classmethod
    def load(cls, path: str) -> EntityIndex:
        corpus = np.load(os.path.join(path, "corpus.npy"))
        ids = np.load(os.path.join(path, "row_to_entity_id.npy")).tolist()
        with open(os.path.join(path, "meta.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
        return cls(corpus, ids, meta["top_k"], meta["dim"])

    def __len__(self) -> int:
        return len(self._row_to_entity_id)
