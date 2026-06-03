"""DataFusion spine — Stage C orchestration (mode="scale").

Stage C threads ``score -> dedup`` through ONE Python DataFusion
``SessionContext`` (with OS disk-manager + fair-spill so the score
self-join and the in-context GROUP-BY dedup ride spill at scale), then
MIRRORS the in-memory frames-out pipeline for the Ray-free
``clustering -> id_prep -> golden`` tail.

Architecture (only score + dedup is new DataFusion; the tail is the
existing in-memory frames-out path verbatim)::

    [DataFusion ctx]
      score(scorer UDF over a block self-join) -> RAW scored pairs
      dedup(in-ctx GROUP BY a, b -> max(score))   -> deduped scored set
    RAW pairs
      -> build_cluster_frames(RAW all_pairs, all_ids)   -> ClusterFrames
      -> ClusterPairScores.from_frames(assignments, RAW pairs)   [id_prep]
      -> build_golden_records_from_frames(source, frames, rules)  [golden]

Scorer registration prefers the Stage-B Rust-crate FFI ScalarUDFs
(``goldenmatch_datafusion_udf``: ``JaroWinklerUDF`` / ``TokenSortUDF`` /
``LevenshteinUDF``, all returning [0, 1], registered under the SQL names
``jaro_winkler`` / ``token_sort`` / ``levenshtein``). When that wheel is
absent the spine falls back to the Stage-B1 vectorized Python UDF from
``datafusion_backend._make_score_udf`` (registered under
``_gm_score_<scorer>``) so the spine still runs without the compiled
crate -- the fallback is stated in a log line.

This is opt-in via ``mode="scale"``; nothing here flips a default.
Semantic parity (vs the in-memory pipeline's frames-out path) is the
gate: Rand-1.0 partition + golden content + id_prep edge sets.
"""
from __future__ import annotations

import logging
from typing import Any

import pyarrow as pa

from goldenmatch.backends.datafusion_backend import (
    _ensure_datafusion,
    _ensure_native,
    _make_score_udf,
    _materialize_blocks_to_arrow,
    _validate_matchkey,
)

logger = logging.getLogger(__name__)

# SQL function names registered by the Stage-B FFI ScalarUDFs (see
# packages/rust/extensions/datafusion-udf/src/scalar_udf.rs). All three
# return [0, 1]; token_sort is the score_one(2)/unit form there, matching
# the B1 native token_sort_ratio/100 fallback.
_FFI_SQL_NAME = {
    "jaro_winkler": "jaro_winkler",
    "levenshtein": "levenshtein",
    "token_sort": "token_sort",
}


def _make_spine_ctx(memory_limit: int | None, target_partitions: int | None):
    """Build the ONE spine SessionContext.

    ``SessionConfig`` (optionally pinned to ``target_partitions``) plus a
    ``RuntimeEnvBuilder`` that wires the OS disk manager and a fair-spill
    pool sized to ``memory_limit`` so the score self-join and the in-ctx
    GROUP-BY dedup spill to disk rather than OOM at scale. The v53
    builders return the runtime object directly (no ``.build()``).
    """
    datafusion_mod = _ensure_datafusion()
    from datafusion import RuntimeEnvBuilder, SessionConfig, SessionContext

    cfg = SessionConfig()
    if target_partitions is not None:
        cfg = cfg.with_target_partitions(int(target_partitions))

    if memory_limit is not None:
        runtime = (
            RuntimeEnvBuilder()
            .with_disk_manager_os()
            .with_fair_spill_pool(int(memory_limit))
        )
        ctx = SessionContext(config=cfg, runtime=runtime)
    else:
        ctx = SessionContext(config=cfg)
    return datafusion_mod, ctx


