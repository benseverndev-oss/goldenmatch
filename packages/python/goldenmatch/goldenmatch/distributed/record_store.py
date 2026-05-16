"""DuckDB-backed prepared-record store.

Spec: docs/superpowers/specs/2026-05-15-distributed-plan-v1-design.md
§Component 1.

The controller's iteration loop (and downstream distributed workers) need
to read the post-transform / post-auto-fix DataFrame multiple times. The
in-memory ``_PREP_CACHE`` in ``core/pipeline.py`` covers small-N within
one process; this store covers large-N (doesn't fit in RAM) and the
distributed case (workers in separate processes / machines need shared
access).

Lifecycle:
- ``PreparedRecordStore()`` (no args) -> ephemeral tempfile, cleaned on close.
- ``PreparedRecordStore(base_dir=...)`` -> tempfile inside that dir.
- ``PreparedRecordStore(path=...)`` -> open an existing store; useful for
  cross-call persistence.
- ``cleanup=False`` keeps the file after close (for persistence).

The store is keyed by ``signature`` (typically the
``_prep_cache_signature(config)`` produced by ``core/pipeline.py``).
Multiple distinct signatures coexist in the same store; lookups are
exact-match.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

_TABLE_PREFIX = "prepared_"


def _sanitize_signature(signature: str) -> str:
    """Map any signature string to a valid DuckDB table-name suffix.

    DuckDB table names must be ``[A-Za-z_][A-Za-z0-9_]*``. We hash the
    signature so the table-name length is bounded and the input character
    set doesn't matter.
    """
    import hashlib

    h = hashlib.sha256(signature.encode("utf-8")).hexdigest()
    return h[:16]


class PreparedRecordStore:
    """Owns one DuckDB connection backing a partitioned record store.

    Usage:

    .. code-block:: python

        with PreparedRecordStore() as store:
            materialize_prepared_records(store, df, signature="sig-v1")
            loaded = load_prepared_records(store, signature="sig-v1")
    """

    def __init__(
        self,
        *,
        base_dir: Path | str | None = None,
        path: Path | str | None = None,
        cleanup: bool = True,
    ) -> None:
        if path is not None:
            self.path = Path(path)
            self._owns_file = False  # caller manages lifecycle
        else:
            base = Path(base_dir) if base_dir is not None else None
            fd, p = tempfile.mkstemp(
                suffix=".duckdb", prefix="goldenmatch_prepared_", dir=base,
            )
            os.close(fd)
            # DuckDB rejects a pre-existing empty file (it's not a valid
            # DuckDB database). Remove the placeholder so duckdb.connect()
            # creates a fresh database at that path.
            os.unlink(p)
            self.path = Path(p)
            self._owns_file = True
        self._cleanup = cleanup
        self._con: duckdb.DuckDBPyConnection | None = duckdb.connect(str(self.path))
        self._closed = False

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            raise RuntimeError("PreparedRecordStore is closed")
        return self._con

    def close(self) -> None:
        """Idempotent close. Removes the file when cleanup=True regardless
        of whether the store created the file (tempfile) or opened an
        existing path. Set cleanup=False to preserve the file across calls
        (cross-call / cross-process persistence)."""
        if self._closed:
            return
        self._closed = True
        if self._con is not None:
            self._con.close()
            self._con = None
        if self._cleanup and self.path.exists():
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass  # Windows: file may still be locked; best-effort cleanup

    def __enter__(self) -> PreparedRecordStore:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


def materialize_prepared_records(
    store: PreparedRecordStore,
    df: pl.DataFrame,
    *,
    signature: str,
) -> None:
    """Write ``df`` into the store under ``signature``.

    Polars -> Arrow -> DuckDB via ``arrow_table`` view registration. Same
    pattern as ``backends/score_duckdb.py`` (PR #235). Existing entries
    at the same signature are replaced.
    """
    table = _TABLE_PREFIX + _sanitize_signature(signature)
    con = store.connection
    arrow_table = df.to_arrow()  # noqa: F841  -- DuckDB resolves by local name
    con.execute(f'DROP TABLE IF EXISTS "{table}"')
    con.execute(f'CREATE TABLE "{table}" AS SELECT * FROM arrow_table')


def load_prepared_records(
    store: PreparedRecordStore,
    *,
    signature: str,
) -> pl.DataFrame | None:
    """Read ``signature``'s entry back as a Polars DataFrame.

    Returns None when the signature isn't present in the store (cache
    miss; caller prepares + materializes).
    """
    table = _TABLE_PREFIX + _sanitize_signature(signature)
    con = store.connection
    exists = con.execute(
        "SELECT 1 FROM duckdb_tables() WHERE table_name = ?",
        [table],
    ).fetchone()
    if exists is None:
        return None
    arrow_table = con.execute(f'SELECT * FROM "{table}"').arrow()
    return pl.from_arrow(arrow_table)
