"""Persistent ANN index manager for database integration.

Owned pure-numpy flat (brute-force) inner-product index over the
``gm_embeddings`` table -- exact top-K by descending inner product, the same
semantics an exact ``IndexFlatIP`` provides. Vectors are persisted as an
``index_vectors.npy`` matrix (alongside ``id_map.npy`` + ``index_meta.json``);
no third-party ANN library is required (numpy is a base dependency).
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

from goldenmatch.db.connector import DatabaseConnector

logger = logging.getLogger(__name__)

DEFAULT_INDEX_DIR = ".goldenmatch_ann"
DEFAULT_MIN_COVERAGE = 0.10  # 10% of records must be embedded for ANN to activate


class PersistentANNIndex:
    """Manages a persistent flat inner-product index backed by gm_embeddings.

    Backed by an in-memory ``np.ndarray`` of vectors; ``query`` does an exact
    brute-force top-K inner-product search (``q @ vectors.T`` + argpartition),
    returning raw inner-product scores in descending order.
    """

    def __init__(
        self,
        index_dir: Path | str | None = None,
        connector: DatabaseConnector | None = None,
        source_table: str = "",
        model_name: str = "all-MiniLM-L6-v2",
        min_coverage: float = DEFAULT_MIN_COVERAGE,
    ):
        self.index_dir = Path(index_dir or DEFAULT_INDEX_DIR)
        self.connector = connector
        self.source_table = source_table
        self.model_name = model_name
        self.min_coverage = min_coverage

        self._vectors: np.ndarray | None = None  # (n, dim) float32 corpus
        self._id_map: list[int] = []  # positional index → DB record ID
        self._id_to_pos: dict[int, int] = {}  # DB record ID → positional index
        self._dim: int = 0
        self._loaded = False

    @property
    def is_available(self) -> bool:
        """True if index has enough embeddings for useful queries."""
        if self._vectors is None or len(self._id_map) == 0:
            return False
        if self.connector is None:
            return len(self._id_map) > 0
        try:
            total = self.connector.get_row_count(self.source_table)
            if total == 0:
                return False
            coverage = len(self._id_map) / total
            return coverage >= self.min_coverage
        except Exception:
            return len(self._id_map) > 0

    @property
    def record_count(self) -> int:
        return len(self._id_map)

    # ── Load / Build ──────────────────────────────────────────────────

    def load_or_build(self) -> None:
        """Load index from disk if fresh, rebuild from DB if stale."""
        disk_count = self._load_from_disk()
        db_count = self._get_db_embedding_count()

        if disk_count > 0 and disk_count >= db_count:
            logger.info("ANN index loaded from disk (%d embeddings)", disk_count)
            self._loaded = True
            return

        if disk_count > 0 and db_count > disk_count:
            # Append delta from DB
            delta = self._load_delta_from_db(disk_count)
            if delta is not None:
                ids, embeddings = delta
                self._add_to_index(ids, embeddings)
                logger.info("ANN index updated: %d → %d embeddings", disk_count, len(self._id_map))
                self.save()
            self._loaded = True
            return

        if db_count > 0:
            self._rebuild_from_db()
            self.save()
            self._loaded = True
            return

        logger.info("No embeddings available yet. ANN index empty.")

    def _load_from_disk(self) -> int:
        """Load vectors + id_map from disk. Returns record count or 0."""
        vectors_path = self.index_dir / "index_vectors.npy"
        meta_path = self.index_dir / "index_meta.json"
        idmap_path = self.index_dir / "id_map.npy"

        if not all(p.exists() for p in [vectors_path, meta_path, idmap_path]):
            return 0

        try:
            with open(meta_path) as f:
                meta = json.load(f)

            self._vectors = np.load(str(vectors_path)).astype(np.float32)
            self._id_map = np.load(str(idmap_path)).tolist()
            self._id_to_pos = {rid: i for i, rid in enumerate(self._id_map)}
            self._dim = meta.get("dim", 0)

            return len(self._id_map)
        except Exception as e:
            logger.warning("Failed to load ANN index from disk: %s", e)
            return 0

    def _get_db_embedding_count(self) -> int:
        """Count embeddings in gm_embeddings for this table."""
        if self.connector is None:
            return 0
        try:
            df = self.connector.read_query(
                f"SELECT COUNT(*) as cnt FROM gm_embeddings "
                f"WHERE source_table = '{self.source_table}' "
                f"AND model_name = '{self.model_name}'"
            )
            return int(df["cnt"][0]) if df.height > 0 else 0
        except Exception:
            return 0

    def _load_delta_from_db(self, existing_count: int) -> tuple[list[int], np.ndarray] | None:
        """Load embeddings from DB that aren't in the disk index."""
        try:
            df = self.connector.read_query(
                f"SELECT record_id, embedding FROM gm_embeddings "
                f"WHERE source_table = '{self.source_table}' "
                f"AND model_name = '{self.model_name}' "
                f"ORDER BY record_id "
                f"OFFSET {existing_count}"
            )
            if df.height == 0:
                return None

            ids = df["record_id"].to_list()
            embeddings = np.array([
                np.frombuffer(b, dtype=np.float32) for b in df["embedding"].to_list()
            ])
            return ids, embeddings
        except Exception as e:
            logger.warning("Failed to load delta embeddings: %s", e)
            return None

    def _rebuild_from_db(self) -> None:
        """Full rebuild of the vector index from gm_embeddings."""
        logger.info("Rebuilding ANN index from gm_embeddings...")

        df = self.connector.read_query(
            f"SELECT record_id, embedding FROM gm_embeddings "
            f"WHERE source_table = '{self.source_table}' "
            f"AND model_name = '{self.model_name}' "
            f"ORDER BY record_id"
        )

        if df.height == 0:
            return

        ids = df["record_id"].to_list()
        embeddings = np.array([
            np.frombuffer(b, dtype=np.float32) for b in df["embedding"].to_list()
        ]).astype(np.float32)

        self._dim = embeddings.shape[1]
        self._vectors = embeddings
        self._id_map = ids
        self._id_to_pos = {rid: i for i, rid in enumerate(ids)}

        logger.info("Rebuilt ANN index with %d embeddings (dim=%d)", len(ids), self._dim)

    # ── Query ─────────────────────────────────────────────────────────

    def query(self, embeddings: np.ndarray, top_k: int = 20) -> list[tuple[int, int, float]]:
        """Find top-K neighbors. Returns (query_idx, db_record_id, score).

        Exact brute-force inner-product search: top-K by descending raw inner
        product against the stored vectors (identical neighbor set + raw-IP
        scores to an exact ``IndexFlatIP.search``).
        """
        if self._vectors is None or self._vectors.shape[0] == 0:
            return []

        ntotal = self._vectors.shape[0]
        k = min(top_k, ntotal)
        if k <= 0:
            return []

        q = embeddings.astype(np.float32)
        ip = q @ self._vectors.T  # (n_query, ntotal) raw inner-product matrix
        # Top-k per row by descending raw IP (argpartition for the cut, then
        # sort the k survivors so order matches a sorted IndexFlatIP output).
        part = np.argpartition(-ip, k - 1, axis=1)[:, :k]
        part_ip = np.take_along_axis(ip, part, axis=1)
        order = np.argsort(-part_ip, axis=1)
        indices = np.take_along_axis(part, order, axis=1)
        scores = np.take_along_axis(ip, indices, axis=1)

        results = []
        for query_idx in range(len(embeddings)):
            for j in range(k):
                pos = int(indices[query_idx][j])
                if pos < 0 or pos >= len(self._id_map):
                    continue
                db_id = self._id_map[pos]
                score = float(scores[query_idx][j])
                results.append((query_idx, db_id, score))

        return results

    # ── Add ───────────────────────────────────────────────────────────

    def add(self, record_ids: list[int], embeddings: np.ndarray) -> None:
        """Add new embeddings to index and store in gm_embeddings."""
        if len(record_ids) == 0:
            return

        emb = embeddings.astype(np.float32)
        self._add_to_index(record_ids, emb)

        # Store in DB
        if self.connector is not None:
            self._store_embeddings_in_db(record_ids, emb)

    def _add_to_index(self, record_ids: list[int], embeddings: np.ndarray) -> None:
        """Append vectors + extend id_map (in-memory only, no DB write)."""
        if len(record_ids) == 0:
            return

        emb = embeddings.astype(np.float32)
        if self._vectors is None:
            self._vectors = emb
        else:
            self._vectors = np.vstack([self._vectors, emb])
        self._dim = self._vectors.shape[1]

        for rid in record_ids:
            pos = len(self._id_map)
            self._id_map.append(rid)
            self._id_to_pos[rid] = pos

    def _store_embeddings_in_db(self, record_ids: list[int], embeddings: np.ndarray) -> None:
        """Batch insert embeddings into gm_embeddings."""
        cursor = self.connector.conn.cursor()
        try:
            for rid, emb in zip(record_ids, embeddings):
                cursor.execute(
                    "INSERT INTO gm_embeddings (record_id, source_table, embedding, model_name) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (record_id, source_table, model_name) DO NOTHING",
                    (rid, self.source_table, emb.tobytes(), self.model_name),
                )
            self.connector.conn.commit()
        except Exception:
            self.connector.conn.rollback()
            raise
        finally:
            cursor.close()

    # ── Save ──────────────────────────────────────────────────────────

    def save(self) -> None:
        """Persist index to disk."""
        if self._vectors is None or len(self._id_map) == 0:
            return

        self.index_dir.mkdir(parents=True, exist_ok=True)

        np.save(str(self.index_dir / "index_vectors.npy"), self._vectors)
        np.save(str(self.index_dir / "id_map.npy"), np.array(self._id_map))

        meta = {
            "record_count": len(self._id_map),
            "dim": self._dim,
            "model": self.model_name,
            "source_table": self.source_table,
        }
        with open(self.index_dir / "index_meta.json", "w") as f:
            json.dump(meta, f)

        logger.info("Saved ANN index (%d embeddings) to %s", len(self._id_map), self.index_dir)
