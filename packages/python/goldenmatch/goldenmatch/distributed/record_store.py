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

_TABLE_PREFIX = "prepared_"

# Single source in core._hashing (re-exported here for back-compat). The bucket
# scorer (backends.score_buckets) imports the SAME constant; changing it
# reshuffles every bucket assignment on BOTH surfaces. See spec §Decisions log.
from goldenmatch.core._hashing import BUCKET_HASH_SEED


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


def _is_arrow_table(obj: Any) -> bool:
    """True when ``obj`` is a PyArrow ``Table`` (arrow lane), False for a
    Polars ``DataFrame``. Duck-typed on ``column_names`` (pa.Table has it,
    pl.DataFrame exposes ``columns`` instead) so neither engine is imported
    eagerly -- mirrors the ``core._hashing`` idiom."""
    return hasattr(obj, "column_names")


def _coerce_backend(frame: Any, backend: str) -> Any:
    """Return ``frame`` (a ``core.frame`` Frame) on the requested backend.

    record_store is arrow-native: the only conversion it performs is
    polars->arrow (``.to_arrow()``, no Polars import), so a mixed
    (polars-``df`` + arrow-assignments, or vice-versa) call still joins on
    one lane. When ``backend="polars"`` the frame is returned as-is -- real
    callers pass ``df`` and ``block_assignments`` on the same lane, and the
    seam ``join_inner`` needs no coercion in that case."""
    from goldenmatch.core.frame import to_frame

    is_arrow = _is_arrow_table(frame.native)
    if backend == "arrow" and not is_arrow:
        return to_frame(frame.to_arrow())
    return frame


def _write_frame_parquet(frame: Any, path: Path) -> None:
    """Write a ``core.frame`` Frame to ``path`` as snappy Parquet, per lane.
    Polars frames use Polars' own writer (byte-identical to the pre-port
    ``native.write_parquet``); arrow frames use ``pyarrow.parquet`` so the
    arrow lane needs no Polars import."""
    native = frame.native
    if hasattr(native, "write_parquet"):  # polars DataFrame
        native.write_parquet(path, compression="snappy")
    else:  # pyarrow Table (arrow lane)
        import pyarrow.parquet as papq

        papq.write_table(native, str(path), compression="snappy")


def materialize_prepared_records(
    store: PreparedRecordStore,
    df: Any,
    *,
    signature: str,
) -> None:
    """Write ``df`` into the store under ``signature``.

    Arrow-native: ``df`` may be a PyArrow ``Table`` OR a Polars ``DataFrame``
    -- both reach DuckDB as Arrow via the ``core.frame`` seam
    (``to_frame(df).to_arrow()``), and record_store itself never imports
    Polars. Same DuckDB ``arrow_table`` view registration pattern as
    ``backends/score_duckdb.py`` (PR #235); existing entries at the same
    signature are replaced.
    """
    from goldenmatch.core.frame import to_frame

    table = _TABLE_PREFIX + _sanitize_signature(signature)
    con = store.connection
    arrow_table = to_frame(df).to_arrow()  # noqa: F841 -- DuckDB resolves by local name
    con.execute(f'DROP TABLE IF EXISTS "{table}"')
    con.execute(f'CREATE TABLE "{table}" AS SELECT * FROM arrow_table')


def load_prepared_records(
    store: PreparedRecordStore,
    *,
    signature: str,
) -> Any | None:
    """Read ``signature``'s entry back as a PyArrow ``Table``.

    Returns None when the signature isn't present in the store (cache
    miss; caller prepares + materializes). Arrow-native: the caller owns any
    conversion to its own frame representation (record_store imports no
    Polars).
    """
    table = _TABLE_PREFIX + _sanitize_signature(signature)
    con = store.connection
    exists = con.execute(
        "SELECT 1 FROM duckdb_tables() WHERE table_name = ?",
        [table],
    ).fetchone()
    if exists is None:
        return None
    result = con.execute(f'SELECT * FROM "{table}"')
    # Prefer the non-deprecated to_arrow_table(); fall back to .arrow() (a
    # RecordBatchReader on newer DuckDB -> read_all() gives the Table).
    to_arrow_table = getattr(result, "to_arrow_table", None)
    if to_arrow_table is not None:
        return to_arrow_table()
    arrow_obj = result.arrow()
    return arrow_obj.read_all() if hasattr(arrow_obj, "read_all") else arrow_obj


