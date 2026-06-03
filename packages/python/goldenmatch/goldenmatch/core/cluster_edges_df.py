"""Scale-mode cluster edge view backed by an embedded DataFusion (Apache Arrow)
query engine. Replaces the per-cluster ``dict[int, dict[(a, b), float]]`` edge
materialization with a cid-sorted Arrow ``RecordBatchStream`` plus a single
rollup table, so the 100M-pair edge set never resides as a Python dict-of-dicts.

This is sub-project 1 of "scale mode". It deliberately trades the legacy view's
BYTE-identical guarantees for a SEMANTIC parity contract (see below) that is
cheap to express in SQL and streams without a global dict.

THE PARITY CONTRACT (semantic, NOT bit-identical):
1. MAX dedup of duplicate canonical ``(a, b)`` pairs (NOT last-wins). Keys are
   kept AS-GIVEN -- ``(a, b)`` is never canonicalized, so ``(7, 3)`` and
   ``(3, 7)`` are distinct edges.
2. Membership: an edge survives iff BOTH endpoints map to the same cid; cross-cut
   edges (one endpoint in another cluster) are dropped.
3. ``size <= 1`` -> confidence 1.0. Connectivity uses cluster SIZE (member count
   from ``assignments``), independent of ``edge_count``.
4. singleton / edgeless clusters MUST survive the rollup -- it is a LEFT JOIN
   from the per-cluster size table, so a cluster with zero surviving edges still
   emits a row (edge_count=0, min/avg coalesced to 0.0).
5. bottleneck tie-break is lexicographic ``(a, b)`` ascending -- order-free and
   deterministic across partition counts.

DataFusion Python API (verified against datafusion >= 53, < 54):
  - ``SessionContext.from_arrow(table, name=...)`` ingests a pyarrow Table.
  - ``SessionConfig().with_target_partitions(n)`` sets the partition count.
  - ``RuntimeEnvBuilder()`` is top-level; spilling is configured with
    ``.with_fair_spill_pool(size_bytes)`` + ``.with_disk_manager_os()``. There is
    NO ``with_memory_limit(...).build()`` and NO ``ctx.set_memory_limit``; the
    builder itself is passed as ``SessionContext(runtime=...)``.
  - ``ctx.sql(...)`` returns a DataFrame. ``.execute_stream()`` yields a
    ``RecordBatchStream`` whose iterated items are DataFusion ``RecordBatch``
    wrappers -- call ``.to_pyarrow()`` to get a ``pyarrow.RecordBatch``.
    ``.to_arrow_table()`` returns a ``pyarrow.Table``.
  - ``first_value(expr ORDER BY ...)`` is a supported ordered aggregate. A
    capability probe (``_supports_ordered_first_value``) gates a two-pass
    fallback for engines that lack it.
"""
from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pyarrow as pa


# ---------------------------------------------------------------------------
# Arrow ingest helpers
# ---------------------------------------------------------------------------
def _pairs_to_arrow(pairs: Iterable[tuple[int, int, float]]) -> pa.Table:
    """Materialize the RAW pair list into an Arrow table with explicit dtypes.
    ``a``/``b`` are int64, ``score`` is float64. Pairs are kept AS-GIVEN (no
    canonicalization)."""
    import pyarrow as pa

    a_col: list[int] = []
    b_col: list[int] = []
    s_col: list[float] = []
    for a, b, s in pairs:
        a_col.append(int(a))
        b_col.append(int(b))
        s_col.append(float(s))
    return pa.table(
        {
            "a": pa.array(a_col, pa.int64()),
            "b": pa.array(b_col, pa.int64()),
            "score": pa.array(s_col, pa.float64()),
        }
    )


def _assign_to_arrow(assignments: Any) -> pa.Table:
    """Convert the ``assignments`` Polars frame ({member_id, cluster_id}) into an
    Arrow table with int64 columns. Accepts anything exposing ``to_arrow()`` (a
    Polars DataFrame) or an Arrow table directly."""
    import pyarrow as pa

    if hasattr(assignments, "to_arrow"):
        tbl = assignments.to_arrow()
    else:
        tbl = assignments
    member = tbl.column("member_id").cast(pa.int64())
    cluster = tbl.column("cluster_id").cast(pa.int64())
    return pa.table({"member_id": member, "cluster_id": cluster})


