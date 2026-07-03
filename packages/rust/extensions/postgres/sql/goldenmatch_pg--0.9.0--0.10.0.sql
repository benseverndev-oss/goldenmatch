-- goldenmatch_pg 0.9.0 -> 0.10.0 upgrade.
--
-- Adds goldenmatch_hnsw_pairs: native-direct (no CPython) HNSW ANN blocking
-- over a row-major flat real[] corpus. Returns the canonical (a<b) candidate
-- pairs (0-based positions) whose inner product clears the threshold, keeping
-- the max score per pair -- the SQL analogue of ANNBlocker.query_with_scores,
-- same inner-product ranking as the goldenhnsw wheel / TS-wasm / DuckDB surfaces.
CREATE FUNCTION "goldenmatch_hnsw_pairs"(
    "flat_vecs" real[],
    "dim" INT,
    "k" INT,
    "threshold" DOUBLE PRECISION
) RETURNS TABLE ("a" BIGINT, "b" BIGINT, "s" DOUBLE PRECISION)
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_hnsw_pairs_wrapper';
