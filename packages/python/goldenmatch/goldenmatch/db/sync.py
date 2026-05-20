"""Incremental sync orchestrator — matches new records against existing DB."""

from __future__ import annotations

import logging
from pathlib import Path

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
    """Run full dedupe on all records.

    Accepts either an eager ``pl.DataFrame`` (legacy callers) or a
    ``pl.LazyFrame`` (new full-scan path from run_sync). The lazy path
    keeps the data on disk through matchkey computation, materializing
    only once via the ``.collect()`` below -- vs the old form which
    materialized in _read_all AND again here after matchkeys, peaking
    at ~2x the frame size during the second collect (#384).
    """
    from goldenmatch.core.autofix import auto_fix_dataframe
    from goldenmatch.core.blocker import build_blocks
    from goldenmatch.core.cluster import build_clusters
    from goldenmatch.core.golden import build_golden_record
    from goldenmatch.core.matchkey import compute_matchkeys
    from goldenmatch.core.scorer import find_exact_matches, find_fuzzy_matches

    # Normalize input -- accept either DataFrame (legacy) or LazyFrame
    # (new memory-bounded path). DataFrame.lazy() is a no-op view; the
    # collect() below is the one materialization.
    logger.info(
        "Pipeline: %d matchkeys configured (%s)",
        len(matchkeys),
        ", ".join(f"{mk.name}:{mk.type}" for mk in matchkeys) if matchkeys else "<none>",
    )
    lf = df_or_lf.lazy() if isinstance(df_or_lf, pl.DataFrame) else df_or_lf
    lf = compute_matchkeys(lf, matchkeys)
    logger.info("Pipeline: materializing matchkey-joined frame...")
    df = lf.collect()
    logger.info(
        "Pipeline: materialized %d rows x %d cols", df.height, df.width,
    )
    # auto_fix runs after the single collect so it operates on the
    # already-materialized frame (it expects DataFrame).
    df, _ = auto_fix_dataframe(df)
    logger.info("Pipeline: auto_fix complete (%d rows)", df.height)

    # Score pairs
    all_pairs = []
    matched_pairs = set()

    exact_mks = [mk for mk in matchkeys if mk.type == "exact"]
    for mk in exact_mks:
        pairs = find_exact_matches(df.lazy(), mk)
        all_pairs.extend(pairs)
        for a, b, s in pairs:
            matched_pairs.add((min(a, b), max(a, b)))
    if exact_mks:
        logger.info(
            "Pipeline: %d pairs from %d exact matchkeys",
            len(all_pairs), len(exact_mks),
        )

    if config.blocking:
        weighted_mks = [mk for mk in matchkeys if mk.type == "weighted"]
        for mk in weighted_mks:
            pairs_before = len(all_pairs)
            blocks = build_blocks(df.lazy(), config.blocking)
            for block in blocks:
                bdf = block.df.collect()
                pairs = find_fuzzy_matches(
                    bdf, mk,
                    exclude_pairs=matched_pairs,
                    pre_scored_pairs=block.pre_scored_pairs,
                )
                all_pairs.extend(pairs)
                for a, b, s in pairs:
                    matched_pairs.add((min(a, b), max(a, b)))
            if weighted_mks:
                logger.info(
                    "Pipeline: matchkey %r contributed %d pairs (total %d)",
                    mk.name, len(all_pairs) - pairs_before, len(all_pairs),
                )

    # Cluster
    all_ids = df["__row_id__"].to_list()
    logger.info(
        "Pipeline: clustering %d records from %d scored pairs",
        len(all_ids), len(all_pairs),
    )
    clusters = build_clusters(all_pairs, all_ids, max_cluster_size=100)

    # Golden records
    golden_rules = config.golden_rules
    golden_records = []
    for cluster_id, cluster_info in clusters.items():
        if cluster_info["size"] > 1 and not cluster_info.get("oversized"):
            cluster_df = df.filter(pl.col("__row_id__").is_in(cluster_info["members"]))
            golden = build_golden_record(cluster_df, golden_rules)
            if golden:
                golden["__cluster_id__"] = cluster_id
                golden_records.append(golden)

    golden_df = pl.DataFrame(golden_records) if golden_records else None

    # Log matches
    match_actions = []
    for a, b, s in all_pairs:
        match_actions.append((int(a), int(b), float(s), "merged"))

    if not dry_run:
        log_matches_batch(connector, match_actions, run_id)
        write_golden_records(connector, clusters, golden_df, source_table, output_mode)
        update_state(connector, source_table, cfg_hash=cfg_hash, record_count=total_rows)

    multi_clusters = {k: v for k, v in clusters.items() if v["size"] > 1}
    logger.info(
        "Sync complete: %d records, %d pairs, %d multi-member clusters, %d golden records",
        df.height, len(all_pairs), len(multi_clusters), len(golden_records),
    )
    # Loud-fail the silent-success case (#391): if the pipeline ran on
    # non-empty input but produced zero pairs AND zero clusters, that's
    # almost always a misconfiguration -- empty matchkey list, all-NULL
    # blocking column, scorer threshold too high, etc. Make it
    # observable instead of returning a clean exit with empty tables.
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
        "golden_records": len(golden_records),
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
