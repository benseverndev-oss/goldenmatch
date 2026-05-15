"""DuckDB-backed block scoring — out-of-core pair storage.

Drop-in replacement for ``goldenmatch.core.scorer.score_blocks_parallel``
that writes scored pairs to a DuckDB table instead of accumulating
them in a Python ``list``. The same per-block rapidfuzz cdist
scoring path is reused for the actual fuzzy work; only the pair
storage and accumulator move out of Python memory.

Why it matters
--------------

The default in-memory scorer holds the full ``list[tuple[int, int,
float]]`` of every scored pair in Python until clustering. At 5M
records with a 12% dupe rate that's ~750K pairs — small. At 50M+
with adversarial blocking, pair count can hit 10⁸+ and the Python
``list`` becomes a real memory pressure source.

By routing pair accumulation through DuckDB (either ``:memory:``
or a disk path), the runtime can spill to disk when the pair table
grows past memory limits — same scoring throughput, bounded RAM.

What this is NOT
----------------

This isn't full SQL-native scoring. The actual rapidfuzz cdist work
still happens in Python per block — there's no UDF dispatch in the
inner loop, no `JOIN`-based candidate generation. Those are a v2
investment that requires the ``goldenmatch-duckdb`` extension to be
a hard dep + design pass on schema. This module delivers the
"intermediate state lives outside Python" half of the out-of-core
story; the "scoring runs in SQL" half is a follow-up.

Wiring
------

``goldenmatch.core.pipeline._get_block_scorer`` routes to this
module when ``config.backend == "duckdb"``. Set ``config.backend =
"duckdb"`` (or pass ``backend="duckdb"`` to ``dedupe_df``) to use it.
"""
from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

logger = logging.getLogger(__name__)