def materialize_bucketed_blocks(
    store: PreparedRecordStore,
    df: Any,
    *,
    block_assignments: dict[int, str] | Any,
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
    (the group-partition step skips empty groups).

    Arrow-native: ``df`` and ``block_assignments`` may be PyArrow ``Table``s
    OR Polars ``DataFrame``s; every intermediate stays on ``df``'s backend
    via the ``core.frame`` seam (record_store imports no Polars). Byte-
    identical bucket layout on a Polars input (the seam's polars
    ``with_bucket_column`` is this stage's old ``hash(seed) % n`` expr).

    Spec: docs/superpowers/specs/2026-05-17-...-v2-bucketed-storage-design.md
    §Components #1.
    """
    from goldenmatch.core.frame import frame_from_column_data, to_frame

    sig_hash = _sanitize_signature(signature)
    bucket_dir = store.path.parent / f"buckets_{sig_hash}"
    # W4e FIX (latent stale-file bug): the signature is CONFIG-ONLY, so a
    # persisted store re-materialized with different data (or a different
    # backend hash) would leave stale bucket=K parquets that iter_buckets
    # happily returns. Materialize is an authoritative rebuild: clear the
    # dir first.
    if bucket_dir.exists():
        import shutil

        shutil.rmtree(bucket_dir)
    bucket_dir.mkdir(parents=True, exist_ok=True)

    # Build/keep every intermediate on the SAME backend as ``df`` so the seam
    # join never mixes lanes and the arrow lane never imports Polars.
    df_backend = "arrow" if _is_arrow_table(df) else "polars"

    if isinstance(block_assignments, dict):
        if not block_assignments:
            return bucket_dir
        assign_frame = frame_from_column_data(
            {
                "__row_id__": list(block_assignments.keys()),
                "__block_key__": list(block_assignments.values()),
            },
            backend=df_backend,
        )
    else:
        assign_frame = to_frame(block_assignments)
        if assign_frame.height == 0:
            return bucket_dir
        required = {"__row_id__", "__block_key__"}
        if not required.issubset(set(assign_frame.columns)):
            raise ValueError(
                f"block_assignments DataFrame must have columns "
                f"{required}; got {set(assign_frame.columns)}"
            )
        assign_frame = _coerce_backend(assign_frame, df_backend)

    # Inner join attaches __block_key__. Rows in `df` without an
    # assignment drop out (matches v1: unassigned rows weren't scored).
    keyed = to_frame(df).join_inner(assign_frame, on="__row_id__")

    # Bucket assignment via the canonical `core.frame` seam -- the SAME
    # `with_bucket_column(seed=BUCKET_HASH_SEED)` op the bucket scorer
    # (`backends.score_buckets`) uses, so a block's bucket is consistent
    # across the two surfaces by construction (test_cross_surface_consistency
    # pins the shared seed). Polars lane is byte-identical to this stage's old
    # `hash(seed) % n` expr; the arrow lane gets the seam's dictionary-code
    # twin. Bucket layout is shard-internal (never output-visible; the
    # clear-on-materialize above makes cross-run reuse of a differently-hashed
    # layout impossible).
    bucketed = keyed.with_bucket_column(
        "__block_key__", "__bucket__", n_buckets, BUCKET_HASH_SEED,
    )

    # W4e: group_partitions == partition_by(as_dict) with unwrapped keys.
    for bucket_id, bucket_frame in bucketed.group_partitions("__bucket__"):
        bucket_path = bucket_dir / f"bucket={int(bucket_id)}" / "data.parquet"
        bucket_path.parent.mkdir(parents=True, exist_ok=True)
        _write_frame_parquet(bucket_frame.drop(["__bucket__"]), bucket_path)

    return bucket_dir


def load_bucket(bucket_path: Path) -> Any:
    """Read a bucket Parquet file back as a PyArrow ``Table``.

    Arrow-native (via ``pyarrow.parquet``): record_store imports no Polars.
    The Ray worker consumer (``backends.ray_backend``) partitions the returned
    table through the ``core.frame`` seam.

    Trivial wrapper, lifted to a function so future enhancements
    (streaming, column projection) have one site to change.
    """
    import pyarrow.parquet as papq

    # Read ONLY this file. A dataset-style read (pq.read_table) would infer a
    # Hive partition from the enclosing ``bucket=<id>`` directory and inject a
    # spurious ``bucket`` column that the prior ``pl.read_parquet`` never
    # produced -- ParquetFile.read() reads just the file's own schema.
    return papq.ParquetFile(str(bucket_path)).read()


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
