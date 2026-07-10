"""Persistent vector index -- stop rebuilding the embedding index every run (#1088).

The on-disk counterpart to ``retrieve_similar_records`` (#1089): embed a column
of records ONCE, persist the vectors + records + a manifest, and query across
later runs / processes without re-embedding. An incremental ``add`` extends the
index, and an embedding cache means re-indexing the same text never re-embeds it.

Built on the existing primitives -- ``get_embedder`` (any provider; the
zero-config ``"inhouse"`` model needs no cloud/torch) and ``ANNBlocker`` (FAISS
with a byte-identical numpy fallback). The index is an exact inner-product
(``IndexFlatIP``) index, so it is reconstituted from the persisted vectors on
load rather than serializing a FAISS graph -- persistence is therefore
faiss-independent and works identically under the numpy fallback.

Persistence layout (a directory):

    <dir>/manifest.json   -- {version, model, column, id_column, dim, count, ...}
    <dir>/vectors.npy     -- (count, dim) float32 corpus embeddings
    <dir>/records.parquet -- the per-row records (+ __row_id__, __vec_text__)

Scope: this is the local on-disk (FAISS/numpy) backend. The pgvector (Postgres)
and DuckDB-HNSW backends named in #1088 are follow-ups -- they implement the same
``build`` / ``add`` / ``query`` / ``save`` / ``load`` surface and
``RetrievedRecord`` result shape behind the one interface this class defines.
The query result type is shared with ``retrieve_similar_records`` so the
in-memory and persistent paths are drop-in interchangeable.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import numpy as np
from goldenmatch._polars_lazy import pl

from goldenmatch.core.ann_blocker import ANNBlocker
from goldenmatch.core.embedder import get_embedder
from goldenmatch.core.retrieval import RetrievedRecord

logger = logging.getLogger(__name__)

_MANIFEST = "manifest.json"
_VECTORS = "vectors.npy"
_RECORDS = "records.parquet"
_VERSION = 1
_ROW_ID = "__row_id__"
_TEXT = "__vec_text__"

# Default on-disk location (mirrors the .goldenmatch_* convention); env override.
DEFAULT_INDEX_DIR = os.environ.get("GOLDENMATCH_VECTOR_INDEX_DIR", ".goldenmatch_vectors")


def _atomic_write_bytes(path: Path, write_fn) -> None:
    """Write via a temp file in the same dir, then atomically replace ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    os.close(fd)
    tmp_path = Path(tmp)
    try:
        write_fn(tmp_path)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


