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
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

_TABLE_PREFIX = "prepared_"

BUCKET_HASH_SEED = 0xC2B5C0BBE7ED5E5D
"""Deterministic seed for Polars' xxHash-based bucket assignment.
Changing this value reshuffles every bucket assignment; treat as a
constant. See spec §Decisions log."""


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
        read_only: bool = False,
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
        self._con: duckdb.DuckDBPyConnection | None = duckdb.connect(
            str(self.path), read_only=read_only,
        )
        self._closed = False

    @property
    def connection(self) -> duckdb.DuckDBPyConnection:
        if self._con is None:
            raise RuntimeError("PreparedRecordStore is closed")
        return self._con

    def release_connection(self) -> None:
        """Close the DuckDB connection without marking the store as closed
        or deleting the file.

        Used by the key-mode Ray dispatch path on Windows: DuckDB acquires
        an exclusive write lock on the file even in the driver process.
        Worker processes that open the same file read-only are blocked by
        that lock. Calling release_connection() from the driver before
        dispatching Ray tasks lets workers open the file concurrently.

        The store object remains usable after this call for metadata access
        (self.path, self._cleanup). The DB connection itself is gone; any
        further call that needs self.connection will raise RuntimeError.
        """
        if self._con is not None:
            self._con.close()
            self._con = None

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
        # v2 bucket directory cleanup (NEW per spec §Error handling #7).
        # shutil.rmtree does NOT expand globs -- iterate explicitly via
        # Path.glob. ignore_errors=True absorbs benign Windows file-locking
        # races (mirrors v1's unlink(missing_ok=True) style).
        if self._cleanup and self._owns_file:
            for sibling in self.path.parent.glob("buckets_*"):
                if sibling.is_dir():
                    shutil.rmtree(sibling, ignore_errors=True)

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


def materialize_bucketed_blocks(
    store: PreparedRecordStore,
    df: pl.DataFrame,
    *,
    block_assignments: dict[int, str] | pl.DataFrame,
    n_buckets: int,
    signature: str,
) -> Path:
    """Write `df` partitioned into N hash buckets at
    `store.path.parent / buckets_<sig_hash>/bucket=K/data.parquet`.

    `block_assignments` accepts EITHER:
    * `dict[int, str]` mapping `__row_id__` -> `block_key` (convenient
      for tests; converted to a 2-col df internally).
    * `pl.DataFrame` with `__row_id__` (int) and `__block_key__` (str)
      columns. Production callers pass this form -- building a
      5M-entry Python dict is precisely the per-row Python loop v2
      exists to avoid.

    Empty assignments yield a bucket_dir with no Parquet files
    (Polars' partition_by skips empty groups).

    Spec: docs/superpowers/specs/2026-05-17-...-v2-bucketed-storage-design.md
    §Components #1.
    """
    sig_hash = _sanitize_signature(signature)
    bucket_dir = store.path.parent / f"buckets_{sig_hash}"
    bucket_dir.mkdir(parents=True, exist_ok=True)

    # Normalize to a Polars DataFrame.
    if isinstance(block_assignments, dict):
        if not block_assignments:
            return bucket_dir
        rid_to_block = pl.DataFrame({
            "__row_id__": list(block_assignments.keys()),
            "__block_key__": list(block_assignments.values()),
        })
    else:
        rid_to_block = block_assignments
        if rid_to_block.height == 0:
            return bucket_dir
        required = {"__row_id__", "__block_key__"}
        if not required.issubset(set(rid_to_block.columns)):
            raise ValueError(
                f"block_assignments DataFrame must have columns "
                f"{required}; got {set(rid_to_block.columns)}"
            )

    # Inner join attaches __block_key__. Rows in `df` without an
    # assignment drop out (matches v1: unassigned rows weren't scored).
    keyed = df.join(rid_to_block, on="__row_id__", how="inner")

    # Bucket assignment via Polars xxHash with fixed seed.
    with_bucket = keyed.with_columns(
        (pl.col("__block_key__").hash(seed=BUCKET_HASH_SEED) % n_buckets)
        .alias("__bucket__"),
    )

    for bucket_id, bucket_df in with_bucket.partition_by(
        "__bucket__", as_dict=True,
    ).items():
        # bucket_id may arrive as a tuple (Polars >= 1.0 with as_dict=True
        # uses tuple keys for partition cols). Unwrap.
        if isinstance(bucket_id, tuple):
            bucket_id = bucket_id[0]
        bucket_path = bucket_dir / f"bucket={int(bucket_id)}" / "data.parquet"
        bucket_path.parent.mkdir(parents=True, exist_ok=True)
        bucket_df.drop("__bucket__").write_parquet(
            bucket_path, compression="snappy",
        )

    return bucket_dir


def load_bucket(bucket_path: Path) -> pl.DataFrame:
    """Read a bucket Parquet file as a Polars DataFrame.

    Trivial wrapper, lifted to a function so future enhancements
    (streaming, column projection) have one site to change.
    """
    return pl.read_parquet(bucket_path)


def iter_buckets(bucket_dir: Path) -> Iterator[tuple[int, Path]]:
    """Yield (bucket_id, parquet_path) pairs for each bucket=K/data.parquet
    under `bucket_dir`. Sorted by bucket_id for determinism.

    Missing directory yields zero items (does NOT raise) -- spec
    §Components #3 missing-dir semantics. Workers receive these paths;
    the driver never reads bucket contents.
    """
    if not bucket_dir.exists():
        return
    pairs: list[tuple[int, Path]] = []
    for sub in bucket_dir.iterdir():
        if not sub.is_dir() or not sub.name.startswith("bucket="):
            continue
        try:
            bid = int(sub.name.split("=", 1)[1])
        except (IndexError, ValueError):
            continue
        path = sub / "data.parquet"
        if path.is_file():
            pairs.append((bid, path))
    pairs.sort(key=lambda p: p[0])
    for bid, path in pairs:
        yield bid, path
