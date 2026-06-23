"""Pluggable vector-store backends behind one interface (#1088, epic #1087).

``VectorIndex`` (``core/vector_index.py``) is the local on-disk backend: it
persists an embedding index to a directory and survives across runs. This module
adds the two other backends named in #1088 -- **pgvector** (Postgres) and
**DuckDB-HNSW** -- behind the *same* surface (``build`` / ``add`` / ``query`` /
``save`` / ``load`` / ``open``, returning ``RetrievedRecord``), plus an
``open_vector_index`` factory so a caller picks local-file / pgvector / duckdb
uniformly.

Design
------
The local backend keeps records as a Polars frame + a numpy matrix. The two SQL
backends instead store one row per record in a single table with a uniform
shape, so they share all the marshaling:

    __row_id__   BIGINT      -- stable record id (id_column or row position)
    __vec_text__ TEXT        -- the embedded source text (for cache repopulation)
    __vec__      vector/ARRAY -- the embedding (pgvector ``vector`` / duckdb ``FLOAT[]``)
    __record__   JSON(B)     -- the non-internal record columns as a JSON object

Search pushes the top-k cosine ranking into the database (pgvector's ``<=>``
operator with an HNSW index; DuckDB's ``array_cosine_similarity`` accelerated by
a ``vss`` HNSW index when the extension is available, brute-force otherwise).
Both return ``RetrievedRecord`` with the same cosine-``[-1, 1]`` score and
internal-column-stripped record dict as ``VectorIndex`` / ``retrieve_similar_records``.

Both SQL backends embed with the same zero-config in-house model by default (no
cloud/torch) and keep an in-memory text->vector cache repopulated on load, so
re-indexing the same text never re-embeds it -- matching ``VectorIndex``.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Protocol, runtime_checkable

import numpy as np
import polars as pl

from goldenmatch.core.embedder import get_embedder
from goldenmatch.core.retrieval import RetrievedRecord

logger = logging.getLogger(__name__)

_ROW_ID = "__row_id__"
_TEXT = "__vec_text__"
_VEC = "__vec__"
_RECORD = "__record__"


# ── shared embedding + record marshaling ─────────────────────────────────────


def embed_texts(
    embedder: Any, model: str, cache: dict[str, np.ndarray], texts: list[str]
) -> np.ndarray:
    """Embed ``texts`` to ``(n, dim)`` float32, reusing ``cache``.

    Only the unique, not-yet-cached texts hit the embedder -- the same
    re-embed-never policy ``VectorIndex`` uses. Mutates ``cache`` in place.
    """
    todo = [t for t in dict.fromkeys(texts) if t not in cache]
    if todo:
        arr = np.asarray(
            embedder.embed_column(todo, cache_key=f"vs:{model}:{hash(tuple(todo))}"),
            dtype=np.float32,
        )
        for t, vec in zip(todo, arr):
            cache[t] = np.asarray(vec, dtype=np.float32)
    if not texts:
        return np.empty((0, 0), dtype=np.float32)
    return np.stack([cache[t] for t in texts]).astype(np.float32)


def prep_rows(
    df: pl.DataFrame, column: str, id_column: str | None, base: int
) -> tuple[list[int], list[str], list[str]]:
    """Return ``(row_ids, texts, record_jsons)`` for ``df``.

    ``record_jsons`` carries only the non-internal columns. Row ids come from
    ``id_column`` when present, else ``base + position`` (so an incremental add
    continues the numbering) -- identical to ``VectorIndex._prep_frame``.
    """
    if column not in df.columns:
        raise ValueError(f"vector store: column {column!r} not in dataframe (have {df.columns})")
    keep = [c for c in df.columns if not c.startswith("__")]
    texts = ["" if v is None else str(v) for v in df[column].to_list()]
    if id_column is not None and id_column in df.columns:
        row_ids = [int(r) for r in df[id_column].to_list()]
    else:
        row_ids = list(range(base, base + df.height))
    records = df.select(keep).to_dicts()
    record_jsons = [json.dumps(r, default=str) for r in records]
    return row_ids, texts, record_jsons


def _record_from_json(blob: Any) -> dict[str, Any]:
    if blob is None:
        return {}
    if isinstance(blob, (dict, list)):
        return blob if isinstance(blob, dict) else {}
    try:
        return json.loads(blob)
    except (TypeError, ValueError):
        return {}


@runtime_checkable
class VectorStore(Protocol):
    """The unified surface every vector backend implements.

    ``VectorIndex`` (local on-disk), ``DuckDBVectorIndex`` and
    ``PgVectorIndex`` all satisfy this -- so a caller can swap backends without
    changing call sites. ``query`` always returns ``RetrievedRecord``.
    """

    @property
    def size(self) -> int: ...
    @property
    def dim(self) -> int | None: ...
    def build(self, df: pl.DataFrame, column: str | None = None, *, id_column: str | None = None) -> VectorStore: ...
    def add(self, df: pl.DataFrame, column: str | None = None, *, id_column: str | None = None) -> VectorStore: ...
    def query(self, query: str, *, k: int = 20, threshold: float = 0.0, filters: dict[str, Any] | None = None) -> list[RetrievedRecord]: ...
    def save(self) -> VectorStore: ...


# ── DuckDB-HNSW backend ──────────────────────────────────────────────────────


class DuckDBVectorIndex:
    """Persistent vector index backed by a DuckDB database file (#1088).

    Stores one row per record in a single table; ranks with the core
    ``array_cosine_similarity`` function, accelerated by a ``vss`` HNSW index
    when the extension is loadable (brute-force cosine otherwise -- same
    results, just slower). The DuckDB file *is* the persistence: writes land in
    it immediately and a later process can ``open`` the same path.

    Requires ``duckdb`` (``pip install goldenmatch[duckdb]``).
    """

    _MANIFEST = "gm_vector_manifest"
    _VERSION = 1

    def __init__(
        self,
        path: str = ":memory:",
        *,
        table: str = "gm_vectors",
        model: str = "inhouse",
        column: str | None = None,
        id_column: str | None = None,
        embedder: Any = None,
    ):
        try:
            import duckdb  # noqa: F401
        except ImportError as exc:  # pragma: no cover - import guard
            raise ImportError(
                "DuckDBVectorIndex needs duckdb: pip install goldenmatch[duckdb]"
            ) from exc
        self.path = str(path)
        self.table = table
        self.model = model
        self.column = column
        self.id_column = id_column
        self._embedder = embedder if embedder is not None else get_embedder(model)
        self._dim: int | None = None
        self._vec_cache: dict[str, np.ndarray] = {}
        self._con = duckdb.connect(self.path)
        self._hnsw = self._try_load_vss()
        # Adopt an existing index at this path (cross-process / reopen).
        if self._table_exists():
            self._adopt_existing()

    # -- duckdb helpers --
    def _try_load_vss(self) -> bool:
        try:
            self._con.execute("INSTALL vss")
            self._con.execute("LOAD vss")
            # HNSW index persistence in a DB file is experimental; opt in so the
            # index survives reopen. Brute-force still works if this is ignored.
            self._con.execute("SET hnsw_enable_experimental_persistence=true")
            return True
        except Exception as exc:  # pragma: no cover - depends on network/extension
            logger.info("DuckDBVectorIndex: vss/HNSW unavailable, using brute-force cosine (%s)", exc)
            return False

    def _table_exists(self) -> bool:
        row = self._con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [self.table],
        ).fetchone()
        return bool(row and row[0])

    def _adopt_existing(self) -> None:
        man = {
            k: v
            for k, v in self._con.execute(f"SELECT key, value FROM {self._MANIFEST}").fetchall()
        } if self._manifest_exists() else {}
        self.model = man.get("model", self.model)
        self.column = man.get("column") or self.column
        self.id_column = man.get("id_column") or self.id_column
        self._dim = int(man["dim"]) if man.get("dim") not in (None, "", "None") else self._infer_dim()
        # Repopulate the embedding cache from stored vectors so add/query reuse them.
        if self.size:
            for text, vec in self._con.execute(
                f"SELECT {_TEXT}, {_VEC} FROM {self.table}"
            ).fetchall():
                self._vec_cache.setdefault(str(text), np.asarray(vec, dtype=np.float32))

    def _manifest_exists(self) -> bool:
        row = self._con.execute(
            "SELECT count(*) FROM information_schema.tables WHERE table_name = ?",
            [self._MANIFEST],
        ).fetchone()
        return bool(row and row[0])

    def _infer_dim(self) -> int | None:
        row = self._con.execute(f"SELECT len({_VEC}) FROM {self.table} LIMIT 1").fetchone()
        return int(row[0]) if row and row[0] else None

    # -- size / dim --
    @property
    def size(self) -> int:
        if not self._table_exists():
            return 0
        return int(self._con.execute(f"SELECT count(*) FROM {self.table}").fetchone()[0])

    @property
    def dim(self) -> int | None:
        return self._dim

    def __len__(self) -> int:
        return self.size

    def __repr__(self) -> str:
        return f"DuckDBVectorIndex(path={self.path!r}, column={self.column!r}, size={self.size}, dim={self._dim})"

    # -- build / add --
    def _create_table(self, dim: int) -> None:
        self._con.execute(f"DROP TABLE IF EXISTS {self.table}")
        self._con.execute(
            f"CREATE TABLE {self.table} ("
            f"{_ROW_ID} BIGINT, {_TEXT} VARCHAR, {_VEC} FLOAT[{dim}], {_RECORD} JSON)"
        )

    def _build_hnsw(self) -> None:
        if not self._hnsw:
            return
        try:
            self._con.execute(f"DROP INDEX IF EXISTS {self.table}_hnsw")
            self._con.execute(
                f"CREATE INDEX {self.table}_hnsw ON {self.table} "
                f"USING HNSW ({_VEC}) WITH (metric = 'cosine')"
            )
        except Exception as exc:  # pragma: no cover - extension edge cases
            logger.info("DuckDBVectorIndex: HNSW index build skipped (%s)", exc)

    def _insert(self, df: pl.DataFrame, column: str, id_column: str | None) -> None:
        row_ids, texts, records = prep_rows(df, column, id_column, base=self.size)
        vectors = embed_texts(self._embedder, self.model, self._vec_cache, texts)
        if self._dim is None:
            self._dim = int(vectors.shape[1]) if vectors.size else None
        elif vectors.size and vectors.shape[1] != self._dim:
            raise ValueError(
                f"DuckDBVectorIndex: embedding dim {vectors.shape[1]} != index dim {self._dim}"
            )
        reg = pl.DataFrame(
            {
                _ROW_ID: pl.Series(row_ids, dtype=pl.Int64),
                _TEXT: texts,
                _VEC: pl.Series(
                    [v.tolist() for v in vectors],
                    dtype=pl.Array(pl.Float32, self._dim) if self._dim else pl.List(pl.Float32),
                ),
                _RECORD: records,
            }
        )
        self._con.register("_gm_reg", reg.to_arrow())
        self._con.execute(
            f"INSERT INTO {self.table} SELECT {_ROW_ID}, {_TEXT}, {_VEC}, {_RECORD} FROM _gm_reg"
        )
        self._con.unregister("_gm_reg")

    def build(self, df: pl.DataFrame, column: str | None = None, *, id_column: str | None = None) -> DuckDBVectorIndex:
        column = column or self.column
        if column is None:
            raise ValueError("DuckDBVectorIndex.build: a column to embed is required")
        self.column = column
        self.id_column = id_column if id_column is not None else self.id_column
        self._dim = None
        self._create_table_for(df, column, id_column)
        self._build_hnsw()
        self._write_manifest()
        return self

    def _create_table_for(self, df: pl.DataFrame, column: str, id_column: str | None) -> None:
        # Determine dim by embedding first (cache makes the later insert free).
        _, texts, _ = prep_rows(df, column, id_column, base=0)
        vectors = embed_texts(self._embedder, self.model, self._vec_cache, texts)
        self._dim = int(vectors.shape[1]) if vectors.size else None
        if self._dim is None:
            self._create_table(1)  # empty corpus: a placeholder dim
            return
        self._create_table(self._dim)
        self._insert(df, column, id_column)

    def add(self, df: pl.DataFrame, column: str | None = None, *, id_column: str | None = None) -> DuckDBVectorIndex:
        if not self._table_exists() or self.size == 0:
            return self.build(df, column, id_column=id_column)
        column = column or self.column
        if column is None:
            raise ValueError("DuckDBVectorIndex.add: a column to embed is required")
        self._insert(df, column, id_column)
        self._build_hnsw()
        self._write_manifest()
        return self

    # -- query --
    def query(self, query: str, *, k: int = 20, threshold: float = 0.0, filters: dict[str, Any] | None = None) -> list[RetrievedRecord]:
        if not query or not self._table_exists() or self.size == 0 or self._dim is None:
            return []
        q_vec = embed_texts(self._embedder, self.model, self._vec_cache, [str(query)])[0]
        where = ""
        params: list[Any] = [q_vec.tolist()]
        if filters:
            clauses = []
            for col, val in filters.items():
                clauses.append(f"json_extract_string({_RECORD}, '$.{col}') = ?")
                params.append(str(val))
            where = "WHERE " + " AND ".join(clauses)
        sql = (
            f"SELECT {_ROW_ID}, {_RECORD}, "
            f"array_cosine_similarity({_VEC}, ?::FLOAT[{self._dim}]) AS score "
            f"FROM {self.table} {where} ORDER BY score DESC LIMIT {int(k)}"
        )
        # the query vector param is first; filter params follow in WHERE order.
        rows = self._con.execute(sql, params).fetchall()
        out: list[RetrievedRecord] = []
        for row_id, record_json, score in rows:
            if score is None or float(score) < threshold:
                continue
            out.append(
                RetrievedRecord(
                    row_id=int(row_id),
                    score=float(score),
                    record=_record_from_json(record_json),
                )
            )
        return out

    # -- persistence --
    def _write_manifest(self) -> None:
        self._con.execute(f"CREATE TABLE IF NOT EXISTS {self._MANIFEST} (key VARCHAR, value VARCHAR)")
        self._con.execute(f"DELETE FROM {self._MANIFEST}")
        man = {
            "version": str(self._VERSION),
            "model": self.model,
            "column": self.column or "",
            "id_column": self.id_column or "",
            "dim": str(self._dim) if self._dim is not None else "",
        }
        self._con.executemany(
            f"INSERT INTO {self._MANIFEST} VALUES (?, ?)", list(man.items())
        )

    def save(self) -> DuckDBVectorIndex:
        """Flush to the DuckDB file (no-op for an in-memory store)."""
        if self.path != ":memory:":
            try:
                self._con.execute("CHECKPOINT")
            except Exception:  # pragma: no cover - checkpoint edge cases
                pass
        return self

    def close(self) -> None:
        try:
            self._con.close()
        except Exception:  # pragma: no cover
            pass

    @classmethod
    def load(cls, path: str, *, embedder: Any = None, table: str = "gm_vectors") -> DuckDBVectorIndex:
        import os

        if path != ":memory:" and not os.path.exists(path):
            raise FileNotFoundError(f"DuckDBVectorIndex.load: no database at {path}")
        idx = cls(path, table=table, embedder=embedder)
        if not idx._table_exists():
            raise FileNotFoundError(f"DuckDBVectorIndex.load: table {table!r} not in {path}")
        return idx

    @classmethod
    def open(cls, path: str, *, model: str = "inhouse", column: str | None = None, embedder: Any = None, table: str = "gm_vectors") -> DuckDBVectorIndex:
        return cls(path, table=table, model=model, column=column, embedder=embedder)


# ── pgvector (Postgres) backend ──────────────────────────────────────────────


class PgVectorIndex:
    """Persistent vector index backed by Postgres + pgvector (#1088).

    Stores one row per record; ranks with pgvector's cosine-distance operator
    ``<=>`` over an HNSW index. The database *is* the persistence: ``save`` is a
    no-op and another process ``open``s the same DSN.

    Requires ``psycopg`` + ``pgvector`` and the ``vector`` extension in the
    target database (``pip install goldenmatch[pgvector]``;
    ``CREATE EXTENSION vector``).
    """

    def __init__(
        self,
        dsn: str,
        *,
        table: str = "gm_vectors",
        model: str = "inhouse",
        column: str | None = None,
        id_column: str | None = None,
        embedder: Any = None,
    ):
        try:
            import psycopg  # noqa: F401
            from pgvector.psycopg import register_vector  # noqa: F401
        except ImportError as exc:  # pragma: no cover - import guard
            raise ImportError(
                "PgVectorIndex needs psycopg + pgvector: pip install goldenmatch[pgvector]"
            ) from exc
        self.dsn = dsn
        self.table = table
        self.model = model
        self.column = column
        self.id_column = id_column
        self._embedder = embedder if embedder is not None else get_embedder(model)
        self._dim: int | None = None
        self._vec_cache: dict[str, np.ndarray] = {}
        self._conn = self._connect()
        if self._table_exists():
            self._adopt_existing()

    def _connect(self):
        import psycopg
        from pgvector.psycopg import register_vector

        conn = psycopg.connect(self.dsn, autocommit=True)
        conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(conn)
        return conn

    def _table_exists(self) -> bool:
        row = self._conn.execute(
            "SELECT to_regclass(%s)", [self.table]
        ).fetchone()
        return bool(row and row[0] is not None)

    def _adopt_existing(self) -> None:
        row = self._conn.execute(
            "SELECT a.atttypmod FROM pg_attribute a "
            "JOIN pg_class c ON a.attrelid = c.oid "
            "WHERE c.relname = %s AND a.attname = %s",
            [self.table, _VEC],
        ).fetchone()
        if row and row[0] and int(row[0]) > 0:
            self._dim = int(row[0])
        for text, vec in self._conn.execute(
            f"SELECT {_TEXT}, {_VEC} FROM {self.table}"
        ).fetchall():
            self._vec_cache.setdefault(str(text), np.asarray(vec, dtype=np.float32))

    @property
    def size(self) -> int:
        if not self._table_exists():
            return 0
        return int(self._conn.execute(f"SELECT count(*) FROM {self.table}").fetchone()[0])

    @property
    def dim(self) -> int | None:
        return self._dim

    def __len__(self) -> int:
        return self.size

    def __repr__(self) -> str:
        return f"PgVectorIndex(table={self.table!r}, column={self.column!r}, size={self.size}, dim={self._dim})"

    def _create_table(self, dim: int) -> None:
        self._conn.execute(f"DROP TABLE IF EXISTS {self.table}")
        self._conn.execute(
            f"CREATE TABLE {self.table} ("
            f"{_ROW_ID} BIGINT, {_TEXT} TEXT, {_VEC} vector({dim}), {_RECORD} JSONB)"
        )
        self._conn.execute(
            f"CREATE INDEX {self.table}_hnsw ON {self.table} "
            f"USING hnsw ({_VEC} vector_cosine_ops)"
        )

    def _insert(self, df: pl.DataFrame, column: str, id_column: str | None) -> None:
        row_ids, texts, records = prep_rows(df, column, id_column, base=self.size)
        vectors = embed_texts(self._embedder, self.model, self._vec_cache, texts)
        if self._dim is None:
            self._dim = int(vectors.shape[1]) if vectors.size else None
        with self._conn.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {self.table} ({_ROW_ID}, {_TEXT}, {_VEC}, {_RECORD}) "
                f"VALUES (%s, %s, %s, %s)",
                [
                    (rid, txt, np.asarray(vec, dtype=np.float32), rec)
                    for rid, txt, vec, rec in zip(row_ids, texts, vectors, records)
                ],
            )

    def build(self, df: pl.DataFrame, column: str | None = None, *, id_column: str | None = None) -> PgVectorIndex:
        column = column or self.column
        if column is None:
            raise ValueError("PgVectorIndex.build: a column to embed is required")
        self.column = column
        self.id_column = id_column if id_column is not None else self.id_column
        _, texts, _ = prep_rows(df, column, id_column, base=0)
        vectors = embed_texts(self._embedder, self.model, self._vec_cache, texts)
        self._dim = int(vectors.shape[1]) if vectors.size else None
        if self._dim is None:
            return self
        self._create_table(self._dim)
        self._insert(df, column, id_column)
        return self

    def add(self, df: pl.DataFrame, column: str | None = None, *, id_column: str | None = None) -> PgVectorIndex:
        if not self._table_exists() or self.size == 0:
            return self.build(df, column, id_column=id_column)
        column = column or self.column
        if column is None:
            raise ValueError("PgVectorIndex.add: a column to embed is required")
        self._insert(df, column, id_column)
        return self

    def query(self, query: str, *, k: int = 20, threshold: float = 0.0, filters: dict[str, Any] | None = None) -> list[RetrievedRecord]:
        if not query or not self._table_exists() or self.size == 0 or self._dim is None:
            return []
        q_vec = embed_texts(self._embedder, self.model, self._vec_cache, [str(query)])[0]
        where = ""
        params: list[Any] = [q_vec, q_vec]
        if filters:
            clauses = []
            for col, val in filters.items():
                clauses.append(f"{_RECORD}->>%s = %s")
                params.extend([col, str(val)])
            where = "WHERE " + " AND ".join(clauses)
        # 1 - cosine_distance = cosine_similarity, matching the local backend's score.
        sql = (
            f"SELECT {_ROW_ID}, {_RECORD}, 1 - ({_VEC} <=> %s) AS score "
            f"FROM {self.table} {where} ORDER BY {_VEC} <=> %s LIMIT {int(k)}"
        )
        rows = self._conn.execute(sql, params).fetchall()
        out: list[RetrievedRecord] = []
        for row_id, record_json, score in rows:
            if score is None or float(score) < threshold:
                continue
            out.append(
                RetrievedRecord(
                    row_id=int(row_id),
                    score=float(score),
                    record=_record_from_json(record_json),
                )
            )
        return out

    def save(self) -> PgVectorIndex:
        """No-op: Postgres persists on insert."""
        return self

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # pragma: no cover
            pass

    @classmethod
    def load(cls, dsn: str, *, embedder: Any = None, table: str = "gm_vectors") -> PgVectorIndex:
        idx = cls(dsn, table=table, embedder=embedder)
        if not idx._table_exists():
            raise FileNotFoundError(f"PgVectorIndex.load: table {table!r} not found")
        return idx

    @classmethod
    def open(cls, dsn: str, *, model: str = "inhouse", column: str | None = None, embedder: Any = None, table: str = "gm_vectors") -> PgVectorIndex:
        return cls(dsn, table=table, model=model, column=column, embedder=embedder)


# ── factory ──────────────────────────────────────────────────────────────────

_PG_PREFIXES = ("postgresql://", "postgres://", "postgresql+", "host=", "dbname=")


def infer_backend(location: str) -> str:
    """Infer the backend kind from a ``location`` string.

    - a Postgres DSN (``postgresql://...`` or libpq ``key=value``) -> ``pgvector``
    - a ``.duckdb`` / ``.ddb`` file or ``duckdb:`` prefix          -> ``duckdb``
    - anything else (a directory path)                            -> ``local``
    """
    loc = location.strip()
    low = loc.lower()
    if low.startswith(_PG_PREFIXES):
        return "pgvector"
    if low.startswith("duckdb:") or low.endswith((".duckdb", ".ddb")):
        return "duckdb"
    return "local"


def open_vector_index(
    location: str,
    *,
    backend: str = "auto",
    model: str = "inhouse",
    column: str | None = None,
    id_column: str | None = None,
    embedder: Any = None,
    table: str = "gm_vectors",
) -> VectorStore:
    """Open (load-or-create) a vector index on the chosen backend.

    ``backend`` is ``"auto"`` (infer from ``location`` via :func:`infer_backend`),
    ``"local"``, ``"duckdb"``, or ``"pgvector"``. All four expose the same
    ``build`` / ``add`` / ``query`` / ``save`` surface and return
    ``RetrievedRecord`` from ``query`` -- so call sites are backend-agnostic.
    """
    kind = infer_backend(location) if backend == "auto" else backend
    if kind == "local":
        from goldenmatch.core.vector_index import VectorIndex

        return VectorIndex.open(location, model=model, column=column, embedder=embedder)
    if kind == "duckdb":
        loc = location[len("duckdb:"):] if location.lower().startswith("duckdb:") else location
        return DuckDBVectorIndex.open(
            loc, model=model, column=column, embedder=embedder, table=table
        )
    if kind == "pgvector":
        return PgVectorIndex.open(
            location, model=model, column=column, embedder=embedder, table=table
        )
    raise ValueError(f"open_vector_index: unknown backend {kind!r} (use local/duckdb/pgvector/auto)")
