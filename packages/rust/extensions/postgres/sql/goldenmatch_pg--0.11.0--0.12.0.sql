/* goldenmatch_pg 0.11.0 -> 0.12.0

   Adds the perceptual (image pHash) surface, native-direct (no CPython) over the
   pyo3-free perceptual-core kernel — the SQL counterpart to the edge/Python
   perceptual hash:

   - goldenmatch_perceptual_phash(grid double precision[], ncols int) -> bigint:
     64-bit DCT perceptual image hash of a row-major flattened luma grid (ncols =
     row width). The kernel resizes to 32x32 internally, so any rectangular grid
     works. The unsigned 64-bit hash is returned bit-reinterpreted as bigint
     (Postgres has no unsigned 64-bit). Same value the Python phash_image, the
     native kernel, and the DuckDB goldenmatch_perceptual_phash UDF produce.

   - goldenmatch_perceptual_hamming(a bigint, b bigint) -> int: Hamming distance
     between two 64-bit pHashes — the near-duplicate image blocking predicate
     (WHERE goldenmatch_perceptual_hamming(a.phash, b.phash) <= 10). Operates on
     the raw bit patterns, so it is correct on the bigint-reinterpreted hashes. */

CREATE FUNCTION "goldenmatch_perceptual_phash"(
    "grid" double precision[],
    "ncols" INT
) RETURNS BIGINT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_perceptual_phash_wrapper';

CREATE FUNCTION "goldenmatch_perceptual_hamming"(
    "a" BIGINT,
    "b" BIGINT
) RETURNS INT
STRICT
LANGUAGE c
AS 'MODULE_PATHNAME', 'goldenmatch_perceptual_hamming_wrapper';