class VectorIndex:
    """A persistent semantic index over records, queryable across runs.

    Create a fresh index with the constructor and ``build`` / ``add``; reopen a
    saved one with :meth:`load`. ``query`` embeds a free-text query and returns
    the most similar records as ``RetrievedRecord`` (the same shape as
    ``retrieve_similar_records``).
    """

    def __init__(
        self,
        path: str | Path = DEFAULT_INDEX_DIR,
        *,
        model: str = "inhouse",
        column: str | None = None,
        id_column: str | None = None,
        embedder: Any = None,
    ):
        self.path = Path(path)
        self.model = model
        self.column = column
        self.id_column = id_column
        self._embedder = embedder if embedder is not None else get_embedder(model)

        self._frame: pl.DataFrame | None = None  # records + __row_id__ + __vec_text__
        self._vectors: np.ndarray | None = None  # (count, dim) float32
        self._dim: int | None = None
        self._blocker: ANNBlocker | None = None
        # Embedding store: text -> vector, so re-indexing the same text never
        # re-embeds. Repopulated from disk on load.
        self._vec_cache: dict[str, np.ndarray] = {}

    # ── size / dunders ───────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        """How many records are indexed."""
        return 0 if self._frame is None else self._frame.height

    @property
    def dim(self) -> int | None:
        """The embedding dimensionality, or None until the index has content."""
        return self._dim

    def __len__(self) -> int:
        return self.size

    def __repr__(self) -> str:
        return (
            f"VectorIndex(path={str(self.path)!r}, model={self.model!r}, "
            f"column={self.column!r}, size={self.size}, dim={self._dim})"
        )

    # ── embedding (with cache) ───────────────────────────────────────────────

    def _embed_texts(self, texts: list[str]) -> np.ndarray:
        """Embed ``texts`` to (n, dim) float32, reusing cached vectors. Only the
        unique, not-yet-cached texts hit the embedder."""
        todo = [t for t in dict.fromkeys(texts) if t not in self._vec_cache]
        if todo:
            arr = self._embedder.embed_column(
                todo, cache_key=f"vi:{self.model}:{hash(tuple(todo))}"
            )
            arr = np.asarray(arr, dtype=np.float32)
            for t, vec in zip(todo, arr):
                self._vec_cache[t] = np.asarray(vec, dtype=np.float32)
        out = np.stack([self._vec_cache[t] for t in texts]).astype(np.float32)
        return out

    def _rebuild_blocker(self) -> None:
        self._blocker = None
        if self._vectors is not None and len(self._vectors):
            blk = ANNBlocker(top_k=self._vectors.shape[0])
            blk.build_index(self._vectors)
            self._blocker = blk

    # ── build / add ──────────────────────────────────────────────────────────

    def _prep_frame(
        self, df: pl.DataFrame, column: str, id_column: str | None
    ) -> tuple[pl.DataFrame, list[str]]:
        if column not in df.columns:
            raise ValueError(
                f"VectorIndex: column {column!r} not in dataframe (have {df.columns})"
            )
        keep = [c for c in df.columns if not c.startswith("__")]
        texts = ["" if v is None else str(v) for v in df[column].to_list()]
        base = self.size
        if id_column is not None and id_column in df.columns:
            row_ids = [int(r) for r in df[id_column].to_list()]
        else:
            row_ids = list(range(base, base + df.height))
        frame = df.select(keep).with_columns(
            pl.Series(_ROW_ID, row_ids, dtype=pl.Int64),
            pl.Series(_TEXT, texts),
        )
        return frame, texts

    def build(
        self,
        df: pl.DataFrame,
        column: str | None = None,
        *,
        id_column: str | None = None,
    ) -> VectorIndex:
        """(Re)build the index from a frame, replacing any current content.

        Args:
            df: the corpus frame.
            column: the text column to embed (defaults to the index's ``column``).
            id_column: optional column holding stable record ids; otherwise row
                position is used.

        Returns:
            ``self`` (for chaining).
        """
        column = column or self.column
        if column is None:
            raise ValueError("VectorIndex.build: a column to embed is required")
        self.column = column
        self.id_column = id_column if id_column is not None else self.id_column
        self._frame = None
        self._vectors = None
        self._dim = None
        # Fresh build: start from an empty index so row ids are 0-based.
        frame, texts = self._prep_frame(df, column, id_column)
        vectors = self._embed_texts(texts)
        self._frame = frame
        self._vectors = vectors
        self._dim = int(vectors.shape[1]) if vectors.size else None
        self._rebuild_blocker()
        return self

    def add(
        self,
        df: pl.DataFrame,
        column: str | None = None,
        *,
        id_column: str | None = None,
    ) -> VectorIndex:
        """Incrementally add records to the index (no full re-embed of existing
        rows; identical text reuses its cached vector).

        Returns:
            ``self`` (for chaining).
        """
        if self._frame is None:
            return self.build(df, column, id_column=id_column)
        column = column or self.column
        if column is None:
            raise ValueError("VectorIndex.add: a column to embed is required")
        frame, texts = self._prep_frame(df, column, id_column)
        vectors = self._embed_texts(texts)
        if vectors.shape[1] != self._vectors.shape[1]:
            raise ValueError(
                f"VectorIndex.add: embedding dim {vectors.shape[1]} != index dim "
                f"{self._vectors.shape[1]} (model/column mismatch)"
            )
        # diagonal_relaxed: union columns by name (missing -> null), relax dtypes,
        # so an add carrying a subset/superset of columns still concatenates.
        self._frame = pl.concat([self._frame, frame], how="diagonal_relaxed")
        self._vectors = np.vstack([self._vectors, vectors]).astype(np.float32)
        self._rebuild_blocker()
        return self

    # ── query ────────────────────────────────────────────────────────────────

    def query(
        self,
        query: str,
        *,
        k: int = 20,
        threshold: float = 0.0,
        filters: dict[str, Any] | None = None,
    ) -> list[RetrievedRecord]:
        """Retrieve the top-``k`` indexed records most similar to ``query``.

        Args:
            query: free-text query, embedded with the index's model.
            k: maximum records to return (ranked by cosine desc).
            threshold: minimum cosine similarity in ``[-1, 1]``.
            filters: optional ``{column: value}`` equality pre-filter on the
                records, applied before search.

        Returns:
            ``list[RetrievedRecord]`` ranked highest-similarity first. Empty when
            the query is blank, the index is empty, the filter excludes
            everything, or nothing clears ``threshold``.
        """
        if not query or self._frame is None or self.size == 0:
            return []

        frame = self._frame
        vectors = self._vectors
        positions = list(range(frame.height))
        if filters:
            mask = np.ones(frame.height, dtype=bool)
            for col, val in filters.items():
                if col not in frame.columns:
                    return []
                mask &= np.asarray((frame[col] == val).to_list(), dtype=bool)
            positions = [i for i, keep in enumerate(mask) if keep]
            if not positions:
                return []
            frame = frame[positions]
            vectors = vectors[positions]

        q_vec = self._embed_texts([str(query)])[0]

        if filters or self._blocker is None:
            blocker = ANNBlocker(top_k=vectors.shape[0])
            blocker.build_index(vectors)
        else:
            blocker = self._blocker
        neighbors = blocker.query_one(q_vec)  # [(local_pos, score), ...] desc

        rows = frame.to_dicts()
        out: list[RetrievedRecord] = []
        for pos, score in neighbors:
            if score < threshold:
                continue
            if pos < 0 or pos >= frame.height:
                continue
            row = rows[pos]
            record = {kk: vv for kk, vv in row.items() if not kk.startswith("__")}
            out.append(
                RetrievedRecord(row_id=int(row[_ROW_ID]), score=float(score), record=record)
            )
            if len(out) >= k:
                break
        return out

    # ── persistence ──────────────────────────────────────────────────────────

    def save(self, path: str | Path | None = None) -> VectorIndex:
        """Persist the index (manifest + vectors + records) to ``path``.

        Atomic per file: each artifact is written to a temp file in the target
        directory and renamed into place. Returns ``self``.
        """
        if path is not None:
            self.path = Path(path)
        if self._frame is None or self._vectors is None:
            raise ValueError("VectorIndex.save: nothing to save (build the index first)")
        self.path.mkdir(parents=True, exist_ok=True)

        def _write_vectors(p: Path) -> None:
            # Write through a handle so np.save uses the path verbatim (passing a
            # str makes it append a second ".npy", missing the atomic temp file).
            with open(p, "wb") as fh:
                np.save(fh, self._vectors, allow_pickle=False)

        _atomic_write_bytes(self.path / _VECTORS, _write_vectors)
        _atomic_write_bytes(self.path / _RECORDS, lambda p: self._frame.write_parquet(p))
        manifest = {
            "version": _VERSION,
            "model": self.model,
            "column": self.column,
            "id_column": self.id_column,
            "dim": self._dim,
            "count": self.size,
            "created_at": time.time(),
        }
        _atomic_write_bytes(
            self.path / _MANIFEST,
            lambda p: p.write_text(json.dumps(manifest, indent=2)),
        )
        logger.info("Saved VectorIndex (%d records) to %s", self.size, self.path)
        return self

    @classmethod
    def load(cls, path: str | Path, *, embedder: Any = None) -> VectorIndex:
        """Load a persisted index from ``path``.

        Raises:
            FileNotFoundError: if ``path`` is not a saved index directory.
        """
        path = Path(path)
        manifest_path = path / _MANIFEST
        if not manifest_path.is_file():
            raise FileNotFoundError(f"VectorIndex.load: no index at {path}")
        manifest = json.loads(manifest_path.read_text())

        idx = cls(
            path,
            model=manifest.get("model", "inhouse"),
            column=manifest.get("column"),
            id_column=manifest.get("id_column"),
            embedder=embedder,
        )
        idx._vectors = np.load(str(path / _VECTORS), allow_pickle=False).astype(np.float32)
        idx._frame = pl.read_parquet(path / _RECORDS)
        idx._dim = manifest.get("dim") or (
            int(idx._vectors.shape[1]) if idx._vectors.size else None
        )
        # Repopulate the embedding store from the persisted vectors so later
        # ``add`` calls reuse them (and identical query text is a cache hit).
        if _TEXT in idx._frame.columns:
            for text, vec in zip(idx._frame[_TEXT].to_list(), idx._vectors):
                idx._vec_cache.setdefault(str(text), np.asarray(vec, dtype=np.float32))
        idx._rebuild_blocker()
        return idx

    @classmethod
    def open(
        cls,
        path: str | Path = DEFAULT_INDEX_DIR,
        *,
        model: str = "inhouse",
        column: str | None = None,
        embedder: Any = None,
    ) -> VectorIndex:
        """Load the index at ``path`` if it exists, else create a fresh empty one."""
        if (Path(path) / _MANIFEST).is_file():
            return cls.load(path, embedder=embedder)
        return cls(path, model=model, column=column, embedder=embedder)