def score_blocks_duckdb(
    blocks: list,
    mk: Any,
    matched_pairs: set[tuple[int, int]],
    max_workers: int = 4,  # noqa: ARG001  # accepted for signature parity
    across_files_only: bool = False,
    source_lookup: dict[int, str] | None = None,
    target_ids: set[int] | None = None,
    *,
    db_path: str | None = None,
) -> list[tuple[int, int, float]]:
    """Score blocks, accumulating pairs in a DuckDB table.

    Signature mirrors ``score_blocks_parallel`` so it's a drop-in via
    ``_get_block_scorer``. ``db_path`` is the DuckDB store location;
    ``None`` (default) uses an in-memory connection. For real
    out-of-core, pass an on-disk path (e.g.
    ``GOLDENMATCH_DUCKDB_SCORE_DB`` env var or an explicit config
    field — TODO follow-up).

    Per-block work is delegated to the existing
    ``goldenmatch.core.scorer._score_one_block`` (rapidfuzz cdist),
    so scoring throughput matches the in-memory variant. The
    difference is where the accumulator lives.
    """
    # Lazy duckdb import so the rest of goldenmatch doesn't depend on it.
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover — import path
        raise ImportError(
            "DuckDB-backed scoring requires duckdb. "
            "Install with: pip install goldenmatch[duckdb]"
        ) from exc

    from goldenmatch.core.scorer import _score_one_block  # noqa: PLC0415

    if not blocks:
        return []

    resolved_db_path = db_path or os.environ.get(
        "GOLDENMATCH_DUCKDB_SCORE_DB"
    )
    tempfile_path: str | None = None
    if resolved_db_path is None:
        resolved_db_path = ":memory:"
    elif resolved_db_path == "auto":
        # Allocate a fresh tempfile path so the pair store is on disk
        # but cleaned up after this call. Useful for "spill to disk if
        # needed" runs without forcing the user to pick a path. We
        # *delete* the tempfile before passing the path to DuckDB —
        # NamedTemporaryFile creates an empty file, and DuckDB rejects
        # empty paths as "not a valid DuckDB database file".
        fd, tempfile_path = tempfile.mkstemp(
            prefix="goldenmatch_pairs_", suffix=".duckdb",
        )
        os.close(fd)
        os.unlink(tempfile_path)
        resolved_db_path = tempfile_path

    con = duckdb.connect(resolved_db_path)
    try:
        con.execute(
            "CREATE OR REPLACE TABLE __gm_pairs__ ("
            "id_a BIGINT, id_b BIGINT, score DOUBLE)"
        )

        # Snapshot exclude_pairs so per-block scoring sees a frozen copy
        # (mirrors score_blocks_parallel's behavior).
        frozen_exclude = frozenset(matched_pairs)

        # Parallelize block scoring with the same ThreadPoolExecutor
        # shape `score_blocks_parallel` uses. rapidfuzz.cdist releases
        # the GIL inside `_score_one_block`, so threads give real
        # parallelism. The earlier single-threaded version of this
        # function was the 5x wall-clock regression measured between
        # backend=None (24s on 100K) and backend="duckdb" (131s on
        # 100K). DuckDB INSERTs stay on the main thread — a single
        # DuckDB connection isn't thread-safe for concurrent writes,
        # and we want to keep the pair store as the single sequential
        # bottleneck instead of contending workers on it.
        from concurrent.futures import ThreadPoolExecutor, as_completed

        # For tiny block counts the threadpool overhead exceeds the
        # work — match score_blocks_parallel's threshold.
        if len(blocks) <= 2:
            result_pairs: list[tuple[int, int, float]] = []
            for block in blocks:
                pairs = _score_one_block(
                    block, mk, frozen_exclude,
                    across_files_only=across_files_only,
                    source_lookup=source_lookup,
                )
                if target_ids is not None:
                    pairs = [
                        (a, b, s) for a, b, s in pairs
                        if (a in target_ids) != (b in target_ids)
                    ]
                result_pairs.extend(pairs)
            if result_pairs:
                con.executemany(
                    "INSERT INTO __gm_pairs__ VALUES (?, ?, ?)",
                    [(min(a, b), max(a, b), float(s)) for a, b, s in result_pairs],
                )
                for a, b, _s in result_pairs:
                    matched_pairs.add((min(a, b), max(a, b)))
        else:
            workers = max_workers if max_workers > 0 else 4
            # Collect all pairs first, INSERT in bulk at the end. The
            # per-block INSERT path serialized workers behind the main
            # thread's DB writes (measured: 4x slowdown on 100K). The
            # pair list is bounded by the dedupe rate — at 100K with
            # 12% dupes it's ~10K rows, well under the 16 GB ceiling
            # this backend exists for. For TRUE out-of-core, the
            # streaming variant can re-enter once we have a
            # benchmarked-large case.
            all_block_pairs: list[tuple[int, int, float]] = []
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = [
                    executor.submit(
                        _score_one_block,
                        block, mk, frozen_exclude,
                        across_files_only, source_lookup,
                    )
                    for block in blocks
                ]
                for future in as_completed(futures):
                    pairs = future.result()
                    if target_ids is not None:
                        pairs = [
                            (a, b, s) for a, b, s in pairs
                            if (a in target_ids) != (b in target_ids)
                        ]
                    if not pairs:
                        continue
                    all_block_pairs.extend(pairs)
                    for a, b, _s in pairs:
                        matched_pairs.add((min(a, b), max(a, b)))
            # Bulk INSERT via Arrow.
            #
            # `con.executemany("INSERT ...", rows)` measured at 1ms/row
            # on the 100K audit — 90K rows = 104s, the entire
            # backend's 5x slowdown vs polars-direct. Bulk Arrow
            # insertion via `con.register(name, table)` + `INSERT ...
            # SELECT * FROM name` does 90K rows in milliseconds.
            if all_block_pairs:
                # Canonicalize pairs in a single list comprehension
                # before building the Arrow table. The canonical form
                # `(min, max, score)` is what the downstream cluster
                # builder expects.
                ids_a: list[int] = []
                ids_b: list[int] = []
                scores: list[float] = []
                for a, b, s in all_block_pairs:
                    if a <= b:
                        ids_a.append(int(a))
                        ids_b.append(int(b))
                    else:
                        ids_a.append(int(b))
                        ids_b.append(int(a))
                    scores.append(float(s))
                try:
                    import pyarrow as pa
                    # The local variable name MUST appear in the SQL —
                    # DuckDB resolves Python-locals as zero-copy view
                    # sources. `arrow_table` is the conventional name
                    # the DuckDB docs use; do not rename.
                    arrow_table = pa.table({  # noqa: F841
                        "id_a": ids_a,
                        "id_b": ids_b,
                        "score": scores,
                    })
                    con.execute(
                        "INSERT INTO __gm_pairs__ SELECT * FROM arrow_table"
                    )
                except ImportError:
                    # Fallback to executemany if pyarrow isn't
                    # available — the duckdb extra usually pulls it
                    # in via Polars, but be defensive.
                    con.executemany(
                        "INSERT INTO __gm_pairs__ VALUES (?, ?, ?)",
                        list(zip(ids_a, ids_b, scores)),
                    )

        # Pull pairs back out — clustering currently expects a Python
        # list, so we materialize at the boundary. A future iteration
        # can hand DuckDB cursors to the cluster builder directly.
        rows = con.execute(
            "SELECT id_a, id_b, score FROM __gm_pairs__"
        ).fetchall()
        result = [(int(a), int(b), float(s)) for a, b, s in rows]
        logger.info(
            "DuckDB scoring: %d blocks, %d pairs (store=%s)",
            len(blocks), len(result), resolved_db_path,
        )
        return result
    finally:
        con.close()
        if tempfile_path is not None:
            try:
                os.unlink(tempfile_path)
            except OSError:  # pragma: no cover
                pass
