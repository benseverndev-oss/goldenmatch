-- Upgrade goldenmatch_pg 0.18.0 -> 0.19.0
--
-- Adds goldenmatch_train_em_from_counts(): FS training with no embedded CPython.

-- 0.19.0: Fellegi-Sunter training from COUNTED comparison vectors, native-direct.
--
-- goldenmatch_train_em(rows, matchkey, params) trains through the embedded
-- CPython bridge because it samples pairs and bins them into comparison
-- vectors, and neither of those is ported. This function starts one step later
-- -- from vectors the engine has already counted -- and so needs no Python at
-- all: it calls the pyo3-free score-core::em_core kernel directly, the same
-- shape as goldenmatch_hnsw_pairs / goldenmatch_lsh_pairs.
--
-- That split is the design, not a shortcut. Counting agreement patterns is
-- GROUP BY over the gamma columns, which is what a SQL engine is for, and the
-- number of distinct vectors is bounded by prod(levels + 1) -- thousands of
-- rows however many pairs were compared. So Postgres can now do both halves of
-- FS training with no interpreter in the backend.
--
-- Flat arrays because pgrx flattens multidimensional ones (the
-- goldenmatch_hnsw_pairs(flat_vecs, dim) idiom): `patterns` is row-major
-- n_patterns x n_fields with -1 meaning unobserved, and `u_probs` is RAGGED --
-- field j contributes n_levels[j] entries -- so it is flattened in field order
-- and split back by n_levels.
--
-- Fail-soft to a {"error": ...} envelope, like goldenmatch_certify_structural:
-- a training call inside a larger query must not abort the transaction.
--
--   SELECT goldenmatch.goldenmatch_train_em_from_counts(
--       ARRAY[2,2], ARRAY[1,1, 0,1, 1,0, 0,0],
--       ARRAY[500,300,150,50]::float8[],
--       ARRAY[0.9,0.1, 0.85,0.15]::float8[],
--       ARRAY[false,false]);
CREATE FUNCTION "goldenmatch_train_em_from_counts"(
    "n_levels" INT[],
    "patterns" INT[],
    "counts" DOUBLE PRECISION[],
    "u_probs" DOUBLE PRECISION[],
    "conditioned" BOOL[],
    "max_iterations" INT DEFAULT 20,
    "convergence" DOUBLE PRECISION DEFAULT 0.001
) RETURNS TEXT
IMMUTABLE PARALLEL SAFE
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_train_em_from_counts_wrapper';
