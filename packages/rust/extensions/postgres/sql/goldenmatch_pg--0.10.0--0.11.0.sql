/* goldenmatch_pg 0.10.0 -> 0.11.0

   Adds goldenmatch_lsh_pairs: native-direct (no CPython) MinHash-LSH token
   blocking over a text[] corpus — the sparse-token counterpart to the 0.10.0
   goldenmatch_hnsw_pairs (dense-vector ANN). Shingle + MinHash + band each
   record via the pyo3-free sketch-core kernel, group rows sharing a
   (band, bucket), and return the canonical (a<b) candidate pairs (0-based row
   ids). Empty / whitespace-only rows and NULL elements block on nothing. Same
   kernel + candidate set as the Python MinHashLSHBlocker, the TS wasm blocker,
   and the DuckDB goldenmatch_lsh_pairs UDF. */

CREATE FUNCTION "goldenmatch_lsh_pairs"(
    "texts" TEXT[],
    "mode" TEXT,
    "k" INT,
    "num_perms" INT,
    "num_bands" INT,
    "seed" BIGINT
) RETURNS TABLE ("a" BIGINT, "b" BIGINT)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_lsh_pairs_wrapper';