def _register_scorers(ctx, scorer_name: str, datafusion_mod):
    """Register the scorer UDF on ``ctx`` and return its SQL call name.

    Prefers the Stage-B FFI ScalarUDF crate; falls back to the Stage-B1
    vectorized Python UDF when the crate is not importable. The fallback
    is stated in a log line so a bench run can't silently measure the
    slow path.
    """
    from datafusion import udf

    try:
        import goldenmatch_datafusion_udf as ffi

        ffi_cls = {
            "jaro_winkler": ffi.JaroWinklerUDF,
            "levenshtein": ffi.LevenshteinUDF,
            "token_sort": ffi.TokenSortUDF,
        }[scorer_name]
        ctx.register_udf(udf(ffi_cls()))
        logger.info(
            "DataFusion spine: registered FFI ScalarUDF for scorer=%s "
            "(SQL name %r)",
            scorer_name, _FFI_SQL_NAME[scorer_name],
        )
        return _FFI_SQL_NAME[scorer_name]
    except ImportError:
        native_mod = _ensure_native()
        b1_udf = _make_score_udf(scorer_name, datafusion_mod, native_mod)
        ctx.register_udf(b1_udf)
        sql_name = f"_gm_score_{scorer_name}"
        logger.info(
            "DataFusion spine: FFI crate goldenmatch_datafusion_udf "
            "unavailable -- FALLING BACK to the Stage-B1 vectorized "
            "Python UDF for scorer=%s (SQL name %r)",
            scorer_name, sql_name,
        )
        return sql_name


def _golden_rules_knobs(config) -> tuple[int, float, bool, Any]:
    """Derive ``(max_cluster_size, weak_cluster_threshold, auto_split,
    golden_rules)`` exactly as ``_run_dedupe_pipeline`` does (pipeline.py
    :1451-1461 + :1570)."""
    from goldenmatch.config.schemas import GoldenRulesConfig

    golden_rules = config.golden_rules or GoldenRulesConfig(
        default_strategy="most_complete"
    )
    max_cluster_size = 100
    weak_threshold = 0.3
    auto_split = True
    if config.golden_rules is not None:
        gr = config.golden_rules
        if hasattr(gr, "max_cluster_size"):
            max_cluster_size = gr.max_cluster_size
        if hasattr(gr, "weak_cluster_threshold"):
            weak_threshold = gr.weak_cluster_threshold
        if hasattr(gr, "auto_split"):
            auto_split = gr.auto_split
    return max_cluster_size, weak_threshold, auto_split, golden_rules


def _resolve_single_weighted_matchkey(config) -> Any:
    """Pick the single weighted matchkey the spine scores. The spine
    scope mirrors ``score_blocks_datafusion`` (single-field weighted,
    supported scorer); ``_validate_matchkey`` enforces the field shape
    downstream, so here we only locate the weighted matchkey."""
    matchkeys = config.get_matchkeys()
    weighted = [mk for mk in matchkeys if mk.type == "weighted"]
    if not weighted:
        raise NotImplementedError(
            "DataFusion spine (mode='scale') requires exactly one weighted "
            "matchkey; config has none."
        )
    if len(weighted) > 1:
        raise NotImplementedError(
            "DataFusion spine (mode='scale') supports a single weighted "
            f"matchkey; config has {len(weighted)}."
        )
    return weighted[0]


