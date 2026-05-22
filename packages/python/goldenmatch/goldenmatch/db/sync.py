"""Incremental sync orchestrator — matches new records against existing DB."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import polars as pl

from goldenmatch.config.schemas import GoldenMatchConfig
from goldenmatch.db.connector import DatabaseConnector
from goldenmatch.db.metadata import (
    config_hash,
    ensure_metadata_tables,
    get_state,
    log_matches_batch,
    new_run_id,
    update_state,
)
from goldenmatch.db.writer import write_golden_records

logger = logging.getLogger(__name__)


def run_sync(
    connector: DatabaseConnector,
    source_table: str,
    config: GoldenMatchConfig,
    output_mode: str = "separate",
    full_rescan: bool = False,
    dry_run: bool = False,
    chunk_size: int = 10000,
    incremental_column: str | None = None,
) -> dict:
    """Run incremental entity resolution against database.

    Args:
        connector: Active database connection
        source_table: Table to match against
        config: GoldenMatch matching configuration
        output_mode: "separate" or "in_place"
        full_rescan: Force reprocess all records
        dry_run: Match but don't write results
        chunk_size: Records per chunk for reading
        incremental_column: Column for incremental detection

    Returns:
        Summary dict with match counts, actions taken
    """
    from goldenmatch.core.autofix import auto_fix_dataframe

    # Ensure metadata tables exist
    ensure_metadata_tables(connector)

    run_id = new_run_id()
    cfg_hash = config_hash(config.model_dump())
    matchkeys = config.get_matchkeys()

    # Check last state
    state = get_state(connector, source_table)
    total_rows = connector.get_row_count(source_table)

    if state and not full_rescan:
        if state.get("config_hash") != cfg_hash:
            logger.info("Config changed since last run. Forcing full rescan.")
            full_rescan = True

    # Determine which records to process.
    #
    # Full-scan path keeps the input as a LazyFrame (pl.scan_parquet over
    # the on-disk staging dir) all the way through __source__/__row_id__
    # + matchkey computation. The first .collect() happens inside
    # _full_scan_pipeline AFTER matchkeys are merged in -- one
    # materialization instead of two, which keeps peak RSS bounded on
    # 1M+ row tables on 8 GB sandboxes (#384).
    if full_rescan or state is None:
        import shutil  # noqa: PLC0415
        logger.info("Full scan: reading %d records from %s", total_rows, source_table)
        new_records_lf, staging_dir = _read_all_lazy(
            connector, source_table, chunk_size,
        )
        if new_records_lf is None:
            logger.info("No new records to process.")
            return {"new_records": 0, "matches": 0, "actions": []}
        try:
            # Route to streaming-block path above a row-count floor.
            #
            # Default threshold = 500K rows. Reasoning: at 500K * 60 cols
            # * ~50 bytes/cell, the eager collect inside _full_scan_pipeline
            # is ~1.5 GB. dedupe_df then doubles+triples that for matchkey
            # columns, scoring matrices, candidate pair sets, and
            # auto-fix's intermediate frames -- another 3-5 GB. On an
            # 8 GB sandbox (#401 trace) the dispatch boundary OOMs at
            # 1.13M rows. 500K leaves a margin even on memory-constrained
            # hosts; smaller datasets prefer the legacy path's faster
            # per-row throughput.
            #
            # The prior 5M default (#386) was tuned for 16 GB+ hosts and
            # failed open on 8 GB sandboxes. See #401.
            #
            # Override via GOLDENMATCH_SYNC_STREAMING_THRESHOLD for
            # hosts where you'd rather take the perf hit / win.
            import os  # noqa: PLC0415
            streaming_threshold = int(
                os.environ.get("GOLDENMATCH_SYNC_STREAMING_THRESHOLD", "500000"),
            )
            if total_rows > streaming_threshold:
                logger.info(
                    "Sync backend selected: STREAMING-BLOCK "
                    "(rows=%d > threshold=%d). Per-block scan keeps RSS "
                    "bounded by largest block, not dataset size. "
                    "Set GOLDENMATCH_SYNC_STREAMING_THRESHOLD to override. "
                    "See #386, #401.",
                    total_rows, streaming_threshold,
                )
                return _full_scan_streaming(
                    connector, staging_dir, source_table, config, matchkeys,
                    output_mode, dry_run, run_id, cfg_hash, total_rows,
                )

            logger.info(
                "Sync backend selected: LEGACY single-collect "
                "(rows=%d <= threshold=%d). Faster per-row but materializes "
                "the full frame; ensure the host has >= rows * cols * "
                "~250 bytes free RAM for the dedupe_df dispatch. See #386, #401.",
                total_rows, streaming_threshold,
            )

            # Chain the internal-column adds lazily.
            new_records_lf = (
                new_records_lf
                .with_columns(pl.lit("new").alias("__source__"))
                .with_row_index("__row_id__")
                .with_columns(pl.col("__row_id__").cast(pl.Int64))
            )
            return _full_scan_pipeline(
                connector, new_records_lf, source_table, config, matchkeys,
                output_mode, dry_run, run_id, cfg_hash, total_rows,
            )
        finally:
            # Staging dir cleanup AFTER the pipeline's collect has read
            # the parquet chunks (#388). The prior weakref.finalize on
            # the LazyFrame fired too early when `lf.with_columns(...)`
            # rebound the variable to a derived frame.
            if staging_dir is not None:
                shutil.rmtree(staging_dir, ignore_errors=True)

    # Incremental path -- still eager because the rest of the
    # incremental pipeline expects DataFrame. Memory bound here is the
    # delta rows (typically small), not the full table.
    new_records, existing_records = _read_incremental(
        connector, source_table, state, incremental_column, chunk_size,
    )
    logger.info(
        "Incremental: %d new records, %d existing in %s",
        new_records.height, existing_records.height if existing_records.height else "N/A", source_table,
    )
    if new_records.height == 0:
        logger.info("No new records to process.")
        return {"new_records": 0, "matches": 0, "actions": []}
    new_records = new_records.with_columns(pl.lit("new").alias("__source__"))
    new_records = new_records.with_row_index("__row_id__").with_columns(
        pl.col("__row_id__").cast(pl.Int64)
    )
    new_records, _ = auto_fix_dataframe(new_records)
    return _incremental_pipeline(
        connector, new_records, source_table, config, matchkeys,
        output_mode, dry_run, run_id, cfg_hash, total_rows,
    )


def _read_all_lazy(
    connector: DatabaseConnector, table: str, chunk_size: int,
) -> tuple[pl.LazyFrame, Path] | tuple[None, None]:
    """Stage chunks to a temp parquet and return (LazyFrame, staging_dir).

    Unlike ``_read_all``, this does NOT materialize the full frame --
    the LazyFrame points at the on-disk parquet so subsequent
    ``with_columns`` / ``compute_matchkeys`` operations chain lazily.
    The materialization happens later in the pipeline at the FIRST
    ``.collect()`` call -- typically once, not twice (#384).

    Returns the staging path alongside the LazyFrame so the caller
    can clean up in a try/finally **after** the pipeline finishes its
    collect. An earlier version tied the staging-dir lifetime to the
    LazyFrame object via ``weakref.finalize``, but that fires when the
    initial Python wrapper is GC'd -- which happens as soon as
    downstream ``lf.with_columns(...)`` rebinds the variable to a new
    LazyFrame, even though the underlying plan still references the
    original ``scan_parquet`` node. Net effect: the staging dir got
    deleted before the eventual ``.collect()`` could read it, and
    Polars erroreed with "expanded paths were empty" (#388).

    Returns ``(None, None)`` if the connector yielded zero chunks.
    """
    import shutil
    import tempfile
    from pathlib import Path

    staging = Path(tempfile.mkdtemp(prefix="gm_sync_read_"))
    unified_schema: dict[str, pl.DataType] | None = None
    n_chunks = 0
    for i, chunk in enumerate(connector.read_table(table, chunk_size)):
        if unified_schema is None:
            unified_schema = {
                name: pl.Utf8 if dt == pl.Null else dt
                for name, dt in chunk.schema.items()
            }
        chunk = _cast_to_schema(chunk, unified_schema)
        chunk.write_parquet(staging / f"chunk_{i:06d}.parquet")
        n_chunks += 1
    if n_chunks == 0:
        shutil.rmtree(staging, ignore_errors=True)
        return None, None
    logger.info(
        "Staged %d chunks to %s for lazy scan",
        n_chunks, staging,
    )
    lf = pl.scan_parquet(staging / "chunk_*.parquet")
    return lf, staging


def _read_all(connector: DatabaseConnector, table: str, chunk_size: int) -> pl.DataFrame:
    """Eager variant of ``_read_all_lazy``: collects the LazyFrame so
    callers that haven't been migrated to the lazy path still work.

    See ``_read_all_lazy`` for the streaming-friendly variant -- prefer
    it for the full-scan path (#384). This shim stays for
    ``_read_incremental``'s fallback case where the rest of the
    incremental pipeline still expects an eager DataFrame.
    """
    import shutil
    import tempfile
    from pathlib import Path

    staging = Path(tempfile.mkdtemp(prefix="gm_sync_read_"))
    try:
        # Unified schema across all chunks. The first non-empty chunk
        # establishes the canonical dtype for each column; subsequent
        # chunks cast columns to that schema before write. Without this,
        # Polars infers Null dtype on a chunk where a column is 100%
        # NULL (e.g. a sparse bigint column with no values in the first
        # 10K rows) and Int64 on a later chunk that does have values,
        # then pl.read_parquet rejects the cross-file mismatch with
        # "data type mismatch for column X: incoming: Null != target:
        # Int64" (#381 -- regression introduced by the staging-parquet
        # rewrite for #379).
        unified_schema: dict[str, pl.DataType] | None = None
        n_chunks = 0
        for i, chunk in enumerate(connector.read_table(table, chunk_size)):
            if unified_schema is None:
                # First chunk seeds the schema. Promote any Null-dtype
                # columns to Utf8 so they're castable later -- matches
                # the _normalize_chunk_schema behavior in connector.py
                # but applied here so the unified schema is concrete.
                unified_schema = {
                    name: pl.Utf8 if dt == pl.Null else dt
                    for name, dt in chunk.schema.items()
                }
            chunk = _cast_to_schema(chunk, unified_schema)
            chunk.write_parquet(staging / f"chunk_{i:06d}.parquet")
            n_chunks += 1
        if n_chunks == 0:
            return pl.DataFrame()
        return pl.read_parquet(staging / "chunk_*.parquet")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _cast_to_schema(
    df: pl.DataFrame,
    schema: dict[str, pl.DataType],
) -> pl.DataFrame:
    """Cast ``df`` columns to the unified ``schema``, narrowing dtypes
    where Polars inferred something looser (Null -> Utf8, Int64 -> the
    canonical Int64, etc.). Missing columns are added as all-null with
    the target dtype; extras are kept as-is so callers can still see
    schema drift on read-back.
    """
    exprs = []
    for name, dtype in schema.items():
        if name not in df.columns:
            exprs.append(pl.lit(None).cast(dtype).alias(name))
        elif df.schema[name] != dtype:
            exprs.append(pl.col(name).cast(dtype, strict=False))
    if not exprs:
        return df
    return df.with_columns(exprs).select(list(schema.keys()))


def _read_incremental(
    connector: DatabaseConnector,
    table: str,
    state: dict,
    incremental_column: str | None,
    chunk_size: int,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Read only new records since last state."""
    from goldenmatch.db.connector import _quote_ident

    if incremental_column and state.get("last_incremental_value"):
        last_val = state["last_incremental_value"]
        query = (
            f"SELECT * FROM {_quote_ident(table)} "
            f"WHERE {_quote_ident(incremental_column)} > '{last_val}' "
            f"ORDER BY {_quote_ident(incremental_column)}"
        )
        new_records = connector.read_query(query)
    elif state.get("last_row_id"):
        last_id = state["last_row_id"]
        query = f"SELECT * FROM {_quote_ident(table)} WHERE id > {last_id} ORDER BY id"
        new_records = connector.read_query(query)
    else:
        new_records = _read_all(connector, table, chunk_size)

    return new_records, pl.DataFrame()  # existing loaded on-demand via blocking


def _full_scan_pipeline(
    connector, df_or_lf, source_table, config, matchkeys,
    output_mode, dry_run, run_id, cfg_hash, total_rows,
):
    """Run full dedupe on all records via the main dedupe_df entry.

    Prior to #391's OOM diagnosis (exit 137 mid-pipeline at 1M rows on a
    small-memory host) this function rolled its own pipeline -- direct
    calls to ``find_exact_matches`` / ``build_blocks`` / ``find_fuzzy_matches``
    / ``build_clusters`` / ``build_golden_record``. That path bypassed the
    v3 backend planner and the controller-budget cap, so on real-world
    skewed data (e.g. 100K rows sharing one normalized name) the Polars
    self-join inside ``find_exact_matches`` produced billions of pair
    rows and the kernel OOM-killed the process.

    Routing through ``dedupe_df`` gets us the same machinery the bench
    uses end-to-end:
      * v3 planner picks ``backend="bucket"`` at >= 100K rows (streaming,
        block-batched, no NxN allocation on skewed blocks)
      * controller-budget cap protects against pathological iterations
      * auto-fix + matchkey computation run inside the pipeline -- one
        materialization, not two

    ``confidence_required=False`` keeps the sync running end-to-end even
    when the controller commits a RED profile; callers who want the
    strict raise can pass an explicit ``GoldenMatchConfig`` with their
    own posture. See #391 for the original OOM trace.
    """
    from goldenmatch._api import dedupe_df

    logger.info(
        "Pipeline: %d matchkeys configured (%s)",
        len(matchkeys),
        ", ".join(f"{mk.name}:{mk.type}" for mk in matchkeys) if matchkeys else "<none>",
    )
    logger.info("Pipeline: collecting input frame...")
    df = df_or_lf.collect() if isinstance(df_or_lf, pl.LazyFrame) else df_or_lf
    logger.info("Pipeline: materialized %d rows x %d cols", df.height, df.width)

    # dedupe_df adds its own __source__ and __row_id__ unconditionally
    # (core/pipeline.py::run_dedupe_df → _add_row_ids). run_sync attaches
    # these upstream so the legacy hand-rolled pipeline could correlate
    # rows; the dispatch path doesn't need them and re-adding via
    # with_columns raises "duplicate column name __row_id__" (#394).
    bookkeeping_cols = [c for c in ("__row_id__", "__source__") if c in df.columns]
    if bookkeeping_cols:
        df = df.drop(bookkeeping_cols)
        logger.info(
            "Pipeline: dropped pre-existing bookkeeping cols %s "
            "(dedupe_df re-adds internally)", bookkeeping_cols,
        )

    logger.info("Pipeline: dispatching to dedupe_df (routes through v3 planner)...")
    result = dedupe_df(df, config=config, confidence_required=False)
    clusters = result.clusters
    golden_df = result.golden
    all_pairs = list(result.scored_pairs)
    logger.info(
        "Pipeline: dedupe_df returned %d scored pairs, %d clusters",
        len(all_pairs), len(clusters),
    )

    match_actions = [(int(a), int(b), float(s), "merged") for a, b, s in all_pairs]

    if not dry_run:
        log_matches_batch(connector, match_actions, run_id)
        write_golden_records(connector, clusters, golden_df, source_table, output_mode)
        update_state(connector, source_table, cfg_hash=cfg_hash, record_count=total_rows)

    multi_clusters = {k: v for k, v in clusters.items() if v["size"] > 1}
    golden_count = golden_df.height if golden_df is not None else 0
    logger.info(
        "Sync complete: %d records, %d pairs, %d multi-member clusters, %d golden records",
        df.height, len(all_pairs), len(multi_clusters), golden_count,
    )
    if df.height > 0 and not all_pairs and not multi_clusters:
        logger.warning(
            "Sync produced zero pairs and zero clusters across %d records. "
            "Possible causes: empty matchkey config, all-NULL blocking "
            "column, scorer threshold too high. See #391.",
            df.height,
        )

    return {
        "new_records": df.height,
        "matches": len(all_pairs),
        "clusters": len(multi_clusters),
        "golden_records": golden_count,
        "actions": match_actions,
        "run_id": run_id,
    }


def _incremental_pipeline(
    connector, new_df, source_table, config, matchkeys,
    output_mode, dry_run, run_id, cfg_hash, total_rows,
    embed_chunk_size=100000, merge_mode="recompute",
):
    """Match new records against existing database using hybrid blocking + reconciliation."""
    from goldenmatch.core.scorer import score_pair
    from goldenmatch.db.ann_index import PersistentANNIndex
    from goldenmatch.db.clusters import get_cluster_for_record
    from goldenmatch.db.hybrid_blocking import find_candidates
    from goldenmatch.db.reconcile import reconcile_match

    all_pairs = []
    match_actions = []

    id_col = "id" if "id" in new_df.columns else new_df.columns[0]
    matchable_cols = [c for c in new_df.columns if not c.startswith("__")]

    golden_rules = config.golden_rules
    max_cluster_size = 100
    if golden_rules and hasattr(golden_rules, "max_cluster_size"):
        max_cluster_size = golden_rules.max_cluster_size

    # Load or build ANN index
    ann_index = None
    try:
        ann_index = PersistentANNIndex(
            connector=connector, source_table=source_table,
        )
        ann_index.load_or_build()
    except Exception as e:
        logger.debug("ANN index not available: %s", e)

    for row in new_df.iter_rows(named=True):
        record_id = row.get(id_col)

        if not config.blocking:
            match_actions.append((record_id, 0, 0.0, "new"))
            continue

        # Hybrid blocking: SQL + ANN union
        candidates = find_candidates(
            new_record=row,
            connector=connector,
            ann_index=ann_index,
            blocking_config=config.blocking,
            source_table=source_table,
            columns=matchable_cols,
            id_column=id_col,
        )

        if candidates.height == 0:
            if not dry_run:
                result = reconcile_match(
                    row, record_id, [], {},
                    connector, source_table, golden_rules,
                    max_cluster_size, merge_mode, run_id, id_col,
                )
                match_actions.append((record_id, 0, 0.0, result.action))
            else:
                match_actions.append((record_id, 0, 0.0, "new"))
            continue

        # Score against each candidate
        matched_ids_with_scores: dict[int, float] = {}

        for mk in matchkeys:
            if mk.type != "weighted":
                continue

            for candidate in candidates.iter_rows(named=True):
                cand_id = candidate.get(id_col)
                score = score_pair(row, candidate, mk.fields)

                if score >= (mk.threshold or 0.0):
                    if cand_id not in matched_ids_with_scores or score > matched_ids_with_scores[cand_id]:
                        matched_ids_with_scores[cand_id] = score

        if not matched_ids_with_scores:
            if not dry_run:
                result = reconcile_match(
                    row, record_id, [], {},
                    connector, source_table, golden_rules,
                    max_cluster_size, merge_mode, run_id, id_col,
                )
                match_actions.append((record_id, 0, 0.0, result.action))
            else:
                match_actions.append((record_id, 0, 0.0, "new"))
            continue

        if not dry_run:
            # Look up clusters for matched records
            matched_cluster_ids = []
            cluster_scores: dict[int, float] = {}
            for match_id, score in matched_ids_with_scores.items():
                cid = get_cluster_for_record(connector, match_id, source_table)
                if cid is not None:
                    if cid not in cluster_scores or score > cluster_scores[cid]:
                        cluster_scores[cid] = score
                    if cid not in matched_cluster_ids:
                        matched_cluster_ids.append(cid)

            # Reconcile
            result = reconcile_match(
                row, record_id, matched_cluster_ids, cluster_scores,
                connector, source_table, golden_rules,
                max_cluster_size, merge_mode, run_id, id_col,
            )

            best_match = max(matched_ids_with_scores.items(), key=lambda x: x[1])
            all_pairs.append((record_id, best_match[0], best_match[1]))
            match_actions.append((record_id, best_match[0], best_match[1], result.action))
        else:
            best_match = max(matched_ids_with_scores.items(), key=lambda x: x[1])
            all_pairs.append((record_id, best_match[0], best_match[1]))
            match_actions.append((record_id, best_match[0], best_match[1], "merged"))

    # Progressive: embed next chunk of existing records
    if ann_index is not None:
        _embed_next_chunk(connector, ann_index, source_table, matchable_cols, embed_chunk_size)

    # Save index
    if ann_index is not None:
        try:
            ann_index.save()
        except Exception as e:
            logger.debug("Failed to save ANN index: %s", e)

    if not dry_run:
        log_matches_batch(connector, match_actions, run_id)
        update_state(
            connector, source_table,
            cfg_hash=cfg_hash, record_count=total_rows,
        )

    merged = sum(1 for _, _, _, a in match_actions if a == "merged")
    new_entities = sum(1 for _, _, _, a in match_actions if a == "new")

    logger.info(
        "Incremental sync: %d new records — %d merged, %d new entities",
        new_df.height, merged, new_entities,
    )

    return {
        "new_records": new_df.height,
        "matches": len(all_pairs),
        "merged": merged,
        "new_entities": new_entities,
        "actions": match_actions,
        "run_id": run_id,
    }


def _embed_next_chunk(
    connector: DatabaseConnector,
    ann_index: PersistentANNIndex,  # noqa: F821  # forward ref, resolved lazily
    source_table: str,
    columns: list[str],
    chunk_size: int = 100000,
) -> int:
    """Embed next chunk of existing records for progressive ANN coverage."""
    try:
        from goldenmatch.core.embedder import get_embedder
        from goldenmatch.db.connector import _quote_ident

        # Find records not yet embedded
        _already_embedded = ann_index.record_count
        query = (
            f"SELECT id FROM {_quote_ident(source_table)} "
            f"WHERE id NOT IN ("
            f"  SELECT record_id FROM gm_embeddings "
            f"  WHERE source_table = '{source_table}'"
            f") ORDER BY id LIMIT {chunk_size}"
        )

        df = connector.read_query(query)
        if df.height == 0:
            logger.info("All records already embedded.")
            return 0

        record_ids = df["id"].to_list()

        # Fetch full records for embedding
        id_list = ", ".join(str(int(i)) for i in record_ids)
        records_df = connector.read_query(
            f"SELECT * FROM {_quote_ident(source_table)} WHERE id IN ({id_list})"
        )

        # Build text for embedding
        texts = []
        for row in records_df.iter_rows(named=True):
            parts = [f"{c}: {row.get(c, '')}" for c in columns if row.get(c) is not None]
            texts.append(" | ".join(parts) if parts else "")

        embedder = get_embedder(ann_index.model_name)
        embeddings = embedder.embed_column(texts, cache_key=f"_progressive_{source_table}")

        ann_index.add(record_ids, embeddings)
        logger.info("Progressive embedding: added %d records (%d total)", len(record_ids), ann_index.record_count)
        return len(record_ids)

    except ImportError:
        logger.debug("sentence-transformers not available for progressive embedding")
        return 0
    except Exception as e:
        logger.warning("Progressive embedding failed: %s", e)
        return 0


# ---------------------------------------------------------------------------
# Streaming-block sync (#386)
#
# Bounds peak RSS at any dataset size by scanning one block at a time from
# the staging parquet instead of materializing the full frame. Gated by
# GOLDENMATCH_SYNC_STREAMING_THRESHOLD (default 5_000_000); below the
# threshold, the legacy _full_scan_pipeline single-collect path is faster.
#
# Spec: docs/superpowers/specs/2026-05-21-streaming-block-sync-design.md
# Plan: docs/superpowers/plans/2026-05-21-streaming-block-sync.md
# ---------------------------------------------------------------------------


def _index_block_sizes(
    staging_dir: Path,
    config: GoldenMatchConfig,
) -> pl.DataFrame:
    """Stream-aggregate block sizes from the staging parquet.

    Returns a ``{__block_key__, count}`` DataFrame sorted descending by
    count so the per-block loop fails fast on hot blocks. Memory is
    bounded by the group-by streaming engine, not the dataset size.

    For multi-pass blocking (``config.blocking.keys`` length > 1), the
    first key drives the index; multi-pass union is a follow-up.
    Spec calls this out as a v1 simplification.

    For ``config.blocking is None``, returns a degenerate single-block
    index covering every row. Callers should warn the user — running
    sync at 10M+ rows without blocking is not the path streaming sync
    optimises.
    """
    from goldenmatch.core.blocker import _build_block_key_expr

    parquet_glob = staging_dir / "chunk_*.parquet"
    scan = pl.scan_parquet(parquet_glob)

    if config.blocking is None or not config.blocking.keys:
        # Degenerate single-block path. Still returns a real index frame
        # (with one row) so downstream code can iterate uniformly.
        n = scan.select(pl.len()).collect().item()
        return pl.DataFrame({
            "__block_key__": ["__all__"],
            "count": [int(n)],
        })

    primary_key = config.blocking.keys[0]
    block_key_expr = _build_block_key_expr(primary_key)

    # Filter NULL block keys -- they don't form valid blocks (matches the
    # gate in core.blocker._build_static_blocks). Streaming group-by keeps
    # memory bounded; the result is small (~num distinct block keys).
    return (
        scan
        .with_columns(block_key_expr)
        .filter(pl.col("__block_key__").is_not_null())
        .filter(pl.col("__block_key__") != "")
        .group_by("__block_key__")
        .agg(pl.len().alias("count"))
        .sort("count", descending=True)
        .collect(engine="streaming")
    )


def _score_block_streaming(
    staging_dir: Path,
    block_key: str,
    config: GoldenMatchConfig,
    matched_pairs: set[tuple[int, int]],
) -> list[tuple[int, int, float]]:
    """Score a single block by scan-filter-collect against the staging
    parquet. Reuses ``_score_partition_with_config`` from #397 so the
    streaming sync path and distributed scoring share the same kernel.

    ``matched_pairs`` is mutated in-place (canonical ``(min, max)``
    tuples added for every new pair). The set lives on the driver
    across all blocks so cross-block dedup works without re-walking
    pairs.

    Returns the new pairs from this block (after de-duping against
    ``matched_pairs``). Empty list if the block has < 2 rows or no
    pairs cross the matchkey threshold.
    """
    from goldenmatch.core.blocker import _build_block_key_expr
    from goldenmatch.core.pipeline import _score_partition_with_config

    parquet_glob = staging_dir / "chunk_*.parquet"
    scan = pl.scan_parquet(parquet_glob)

    # Add __row_id__ BEFORE filtering so the kernel sees global row ids
    # (position in the full staging parquet), not local-to-block ids.
    # Without this, the same pair (0,1) emerges from every block and
    # matched_pairs's cross-block dedup collapses them into one entry.
    scan_with_ids = scan.with_row_index("__row_id__").with_columns(
        pl.col("__row_id__").cast(pl.Int64),
    )

    if config.blocking is None or not config.blocking.keys:
        # Degenerate single-block path -- whole frame is one block.
        # The index pass assigns block_key="__all__"; honor that here
        # by reading everything.
        block_df = scan_with_ids.collect()
    else:
        primary_key = config.blocking.keys[0]
        block_key_expr = _build_block_key_expr(primary_key)
        block_df = (
            scan_with_ids
            .with_columns(block_key_expr)
            .filter(pl.col("__block_key__") == block_key)
            .drop("__block_key__")
            .collect()
        )

    if block_df.height < 2:
        return []

    # Force bucket backend -- same posture as distributed scoring. The
    # kernel will do its own matchkey + scoring work; clustering and
    # golden are driver-side responsibilities.
    if hasattr(config, "model_copy"):
        local_cfg = config.model_copy()
    else:
        import copy as _copy
        local_cfg = _copy.deepcopy(config)
    local_cfg.backend = "bucket"

    pairs = _score_partition_with_config(block_df, local_cfg)

    # De-dup against the global matched_pairs set. Canonicalize each
    # pair as (min, max) for the set entry to match the project-wide
    # invariant.
    new_pairs: list[tuple[int, int, float]] = []
    for a, b, s in pairs:
        canonical = (min(a, b), max(a, b))
        if canonical in matched_pairs:
            continue
        matched_pairs.add(canonical)
        new_pairs.append((a, b, s))
    return new_pairs


def _full_scan_streaming(
    connector,
    staging_dir: Path,
    source_table: str,
    config: GoldenMatchConfig,
    matchkeys: list,
    output_mode: str,
    dry_run: bool,
    run_id: str,
    cfg_hash: str,
    total_rows: int,
) -> dict:
    """Streaming-block sync orchestrator (#386).

    Walks the staging parquet one block at a time. Peak RSS is bounded
    by the largest individual block's scoring footprint, not by the
    total dataset size.

    Used when ``total_rows > GOLDENMATCH_SYNC_STREAMING_THRESHOLD``
    (default 5_000_000). Below that, the legacy single-collect path
    via ``_full_scan_pipeline`` is faster per-row and fits comfortably.

    Returns the same shape as ``_full_scan_pipeline`` so callers see no
    difference at the dict level.
    """
    from goldenmatch.core.cluster import build_clusters
    from goldenmatch.core.golden import build_golden_record

    logger.info(
        "Streaming pipeline: %d matchkeys configured (%s)",
        len(matchkeys),
        ", ".join(f"{mk.name}:{mk.type}" for mk in matchkeys) if matchkeys else "<none>",
    )

    if config.blocking is None or not config.blocking.keys:
        logger.warning(
            "Streaming sync invoked with no blocking config. The whole "
            "frame is one degenerate block; expect single-collect memory "
            "characteristics. Configure blocking for true per-block "
            "streaming. See #386.",
        )

    # Index pass -- bounded memory, returns the sorted block list.
    logger.info("Streaming pipeline: indexing block sizes...")
    block_index = _index_block_sizes(staging_dir, config)
    logger.info(
        "Streaming pipeline: indexed %d blocks; largest = %d rows",
        block_index.height,
        block_index["count"].max() if block_index.height > 0 else 0,
    )

    all_pairs: list[tuple[int, int, float]] = []
    matched_pairs: set[tuple[int, int]] = set()

    # #422 fix 2: parallel per-block scoring. Each block's
    # _score_block_streaming is independent (rescans the staging parquet
    # filtered on __block_key__). Originally serial; on 585K small blocks
    # the per-block orchestration dominated (~10 min overhead at the
    # observed sub-ms per-block scoring time).
    #
    # ThreadPoolExecutor (not Process) because:
    # - rapidfuzz releases the GIL inside the inner scorer
    # - polars also releases the GIL during scan-filter-collect work
    # - shared filesystem cache + parquet scan handle is process-local
    # - matched_pairs cross-block dedup is dropped to enable parallelism;
    #   correctness handled by canonical (min, max) tuple dedup at the
    #   end (one set update across the merged pair list).
    #
    # Worker count: min(cpu_count, 8). Bounded to avoid filesystem
    # thrash on the staging parquet glob. Env override:
    # GOLDENMATCH_STREAMING_BLOCK_WORKERS.
    import os as _os
    from concurrent.futures import ThreadPoolExecutor

    _max_workers_env = _os.environ.get("GOLDENMATCH_STREAMING_BLOCK_WORKERS")
    if _max_workers_env:
        try:
            max_workers = max(int(_max_workers_env), 1)
        except ValueError:
            max_workers = min(_os.cpu_count() or 4, 8)
    else:
        max_workers = min(_os.cpu_count() or 4, 8)

    work_items: list[tuple[int, Any, int]] = []  # (i, block_key, count)
    for i, row in enumerate(block_index.iter_rows(named=True)):
        block_key = row["__block_key__"]
        block_n = row["count"]
        if block_n < 2:
            continue
        work_items.append((i, block_key, block_n))

    logger.info(
        "Streaming pipeline: dispatching %d non-singleton blocks "
        "across %d workers (#422)",
        len(work_items), max_workers,
    )

    # No-dedup variant of _score_block_streaming: skip cross-block
    # dedup against matched_pairs (the shared mutable set isn't
    # thread-safe and the dedup is just an optimization for skipping
    # work). Correctness restored at the final dedup pass below.
    def _score_one(args: tuple[int, Any, int]) -> tuple[int, Any, int, list[tuple[int, int, float]]]:
        i, block_key, block_n = args
        _empty: set[tuple[int, int]] = set()  # local empty set; worker-private
        pairs = _score_block_streaming(
            staging_dir, block_key, config, _empty,
        )
        return i, block_key, block_n, pairs

    # Match-log batching: collect all pairs from a worker, log when
    # the worker returns, so a long run is still visible in
    # gm_matches as blocks complete (mirrors the serial behavior).
    if max_workers <= 1 or len(work_items) <= 2:
        # Serial fallback path -- same as pre-#422.
        for args in work_items:
            i, block_key, block_n = args
            logger.info(
                "Streaming pipeline: block %d/%d (key=%r, size=%d)",
                i + 1, block_index.height, block_key, block_n,
            )
            _empty_serial: set[tuple[int, int]] = set()
            pairs = _score_block_streaming(
                staging_dir, block_key, config, _empty_serial,
            )
            all_pairs.extend(pairs)
            if pairs and not dry_run:
                log_matches_batch(
                    connector,
                    [(int(a), int(b), float(s), "merged") for a, b, s in pairs],
                    run_id,
                )
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            for i, block_key, block_n, pairs in pool.map(_score_one, work_items):
                logger.info(
                    "Streaming pipeline: block %d/%d (key=%r, size=%d) -> %d pairs",
                    i + 1, block_index.height, block_key, block_n, len(pairs),
                )
                all_pairs.extend(pairs)
                if pairs and not dry_run:
                    log_matches_batch(
                        connector,
                        [(int(a), int(b), float(s), "merged") for a, b, s in pairs],
                        run_id,
                    )

    # Final cross-block dedup: canonical (min, max) tuple set. Replaces
    # the per-block matched_pairs lookup that was dropped above.
    _seen: set[tuple[int, int]] = set()
    _deduped: list[tuple[int, int, float]] = []
    for a, b, s in all_pairs:
        canonical = (min(a, b), max(a, b))
        if canonical in _seen:
            continue
        _seen.add(canonical)
        _deduped.append((a, b, s))
    if len(_deduped) < len(all_pairs):
        logger.info(
            "Streaming pipeline: deduped %d -> %d pairs across blocks",
            len(all_pairs), len(_deduped),
        )
    all_pairs = _deduped
    matched_pairs.update(_seen)

    logger.info(
        "Streaming pipeline: scored %d pairs across %d blocks",
        len(all_pairs), block_index.height,
    )

    # Cluster on the merged pair set. Driver-side, O(pairs).
    # all_ids comes from a cheap projection across the staging parquet:
    # only the __row_id__ column is read. Polars streams the select.
    parquet_glob = staging_dir / "chunk_*.parquet"
    all_ids_df = (
        pl.scan_parquet(parquet_glob)
        .with_row_index("__row_id__")
        .select(pl.col("__row_id__").cast(pl.Int64))
        .collect(engine="streaming")
    )
    all_ids = all_ids_df["__row_id__"].to_list()
    logger.info(
        "Streaming pipeline: clustering %d records from %d pairs",
        len(all_ids), len(all_pairs),
    )
    clusters = build_clusters(all_pairs, all_ids, max_cluster_size=100)

    # Per-cluster golden -- only multi-member clusters need a golden
    # record. Mega-clusters are already capped by max_cluster_size=100.
    golden_rules = config.golden_rules
    golden_records: list[dict] = []
    for cluster_id, cluster_info in clusters.items():
        if cluster_info["size"] <= 1 or cluster_info.get("oversized"):
            continue
        member_ids = cluster_info["members"]
        cluster_df = (
            pl.scan_parquet(parquet_glob)
            .with_row_index("__row_id__")
            .with_columns(pl.col("__row_id__").cast(pl.Int64))
            .filter(pl.col("__row_id__").is_in(member_ids))
            .collect()
        )
        if cluster_df.height == 0:
            continue
        golden = build_golden_record(cluster_df, golden_rules)
        if golden:
            golden["__cluster_id__"] = cluster_id
            golden_records.append(golden)

    golden_df = pl.DataFrame(golden_records) if golden_records else None

    match_actions = [
        (int(a), int(b), float(s), "merged") for a, b, s in all_pairs
    ]

    if not dry_run:
        write_golden_records(
            connector, clusters, golden_df, source_table, output_mode,
        )
        update_state(
            connector, source_table, cfg_hash=cfg_hash, record_count=total_rows,
        )

    multi_clusters = {k: v for k, v in clusters.items() if v["size"] > 1}
    golden_count = golden_df.height if golden_df is not None else 0
    logger.info(
        "Streaming sync complete: %d records, %d pairs, %d multi-member "
        "clusters, %d golden records",
        total_rows, len(all_pairs), len(multi_clusters), golden_count,
    )
    if total_rows > 0 and not all_pairs and not multi_clusters:
        logger.warning(
            "Streaming sync produced zero pairs and zero clusters across "
            "%d records. Possible causes: empty matchkey config, all-NULL "
            "blocking column, scorer threshold too high.",
            total_rows,
        )

    return {
        "new_records": total_rows,
        "matches": len(all_pairs),
        "clusters": len(multi_clusters),
        "golden_records": golden_count,
        "actions": match_actions,
        "run_id": run_id,
    }
