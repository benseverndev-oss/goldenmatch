"""A zero-config passage store for hybrid `ask` (`mode="hybrid"`).

Hybrid answering layers the raw source passages back into synthesis (the ground-truth
text the extracted triples drop) with the graph as a cross-passage multi-hop map --
measured +169% answer_match / +143% judge over local on the same graph. `ask` consumes
any object exposing ``retrieve(query, k) -> list[str]``; before this module the ONLY
such object lived in the benchmark harness (OpenAI + polars), so a plain library caller
who ran `mode="hybrid"` got the local fallback (no passages).

`PassageIndex` closes that: embed each source passage ONCE at build (via the SAME
`Embedder` the graph already uses -- no new provider, no OpenAI, no polars), keep an
`ANNBlocker` (FAISS IndexFlatIP + numpy fallback) over the L2-normalized vectors, and
answer per-query top-k by embedding only the query. It mirrors `entity_index.py`, but
HOLDS the embedder so its `retrieve(query, k)` satisfies the `passages` protocol `ask`
expects -- letting hybrid work out of the box:

    idx = PassageIndex.build(doc_ids, doc_texts, embedder)
    ask(query, store, ..., mode="hybrid", passages=idx)

PassageIndex OWNS the embedding array, so persistence is backend-agnostic (`np.save` +
JSON) and nothing reaches into `ANNBlocker` internals.
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


class PassageIndex:
    def __init__(self, corpus: np.ndarray, texts, embedder, *, ids=None, top_k: int = 50):
        self._corpus = np.asarray(corpus, dtype=np.float32)
        self._texts = [str(t) for t in texts]
        self._ids = [str(i) for i in ids] if ids is not None else list(range(len(self._texts)))
        self._embedder = embedder
        self._top_k = int(top_k)
        self._blocker = None
        self._build_blocker()

    def _build_blocker(self) -> None:
        from goldenmatch.core.ann_blocker import ANNBlocker  # lazy: keep import off the hot path

        self._blocker = ANNBlocker(top_k=self._top_k)
        if self._texts:
            self._blocker.build_index(self._corpus)

    @classmethod
    def build(cls, ids, texts, embedder, *, top_k: int = 50) -> PassageIndex:
        """Embed all non-empty passages ONCE, L2-normalize, index. `ids` runs parallel to
        `texts` (a doc/passage id per text, kept for provenance/debug -- retrieval returns
        the TEXT). Empty/whitespace-only passages are dropped so no zero vector pollutes the
        ANN neighborhood; `ids`/`texts` stay aligned to the kept rows. `top_k` = index
        capacity (max passages a single `retrieve` can return; passage_k must be <= it)."""
        ids = list(ids)
        kept_ids, kept_texts = [], []
        for i, t in zip(ids, texts):
            s = str(t).strip()
            if not s:
                continue
            kept_ids.append(i)
            kept_texts.append(str(t))
        if not kept_texts:
            return cls(np.zeros((0, 1), dtype=np.float32), [], embedder, ids=[], top_k=top_k)
        vecs = _l2(np.asarray(embedder.embed(kept_texts), dtype=np.float32))
        return cls(vecs, kept_texts, embedder, ids=kept_ids, top_k=top_k)

    def retrieve(self, query: str, k: int = 10) -> list[str]:
        """Embed the QUERY only, ANN top-k, return the passage TEXTS (rank-ordered). Satisfies
        the `passages` protocol `ask(mode="hybrid", passages=...)` calls. `k` is clamped to the
        index capacity (`top_k`) -- a retriever inside `ask` must degrade, never raise -- so ask
        keeps answering; rebuild with a larger `top_k` to lift the cap."""
        if not self._texts:
            return []
        q = _l2(np.asarray(self._embedder.embed([query]), dtype=np.float32)[0])
        rows = self._blocker.query_one(q)  # [(row, score)] rank-ordered, capped at top_k
        return [self._texts[r] for r, _ in rows[: max(0, k)]]

    def save(self, path: str) -> None:
        """Backend-agnostic: np.save the (normalized) corpus + a passages.json (ids + texts) +
        meta.json. NO faiss.write_index -- PassageIndex owns the array, so load rebuilds the
        ANNBlocker from it. The embedder is a live object; `load` takes it as an argument."""
        os.makedirs(path, exist_ok=True)
        np.save(os.path.join(path, "corpus.npy"), self._corpus)
        with open(os.path.join(path, "passages.json"), "w", encoding="utf-8") as fh:
            json.dump({"ids": self._ids, "texts": self._texts}, fh)
        with open(os.path.join(path, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"top_k": self._top_k}, fh)

    @classmethod
    def load(cls, path: str, embedder) -> PassageIndex:
        """Rebuild from `save`'s artifacts. `embedder` (a live object) is supplied by the caller --
        it is what `retrieve` embeds queries with, and must match the one used at build."""
        corpus = np.load(os.path.join(path, "corpus.npy"))
        with open(os.path.join(path, "passages.json"), encoding="utf-8") as fh:
            payload = json.load(fh)
        with open(os.path.join(path, "meta.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
        return cls(corpus, payload["texts"], embedder, ids=payload["ids"], top_k=meta["top_k"])

    def __len__(self) -> int:
        return len(self._texts)
