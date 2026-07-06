/* goldenmatch_pg 0.12.0 -> 0.13.0

   Adds the GoldenCheck deep-profiling surface, native-direct (no CPython) over
   the pyo3-free goldencheck-core kernels — the SQL counterpart to the
   edge/Python GoldenCheck profiling kernels, completing the GoldenCheck row of
   the cross-surface parity roadmap (P5). Same values the goldencheck[native]
   wheel and the DuckDB goldencheck_* UDFs produce.

   The multi-column functions take a column-major flat text[] (all of column 0's
   rows, then column 1's, …, each column the same length) plus an n_cols count —
   the flat-array-plus-dimension idiom pgrx uses for goldenmatch_hnsw_pairs,
   because pgrx flattens multidim arrays.

   - goldencheck_benford(values double precision[]) -> bigint[]: leading-digit
     (1..9) histogram; element d-1 is the count of values whose first digit is d.
   - goldencheck_near_duplicates(values text[], min_similarity float8)
     -> TABLE(cluster bigint, member bigint): near-duplicate value clusters as
     (cluster, member-row-index) rows; singletons omitted.
   - goldencheck_discover_fds(flat text[], n_cols int)
     -> TABLE(det bigint, dep bigint): strict functional dependencies as
     (determinant, dependent) column-index pairs.
   - goldencheck_discover_approx_fds(flat text[], n_cols int, min_confidence float8)
     -> TABLE(det bigint, dep bigint, violations bigint): near-strict FDs + their
     violation-row counts.
   - goldencheck_composite_keys(flat text[], n_cols int, max_size int)
     -> TABLE(key_id bigint, col_index bigint): minimal composite keys as
     column-index subsets. */

CREATE FUNCTION "goldencheck_benford"(
    "values" double precision[]
) RETURNS BIGINT[]
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldencheck_benford_wrapper';

CREATE FUNCTION "goldencheck_near_duplicates"(
    "values" TEXT[],
    "min_similarity" DOUBLE PRECISION
) RETURNS TABLE ("cluster" BIGINT, "member" BIGINT)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldencheck_near_duplicates_wrapper';

CREATE FUNCTION "goldencheck_discover_fds"(
    "flat" TEXT[],
    "n_cols" INT
) RETURNS TABLE ("det" BIGINT, "dep" BIGINT)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldencheck_discover_fds_wrapper';

CREATE FUNCTION "goldencheck_discover_approx_fds"(
    "flat" TEXT[],
    "n_cols" INT,
    "min_confidence" DOUBLE PRECISION
) RETURNS TABLE ("det" BIGINT, "dep" BIGINT, "violations" BIGINT)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldencheck_discover_approx_fds_wrapper';

CREATE FUNCTION "goldencheck_composite_keys"(
    "flat" TEXT[],
    "n_cols" INT,
    "max_size" INT
) RETURNS TABLE ("key_id" BIGINT, "col_index" BIGINT)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldencheck_composite_keys_wrapper';
