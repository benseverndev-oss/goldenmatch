"""Embedding cache keyed by ``(model_id, normalized_text_hash)``.

In-memory always; optionally backed by a SQLite file so embeddings survive
across processes/runs. Vectors are stored as little-endian ``float32`` blobs.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np


class EmbeddingCache:
    """A two-tier (memory + optional SQLite) cache of embedding vectors.

    Each entry is a 1-D ``float32`` vector identified by ``(model_id,
    text_hash)`` — the same vector is reused for any record whose normalized
    text hashes to ``text_hash`` under that model.
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._mem: dict[tuple[str, str], np.ndarray] = {}
        self._path = Path(path) if path is not None else None
        self._conn: sqlite3.Connection | None = None
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._path))
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS embeddings ("
                "model_id TEXT NOT NULL, text_hash TEXT NOT NULL, "
                "dim INTEGER NOT NULL, vec BLOB NOT NULL, "
                "PRIMARY KEY (model_id, text_hash))"
            )
            self._conn.commit()

    def get(self, model_id: str, text_hash: str) -> np.ndarray | None:
        key = (model_id, text_hash)
        hit = self._mem.get(key)
        if hit is not None:
            return hit
        if self._conn is not None:
            row = self._conn.execute(
                "SELECT dim, vec FROM embeddings WHERE model_id = ? AND text_hash = ?",
                (model_id, text_hash),
            ).fetchone()
            if row is not None:
                dim, blob = row
                arr = np.frombuffer(blob, dtype="<f4").reshape(dim).copy()
                self._mem[key] = arr
                return arr
        return None

    def put(self, model_id: str, text_hash: str, vec: np.ndarray) -> np.ndarray:
        arr = np.ascontiguousarray(np.asarray(vec, dtype="<f4").reshape(-1))
        self._mem[(model_id, text_hash)] = arr
        if self._conn is not None:
            self._conn.execute(
                "INSERT OR REPLACE INTO embeddings (model_id, text_hash, dim, vec) "
                "VALUES (?, ?, ?, ?)",
                (model_id, text_hash, int(arr.shape[0]), arr.tobytes()),
            )
            self._conn.commit()
        return arr

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __len__(self) -> int:
        return len(self._mem)