# ---------------------------------------------------------------------------
# Session construction
# ---------------------------------------------------------------------------
def _make_context(
    *, memory_limit: int | None, target_partitions: int | None
) -> Any:
    """Build a DataFusion ``SessionContext`` with optional partition count and
    memory-limited (spill-to-disk) runtime. The 0.8 fraction mirrors the legacy
    ``with_memory_limit(max, 0.8)`` intent -- DataFusion's builder takes a raw
    byte size, so we apply the fraction ourselves."""
    from datafusion import RuntimeEnvBuilder, SessionConfig, SessionContext

    cfg = SessionConfig()
    if target_partitions is not None:
        cfg = cfg.with_target_partitions(int(target_partitions))

    if memory_limit is not None:
        pool_bytes = max(1, int(memory_limit * 0.8))
        runtime = (
            RuntimeEnvBuilder()
            .with_disk_manager_os()
            .with_fair_spill_pool(pool_bytes)
        )
        return SessionContext(config=cfg, runtime=runtime)
    return SessionContext(config=cfg)


def _supports_ordered_first_value(ctx: Any) -> bool:
    """Probe whether this DataFusion build supports the ``first_value(expr ORDER
    BY ...)`` ordered aggregate. datafusion >= 53 does; the probe future-proofs
    against a pinned engine that doesn't, wiring the two-pass fallback instead of
    leaving it for impl-discovery."""
    try:
        ctx.sql(
            "SELECT first_value(v ORDER BY v ASC) AS f "
            "FROM (SELECT 1 AS v UNION ALL SELECT 2 AS v)"
        ).to_arrow_table()
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Edge stream (PRIMARY) + run collection
# ---------------------------------------------------------------------------
def _edges_sql() -> str:
    """SQL for the canonical surviving-edge set, registered as the ``edges`` view
    that BOTH the stream and the rollup read. Dedup pairs by MAX(score) AS-GIVEN
    (no canonicalization), join both endpoints to assignments, keep only edges
    whose endpoints share a cid (cross-cut edges dropped)."""
    return """
        WITH deduped AS (
            SELECT a, b, max(score) AS score
            FROM pairs
            GROUP BY a, b
        )
        SELECT aa.cluster_id AS cid, d.a AS a, d.b AS b, d.score AS score
        FROM deduped d
        JOIN assignments aa ON d.a = aa.member_id
        JOIN assignments bb ON d.b = bb.member_id
        WHERE aa.cluster_id IS NOT NULL
          AND bb.cluster_id IS NOT NULL
          AND aa.cluster_id = bb.cluster_id
    """


def _edge_stream_sql() -> str:
    """SQL for the cid-sorted edge stream: the ``edges`` view ORDER BY cid so a
    consumer can collect contiguous per-cid runs."""
    return "SELECT cid, a, b, score FROM edges ORDER BY cid"


def _collect_runs(stream: Any) -> dict[int, dict[tuple[int, int], float]]:
    """Consume a cid-ORDERED ``RecordBatchStream`` into
    ``{cid: {(a, b): score}}``. Same-cid rows are contiguous because the SQL
    sorts by cid; we accumulate per-cid dicts as batches arrive so the global
    dict-of-dicts is the only resident structure (the streaming win is realized
    by the caller draining + discarding per-cid runs).

    Edges are deduped to MAX upstream (the GROUP BY), so a ``(a, b)`` appears at
    most once; assignment here is unconditional."""
    out: dict[int, dict[tuple[int, int], float]] = {}
    for raw_batch in stream:
        batch = raw_batch.to_pyarrow() if hasattr(raw_batch, "to_pyarrow") else raw_batch
        cids = batch.column("cid").to_pylist()
        a_vals = batch.column("a").to_pylist()
        b_vals = batch.column("b").to_pylist()
        s_vals = batch.column("score").to_pylist()
        for cid, a, b, s in zip(cids, a_vals, b_vals, s_vals):
            out.setdefault(int(cid), {})[(int(a), int(b))] = float(s)
    return out


