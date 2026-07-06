"""DuckDB UDFs for the GoldenCheck deep-profiling kernels.

These are **native-direct, columnar**: callers aggregate the whole column(s)
into DuckDB ``LIST`` arguments (``list(col)`` / ``array_agg(col)``) and get an
index / count structure back. Each UDF calls the shared, native-gated kernel in
``goldencheck.core.kernels`` -- the Rust ``goldencheck-core`` kernel when
``goldencheck[native]`` is installed, else the identical pure-Python fallback
(same values either way). This mirrors ``core_kernels.py`` (graph) and the
``hnsw_kernels`` / ``lsh_kernels`` / ``perceptual_kernels`` surfaces.

Exposed in SQL (all over ``goldencheck.core.kernels``):
- ``goldencheck_benford(values DOUBLE[]) -> BIGINT[]`` -- leading-digit (1..9)
  histogram; index ``d-1`` is the count of values whose first digit is ``d``.
- ``goldencheck_near_duplicates(values VARCHAR[], min_similarity DOUBLE)
  -> BIGINT[][]`` -- near-duplicate value clusters as row-index lists.
- ``goldencheck_discover_fds(columns VARCHAR[][])
  -> STRUCT(det BIGINT, dep BIGINT)[]`` -- strict functional dependencies as
  ``(determinant_col, dependent_col)`` index pairs.
- ``goldencheck_discover_approx_fds(columns VARCHAR[][], min_confidence DOUBLE)
  -> STRUCT(det BIGINT, dep BIGINT, violations BIGINT)[]`` -- near-strict FDs +
  their violation-row counts.
- ``goldencheck_composite_keys(columns VARCHAR[][], max_size BIGINT)
  -> BIGINT[][]`` -- minimal composite keys as column-index subsets.

Columns are passed as ``VARCHAR[][]`` (a list of columns, each a list of the
column's values in row order) -- build it with
``list(list(col_a), list(col_b), ...)`` or an ``array_agg`` per column. Bad
input fails the query (no fail-soft JSON envelope) since the columnar shape has
no slot for an error sentinel.

Registered via ``register_goldencheck_functions(con)`` from
``functions.register`` (fail-open if ``goldencheck`` is not installed).
"""
from __future__ import annotations

import duckdb


def register_goldencheck_functions(con: duckdb.DuckDBPyConnection) -> None:
    """Register the GoldenCheck deep-profiling UDFs.

    Fail-open: if ``goldencheck`` is not importable the functions are skipped
    (the DuckDB package does not hard-depend on goldencheck), matching how
    ``goldenflow.py`` guards an optional dependency.
    """
    try:
        import goldencheck.core.kernels  # noqa: F401
    except Exception:  # noqa: BLE001 - goldencheck optional for this package
        return

    con.create_function(
        "goldencheck_benford", _benford,
        ["DOUBLE[]"], "BIGINT[]",
    )
    con.create_function(
        "goldencheck_near_duplicates", _near_duplicates,
        ["VARCHAR[]", "DOUBLE"], "BIGINT[][]",
    )
    con.create_function(
        "goldencheck_discover_fds", _discover_fds,
        ["VARCHAR[][]"], "STRUCT(det BIGINT, dep BIGINT)[]",
    )
    con.create_function(
        "goldencheck_discover_approx_fds", _discover_approx_fds,
        ["VARCHAR[][]", "DOUBLE"], "STRUCT(det BIGINT, dep BIGINT, violations BIGINT)[]",
    )
    con.create_function(
        "goldencheck_composite_keys", _composite_keys,
        ["VARCHAR[][]", "BIGINT"], "BIGINT[][]",
    )


def _benford(values: list) -> list[int]:
    from goldencheck.core.kernels import benford_histogram

    return benford_histogram([float(v) for v in values if v is not None])


def _near_duplicates(values: list, min_similarity: float) -> list[list[int]]:
    from goldencheck.core.kernels import near_duplicate_clusters

    return near_duplicate_clusters([str(v) for v in values], float(min_similarity))


def _discover_fds(columns: list) -> list[dict]:
    from goldencheck.core.kernels import discover_functional_dependencies

    pairs = discover_functional_dependencies([list(c) for c in columns])
    return [{"det": det, "dep": dep} for det, dep in pairs]


def _discover_approx_fds(columns: list, min_confidence: float) -> list[dict]:
    from goldencheck.core.kernels import discover_approximate_fds

    triples = discover_approximate_fds(
        [list(c) for c in columns], float(min_confidence)
    )
    return [{"det": det, "dep": dep, "violations": viol} for det, dep, viol in triples]


def _composite_keys(columns: list, max_size: int) -> list[list[int]]:
    from goldencheck.core.kernels import composite_key_search

    return composite_key_search([list(c) for c in columns], int(max_size))
