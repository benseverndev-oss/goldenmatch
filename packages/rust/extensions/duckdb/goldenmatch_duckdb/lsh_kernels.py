"""DuckDB UDF for MinHash-LSH token blocking over a text column.

The sparse-token counterpart to ``hnsw_kernels.py`` (dense-vector ANN blocking):
the caller aggregates a table's text column with ``list(text)`` and gets back the
near-duplicate candidate pairs — the SQL analogue of the Python
``MinHashLSHBlocker.candidate_pairs`` and the TS ``MinHashLSHBlocker``, all three
running the SAME ``sketch`` kernel (shingle -> MinHash signature -> banded LSH
buckets) so the candidate set is identical across surfaces.

This reuses ``goldenmatch.core.lsh_blocker.MinHashLSHBlocker`` directly rather
than reimplementing the bucketing — that blocker is native-gated (it calls
``goldenmatch.core.sketch``, which uses the compiled sketch kernel when
``goldenmatch[native]`` is installed, else the pure-Python reference). So there
is no separate optional wheel here (unlike HNSW): ``goldenmatch`` itself carries
the kernel.

Exposed in SQL:
- ``goldenmatch_lsh_pairs(texts VARCHAR[], mode VARCHAR, k BIGINT,
  num_perms BIGINT, num_bands BIGINT, seed BIGINT)``
  -> ``STRUCT(a BIGINT, b BIGINT)[]`` -- canonical (a<b) candidate pairs. Row
  ids are 0-based positions in the aggregated ``texts`` list. Empty /
  whitespace-only rows block on nothing (they never enter a pair).

Registered via ``register_lsh_functions(con)`` from ``functions.register``.
"""
from __future__ import annotations

import duckdb


def _lsh_pairs(texts, mode, k, num_perms, num_bands, seed):
    """Candidate near-duplicate pairs for an aggregated column of texts."""
    if not texts:
        return []
    # DuckDB LISTs can carry NULLs; an empty record blocks on nothing (the
    # blocker's empty-sentinel drops it), so None -> "" preserves row positions.
    rows = ["" if t is None else str(t) for t in texts]

    from goldenmatch.core.lsh_blocker import MinHashLSHBlocker

    blocker = MinHashLSHBlocker(
        str(mode), int(k), int(num_perms), int(num_bands), int(seed)
    )
    pairs = blocker.candidate_pairs(rows)
    # Deterministic order; canonical (a<b) already guaranteed by the blocker.
    return [{"a": a, "b": b} for (a, b) in sorted(pairs)]


def register_lsh_functions(con: duckdb.DuckDBPyConnection) -> None:
    """Register the MinHash-LSH token-blocking UDF."""
    con.create_function(
        "goldenmatch_lsh_pairs",
        _lsh_pairs,
        ["VARCHAR[]", "VARCHAR", "BIGINT", "BIGINT", "BIGINT", "BIGINT"],
        "STRUCT(a BIGINT, b BIGINT)[]",
    )
