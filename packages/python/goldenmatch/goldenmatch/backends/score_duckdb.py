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

        for block in blocks:
            pairs = _score_one_block(
                block, mk, frozen_exclude,
                across_files_only=across_files_only,
                source_lookup=source_lookup,
            )
            if target_ids is not None:
                pairs = [
                    (a, b, s)
                    for a, b, s in pairs
                    if (a in target_ids) != (b in target_ids)
                ]
            if not pairs:
                continue
            # Canonicalize and insert. Using executemany is the lightest
            # path — bulk INSERT VALUES on 10K rows is roughly the same
            # cost as a Python list.append, and DuckDB handles spilling.
            con.executemany(
                "INSERT INTO __gm_pairs__ VALUES (?, ?, ?)",
                [(min(a, b), max(a, b), float(s)) for a, b, s in pairs],
            )
            for a, b, _s in pairs:
                matched_pairs.add((min(a, b), max(a, b)))

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