# ---------------------------------------------------------------------------
# Rollup (SECONDARY)
# ---------------------------------------------------------------------------
def _rollup_sql(*, ordered_first_value: bool) -> str:
    """SQL for the per-cluster rollup. ``size`` comes from a GROUP BY over
    ``assignments`` (so singletons survive), LEFT JOINed to the per-cid edge
    aggregate. min/avg/edge_count are coalesced for edgeless clusters.

    bottleneck = the edge with the smallest score, ties broken lexicographically
    by ``(a, b)`` ascending -- expressed as ``first_value(... ORDER BY score ASC,
    a ASC, b ASC)`` when the ordered aggregate is available."""
    if ordered_first_value:
        agg = """
            SELECT
                cid,
                min(score) AS min_edge,
                avg(score) AS avg_edge,
                count(*)   AS edge_count,
                first_value(a ORDER BY score ASC, a ASC, b ASC) AS bottleneck_a,
                first_value(b ORDER BY score ASC, a ASC, b ASC) AS bottleneck_b
            FROM edges
            GROUP BY cid
        """
    else:
        # Two-pass fallback: per-cid min(score), then the lexicographically
        # smallest (a, b) among the rows whose score equals that min.
        agg = """
            WITH per_cid AS (
                SELECT
                    cid,
                    min(score) AS min_edge,
                    avg(score) AS avg_edge,
                    count(*)   AS edge_count
                FROM edges
                GROUP BY cid
            ),
            bottleneck AS (
                SELECT e.cid AS cid, min(e.a) AS bottleneck_a,
                       min(e.b) AS bottleneck_b
                FROM edges e
                JOIN per_cid p ON e.cid = p.cid AND e.score = p.min_edge
                -- restrict b to rows at the minimal a so (a, b) is lexicographic
                WHERE e.a = (
                    SELECT min(e2.a) FROM edges e2
                    JOIN per_cid p2 ON e2.cid = p2.cid AND e2.score = p2.min_edge
                    WHERE e2.cid = e.cid
                )
                GROUP BY e.cid
            )
            SELECT p.cid, p.min_edge, p.avg_edge, p.edge_count,
                   b.bottleneck_a, b.bottleneck_b
            FROM per_cid p
            LEFT JOIN bottleneck b ON p.cid = b.cid
        """
    return f"""
        WITH size_t AS (
            SELECT cluster_id, count(*) AS size
            FROM assignments
            GROUP BY cluster_id
        ),
        agg AS ({agg})
        SELECT
            s.cluster_id AS cid,
            s.size AS size,
            coalesce(a.edge_count, 0) AS edge_count,
            coalesce(a.min_edge, 0.0) AS min_edge,
            coalesce(a.avg_edge, 0.0) AS avg_edge,
            a.bottleneck_a AS bottleneck_a,
            a.bottleneck_b AS bottleneck_b
        FROM size_t s
        LEFT JOIN agg a ON s.cluster_id = a.cid
    """


def _confidence(rollup_row: dict[str, Any]) -> float:
    """Weighted confidence per the parity contract. ``size <= 1`` -> 1.0; else
    ``0.4*min + 0.3*avg + 0.3*conn`` where ``conn = edge_count / (size*(size-1)/2)``.

    Matches ``goldenmatch.core.cluster.compute_cluster_confidence`` for the
    confidence value itself (the weak-cluster *0.7 downgrade is a downstream
    concern, not part of this raw rollup)."""
    size = int(rollup_row["size"])
    if size <= 1:
        return 1.0
    edge_count = int(rollup_row["edge_count"])
    if edge_count == 0:
        # size > 1 but no surviving edges: connectivity 0, min/avg 0 -> 0.0.
        return 0.0
    min_edge = float(rollup_row["min_edge"])
    avg_edge = float(rollup_row["avg_edge"])
    max_possible = size * (size - 1) / 2
    conn = edge_count / max_possible if max_possible > 0 else 0.0
    return 0.4 * min_edge + 0.3 * avg_edge + 0.3 * conn


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def cluster_edges_datafusion(
    pairs: Iterable[tuple[int, int, float]],
    assignments: Any,
    *,
    memory_limit: int | None = None,
    target_partitions: int | None = None,
) -> tuple[Any, pa.Table]:
    """Build the scale-mode cluster edge view via embedded DataFusion.

    Returns ``(edges_stream, rollup_table)``:
      - ``edges_stream`` is a cid-ORDERED DataFusion ``RecordBatchStream`` of
        ``(cid, a, b, score)``; feed it to ``_collect_runs`` to materialize
        per-cid edge dicts (deduped MAX, membership-filtered).
      - ``rollup_table`` is a pyarrow ``Table`` with one row PER CLUSTER
        (singletons included via LEFT JOIN), columns: ``cid, size, edge_count,
        min_edge, avg_edge, bottleneck_a, bottleneck_b``. Derive confidence with
        ``_confidence`` per row.

    ``pairs`` are the RAW input pairs AS-GIVEN (never canonicalized).
    ``assignments`` is the FINAL membership frame ({member_id, cluster_id}, one
    row per member, singletons included).
    """
    ctx = _make_context(
        memory_limit=memory_limit, target_partitions=target_partitions
    )
    ctx.from_arrow(_pairs_to_arrow(pairs), name="pairs")
    ctx.from_arrow(_assign_to_arrow(assignments), name="assignments")

    # Register the membership-filtered, MAX-deduped edge set as a named view so
    # the rollup aggregates over the SAME edges the stream emits.
    ctx.register_view("edges", ctx.sql(_edges_sql()))

    ordered = _supports_ordered_first_value(ctx)
    rollup_table = ctx.sql(
        _rollup_sql(ordered_first_value=ordered)
    ).to_arrow_table()

    edges_stream = ctx.sql(_edge_stream_sql()).execute_stream()
    return edges_stream, rollup_table
