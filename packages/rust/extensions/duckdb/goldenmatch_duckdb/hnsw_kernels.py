"""DuckDB UDF for native HNSW ANN blocking over an embedding column.

Native-direct + columnar, mirroring ``core_kernels.py``: the caller aggregates
a table's embedding column with ``list(embedding)`` (each element a
``DOUBLE[]``) and gets back the candidate near-duplicate pairs — the SQL
analogue of ``goldenmatch.core.ann_blocker.ANNBlocker.query_with_scores`` and
the same inner-product ranking as the Rust / Python / TS surfaces.

The index is built by the ``goldenhnsw`` kernel via the optional
``goldenmatch-hnsw`` wheel when present; otherwise a pure-numpy all-pairs
inner-product fallback runs (byte-identical neighbor SET at the scales a single
UDF call handles), so the UDF works with zero extra dependencies. Same
"native kernel else reference" posture as the graph kernels.

Exposed in SQL:
- ``goldenmatch_hnsw_pairs(vectors DOUBLE[][], k BIGINT, threshold DOUBLE)``
  -> ``STRUCT(a BIGINT, b BIGINT, s DOUBLE)[]`` -- canonical (a<b) candidate
  pairs, each carrying the max inner-product score >= ``threshold``. Row ids are
  0-based positions in the aggregated ``vectors`` list.

Registered via ``register_hnsw_functions(con)`` from ``functions.register``.
"""
from __future__ import annotations

import importlib.util

import duckdb
import numpy as np

_HAS_HNSW = importlib.util.find_spec("goldenmatch_hnsw") is not None


def _search_topk(x: np.ndarray, k: int) -> list[list[tuple[int, float]]]:
    """Top-``k`` neighbors per row as ``(idx, inner_product)``, descending.

    Uses the native goldenhnsw wheel when available (sub-linear), else a numpy
    all-pairs fallback (exact). Both rank by raw inner product.
    """
    n = x.shape[0]
    k = min(k, n)
    if k <= 0:
        return [[] for _ in range(n)]
    if _HAS_HNSW:
        from goldenmatch_hnsw import HnswIndex

        buf = np.ascontiguousarray(x, dtype=np.float32)
        idx = HnswIndex(dim=int(x.shape[1]), ef_search=max(64, k))
        idx.add_batch(buf.tobytes(), n=n)
        return idx.search_batch(buf.tobytes(), n, k)
    # numpy fallback: exact top-k by raw inner product.
    ip = x @ x.T
    out: list[list[tuple[int, float]]] = []
    for i in range(n):
        order = np.argpartition(-ip[i], k - 1)[:k]
        order = order[np.argsort(-ip[i][order])]
        out.append([(int(j), float(ip[i, j])) for j in order])
    return out


def _hnsw_pairs(vectors, k, threshold):
    """Build the candidate-pair set for an aggregated column of vectors."""
    if not vectors:
        return []
    # Drop null rows defensively; DuckDB LISTs can carry NULLs.
    rows = [v for v in vectors if v is not None]
    if not rows:
        return []
    x = np.asarray(rows, dtype=np.float32)
    if x.ndim != 2 or x.shape[1] == 0:
        return []
    thr = float(threshold)
    neighbors = _search_topk(x, int(k))
    best: dict[tuple[int, int], float] = {}
    for i, row in enumerate(neighbors):
        for j, s in row:
            if j == i or j < 0:
                continue
            if s < thr:
                continue
            a, b = (i, j) if i < j else (j, i)
            prev = best.get((a, b))
            if prev is None or s > prev:
                best[(a, b)] = s
    return [{"a": a, "b": b, "s": s} for (a, b), s in best.items()]


def register_hnsw_functions(con: duckdb.DuckDBPyConnection) -> None:
    """Register the native HNSW ANN-blocking UDF."""
    con.create_function(
        "goldenmatch_hnsw_pairs",
        _hnsw_pairs,
        ["DOUBLE[][]", "BIGINT", "DOUBLE"],
        "STRUCT(a BIGINT, b BIGINT, s DOUBLE)[]",
    )