def _score_and_dedup(
    ctx,
    sql_name: str,
    threshold: float,
) -> tuple[list[tuple[int, int, float]], list[tuple[int, int, float]]]:
    """Run the block self-join score + the in-ctx GROUP-BY dedup on ``ctx``.

    Returns ``(raw_pairs, deduped_pairs)``:
      - ``raw_pairs``: every above-threshold ``(a, b, score)`` with
        ``a < b`` (canonical), KEPT for clustering + id_prep (the RAW
        all_pairs the in-memory path feeds to build_cluster_frames).
      - ``deduped_pairs``: ``SELECT a, b, max(score) GROUP BY a, b`` over
        the raw set -- rides the ctx spill -- exposed as the
        ``scored_pairs`` result field only (NOT used for clustering).

    Both stay inside the ONE ctx; only the final collect() crosses to
    Python.
    """
    # Self-join on (block_key, row_id_a < row_id_b); the scorer UDF runs
    # once per surviving pair in the nested SELECT, the outer SELECT
    # threshold-filters. Mirrors score_blocks_datafusion's join shape;
    # the table is named "blocks" (registered by run_spine).
    score_sql = f"""
        SELECT id_a, id_b, score FROM (
            SELECT
                a.__row_id__ AS id_a,
                b.__row_id__ AS id_b,
                {sql_name}(a.__value__, b.__value__) AS score
            FROM blocks a
            JOIN blocks b
              ON a.__block_key__ = b.__block_key__
             AND a.__row_id__ < b.__row_id__
        ) scored
        WHERE score >= {threshold}
    """
    raw_batches = ctx.sql(score_sql).collect()
    raw_pairs: list[tuple[int, int, float]] = []
    for batch in raw_batches:
        id_a = batch.column(0).to_pylist()
        id_b = batch.column(1).to_pylist()
        sc = batch.column(2).to_pylist()
        for a, b, s in zip(id_a, id_b, sc, strict=True):
            a_i = int(a)
            b_i = int(b)
            # row_id_a < row_id_b already holds from the join predicate, so
            # (a_i, b_i) is canonical; keep as-is.
            raw_pairs.append((a_i, b_i, float(s)))

    # In-ctx max-score dedup -- registered as its own table so the GROUP BY
    # plans/spills on the same ctx rather than in Python. Only the scored
    # result field reads this; clustering uses raw_pairs.
    deduped_pairs: list[tuple[int, int, float]] = []
    if raw_pairs:
        pairs_tbl = pa.table({
            "a": pa.array([p[0] for p in raw_pairs], type=pa.int64()),
            "b": pa.array([p[1] for p in raw_pairs], type=pa.int64()),
            "score": pa.array([p[2] for p in raw_pairs], type=pa.float64()),
        })
        ctx.from_arrow(pairs_tbl, name="raw_pairs")
        dedup_batches = ctx.sql(
            "SELECT a, b, max(score) AS score FROM raw_pairs GROUP BY a, b"
        ).collect()
        for batch in dedup_batches:
            da = batch.column(0).to_pylist()
            db = batch.column(1).to_pylist()
            ds = batch.column(2).to_pylist()
            for a, b, s in zip(da, db, ds, strict=True):
                deduped_pairs.append((int(a), int(b), float(s)))

    return raw_pairs, deduped_pairs


def run_spine(
    blocked_candidates: list,
    config,
    *,
    memory_limit: int | None = None,
    target_partitions: int | None = None,
):
    """DataFusion-spine entry point (mode="scale").

    Threads ``score -> dedup`` through one spilling DataFusion
    ``SessionContext`` then mirrors the in-memory frames-out path for
    ``clustering -> id_prep -> golden`` (Ray-free).

    Args:
        blocked_candidates: ``list[BlockResult]`` (as ``build_blocks``
            emits). Flattened to ONE Arrow block table via
            ``_materialize_blocks_to_arrow`` and registered as ``blocks``.
        config: a ``GoldenMatchConfig`` carrying a single weighted
            matchkey (single-field, supported scorer) and the golden rules.
        memory_limit: bytes for the fair-spill pool; ``None`` skips the
            spill runtime (no memory cap).
        target_partitions: pins ``SessionConfig.with_target_partitions``.

    Returns:
        ``(golden_df, assignments_df)`` where ``assignments_df`` is
        ``cluster_frames.assignments`` (one row per ``(cluster_id,
        member_id)``, singletons included).
    """
    from goldenmatch.core.cluster import build_cluster_frames
    from goldenmatch.core.cluster_pairscores import ClusterPairScores
    from goldenmatch.core.golden import build_golden_records_from_frames

    # ── C1: ctx + score + dedup ───────────────────────────────────────
    mk = _resolve_single_weighted_matchkey(config)
    field_name, scorer_name, threshold = _validate_matchkey(mk)

    datafusion_mod, ctx = _make_spine_ctx(memory_limit, target_partitions)
    sql_name = _register_scorers(ctx, scorer_name, datafusion_mod)

    block_table = _materialize_blocks_to_arrow(
        blocked_candidates,
        field_name,
        across_files_only=False,
        source_lookup=None,
    )
    if block_table is None:
        # No block survived the height>=2 filter -> no pairs. Build empty
        # frames over the (possibly empty) id universe and return.
        all_pairs: list[tuple[int, int, float]] = []
        all_ids_arr = _all_ids_from_blocks(blocked_candidates)
    else:
        ctx.from_arrow(block_table, name="blocks")
        all_pairs, _deduped = _score_and_dedup(ctx, sql_name, threshold)
        all_ids_arr = _all_ids_from_blocks(blocked_candidates)

    # ── C2: clustering via build_cluster_frames (no Ray) ──────────────
    max_cluster_size, weak_threshold, auto_split, golden_rules = (
        _golden_rules_knobs(config)
    )
    cluster_frames = build_cluster_frames(
        all_pairs,
        all_ids_arr,
        max_cluster_size=max_cluster_size,
        weak_cluster_threshold=weak_threshold,
        auto_split=auto_split,
    )

    # ── C3: id_prep (from_frames over RAW pairs) + golden ─────────────
    # pair_score_view mirrors pipeline.py:1906 -- built from the FINAL
    # assignments frame + RAW all_pairs (NOT the deduped set). This is the
    # id_prep edge view the identity stage consumes; the spine materializes
    # it here so id_prep is a real executed stage on the validated path
    # (the parity test re-derives the same view from the returned
    # assignments + raw pairs and diffs per-cluster edge sets).
    pair_score_view = ClusterPairScores.from_frames(
        cluster_frames.assignments, all_pairs
    )
    logger.info(
        "DataFusion spine: id_prep built ClusterPairScores view over "
        "%d cluster(s)",
        cluster_frames.metadata.height,
    )
    # Keep a binding so the view is not GC'd before golden (it shares the
    # raw-pairs buffers); referenced for clarity, not consumed downstream.
    del pair_score_view

    _golden_source = _slim_golden_source(blocked_candidates)
    golden_df, _golden_records = build_golden_records_from_frames(
        _golden_source,
        cluster_frames,
        golden_rules,
        quality_scores=None,
        provenance=config.output.lineage_provenance,
    )

    return golden_df, cluster_frames.assignments


