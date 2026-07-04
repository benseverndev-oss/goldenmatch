"""DuckDB UDFs for the perceptual (image pHash) surface.

The SQL counterpart to the edge/Python perceptual hash, running the SAME
``goldenmatch.core.perceptual`` kernel (native-gated: it calls the compiled
``perceptual_phash_image`` when ``goldenmatch[native]`` is installed, else the
pure-Python reference) — so the hash is identical across the Python, native,
DuckDB, and Postgres surfaces.

Exposed in SQL:
- ``goldenmatch_perceptual_phash(grid DOUBLE[], ncols BIGINT) -> BIGINT``
  -- 64-bit DCT perceptual image hash of a row-major flattened luma grid
  (``ncols`` = row width). The kernel resizes to 32x32 internally, so any
  rectangular grid works. The unsigned 64-bit hash is returned bit-reinterpreted
  as a signed ``BIGINT`` (DuckDB ``BIGINT`` is i64), matching the Postgres
  ``goldenmatch_perceptual_phash`` so a hash stored from either surface compares
  equal.
- ``goldenmatch_perceptual_hamming(a BIGINT, b BIGINT) -> INTEGER``
  -- Hamming distance between two 64-bit pHashes: the near-duplicate blocking
  predicate ``WHERE goldenmatch_perceptual_hamming(a.phash, b.phash) <= 10``.
  Operates on the raw bit patterns (masked to 64 bits), so it is correct on the
  signed ``BIGINT`` hashes ``goldenmatch_perceptual_phash`` returns.

Registered via ``register_perceptual_functions(con)`` from ``functions.register``.
"""
from __future__ import annotations

import duckdb

_U64 = 0xFFFFFFFFFFFFFFFF
_I64_SIGN = 1 << 63


def _to_signed_i64(h: int) -> int:
    """Reinterpret an unsigned 64-bit value as a signed i64 (for BIGINT)."""
    return h - (1 << 64) if h >= _I64_SIGN else h


def _perceptual_phash(grid, ncols):
    """64-bit pHash of a row-major flattened luma grid, as a signed BIGINT."""
    ncols = int(ncols)
    if ncols <= 0 or not grid or len(grid) % ncols != 0:
        raise ValueError("grid length must be a positive multiple of ncols")
    rows = [
        [float(grid[r * ncols + c]) for c in range(ncols)]
        for r in range(len(grid) // ncols)
    ]
    from goldenmatch.core.perceptual import phash_image

    return _to_signed_i64(phash_image(rows))


def _perceptual_hamming(a, b):
    """Hamming distance between two 64-bit pHashes (correct on signed BIGINTs)."""
    return ((int(a) ^ int(b)) & _U64).bit_count()


def register_perceptual_functions(con: duckdb.DuckDBPyConnection) -> None:
    """Register the perceptual pHash + hamming UDFs."""
    con.create_function(
        "goldenmatch_perceptual_phash",
        _perceptual_phash,
        ["DOUBLE[]", "BIGINT"],
        "BIGINT",
    )
    con.create_function(
        "goldenmatch_perceptual_hamming",
        _perceptual_hamming,
        ["BIGINT", "BIGINT"],
        "INTEGER",
    )