def _all_ids_from_blocks(blocked_candidates: list):
    """Build the ``all_ids`` argument for ``build_cluster_frames`` as a
    Polars int64 Series (an Arrow-array-like, NOT a Python ``list[int]``
    rehydration) over the UNION of ``__row_id__`` across every block.

    Mirrors ``collected_df["__row_id__"]`` in the in-memory path: every
    record id in the universe, so singletons surface as size-1 clusters.
    Deduped (a row can land in multiple blocks under multi-pass blocking).
    """
    import polars as pl

    series_parts: list[pl.Series] = []
    for block in blocked_candidates:
        df = block.df
        if isinstance(df, pl.LazyFrame):
            df = df.collect()
        series_parts.append(df["__row_id__"].cast(pl.Int64))
    if not series_parts:
        return pl.Series("__row_id__", [], dtype=pl.Int64)
    return pl.concat(series_parts).unique(maintain_order=True)


def _slim_golden_source(blocked_candidates: list):
    """Reconstruct the golden source frame from the blocks, slimmed the
    way pipeline.py:1632-1640 slims ``collected_df`` before the from-frames
    join: drop the internal ``__xform_*__`` / ``__mk_*__`` / ``__block_key__``
    / ``__bucket__`` columns survivorship never reads, keep ``__row_id__``.

    The in-memory path slims ``collected_df`` directly; the spine only has
    the blocks, so it unions them (deduped on ``__row_id__``) to rebuild the
    source universe, then applies the same projection.
    """
    import os

    import polars as pl

    frames: list[pl.DataFrame] = []
    for block in blocked_candidates:
        df = block.df
        if isinstance(df, pl.LazyFrame):
            df = df.collect()
        frames.append(df)
    if not frames:
        return pl.DataFrame({"__row_id__": pl.Series([], dtype=pl.Int64)})

    source = pl.concat(frames, how="vertical_relaxed").unique(
        subset=["__row_id__"], keep="first", maintain_order=True
    )

    if os.environ.get("GOLDENMATCH_GOLDEN_SLIM_MULTIDF", "1") != "0":
        _internal_prefixes = ("__xform_", "__mk_", "__block_key__", "__bucket__")
        source = source.select([
            c for c in source.columns
            if not any(c.startswith(p) for p in _internal_prefixes)
        ])
    return source
