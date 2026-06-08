"""Large dataset mode — chunked processing for files that don't fit in memory.

Processes a CSV/Parquet in chunks, maintains a persistent match index,
and merges results across chunks. Handles datasets from 1M to 100M+ records.

Architecture:
  Chunk 1 → match within chunk → add to index
  Chunk 2 → match within chunk + match against index → add to index
  Chunk 3 → match within chunk + match against index → add to index
  ...
  Final → merge all clusters → compute golden records
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import polars as pl

from goldenmatch.config.schemas import GoldenMatchConfig

logger = logging.getLogger(__name__)


class ChunkedMatcher:
    """Process large files in chunks with persistent matching."""

    def __init__(
        self,
        config: GoldenMatchConfig,
        chunk_size: int = 100_000,
    ):
        self.config = config
        self.chunk_size = chunk_size

        # Persistent state across chunks
        self._all_pairs: list[tuple[int, int, float]] = []
        self._all_ids: list[int] = []
        self._index_df: pl.DataFrame | None = None  # slim slice of prior chunks
        # Per-blocking-key bucketed index: list[dict[block_key_str, slim_df]].
        # Lazily populated on first `_add_to_index` when blocking is
        # configured with `strategy="static"`. Lets cross-chunk matching
        # skip pure-index blocks and avoid joint-frame block building.
        self._index_by_block: list[dict[str, pl.DataFrame]] | None = None
        self._row_offset = 0
        self._total_processed = 0
        self._chunk_count = 0

    def process_file(
        self,
        file_path: str | Path,
        on_chunk: callable | None = None,
    ) -> dict:
        """Process a large file in chunks.

        Args:
            file_path: Path to CSV or Parquet file.
            on_chunk: Optional callback(chunk_num, records_processed, pairs_found).

        Returns:
            Summary dict with stats.
        """
        from goldenmatch.core.autofix import auto_fix_dataframe
        from goldenmatch.core.blocker import build_blocks
        from goldenmatch.core.cluster import build_clusters
        from goldenmatch.core.matchkey import compute_matchkeys
        from goldenmatch.core.scorer import (
            find_exact_matches,
            score_blocks_parallel,
        )
        from goldenmatch.core.standardize import apply_standardization

        file_path = Path(file_path)
        matchkeys = self.config.get_matchkeys()
        t_start = time.perf_counter()

        # Determine reader
        if file_path.suffix == ".parquet":
            reader = self._read_parquet_chunks(file_path)
        else:
            reader = self._read_csv_chunks(file_path)

        for chunk_df in reader:
            self._chunk_count += 1
            chunk_start = time.perf_counter()

            # Add row IDs with offset
            chunk_df = chunk_df.with_row_index("__row_id__").with_columns(
                (pl.col("__row_id__") + self._row_offset).cast(pl.Int64).alias("__row_id__")
            )
            chunk_df = chunk_df.with_columns(pl.lit("source").alias("__source__"))

            # Auto-fix
            chunk_df, _ = auto_fix_dataframe(chunk_df)

            # Standardize
            if self.config.standardization:
                lf = chunk_df.lazy()
                lf = apply_standardization(lf, self.config.standardization)
                chunk_df = lf.collect()

            # Compute matchkeys
            lf = chunk_df.lazy()
            lf = compute_matchkeys(lf, matchkeys)
            chunk_df = lf.collect()

            # Match within chunk
            chunk_pairs = []
            matched_pairs = set()

            for mk in matchkeys:
                if mk.type == "exact":
                    pairs = find_exact_matches(chunk_df.lazy(), mk)
                    chunk_pairs.extend(pairs)
                    for a, b, s in pairs:
                        matched_pairs.add((min(a, b), max(a, b)))

            if self.config.blocking:
                for mk in matchkeys:
                    if mk.type == "weighted":
                        blocks = build_blocks(chunk_df.lazy(), self.config.blocking)
                        pairs = score_blocks_parallel(blocks, mk, matched_pairs)
                        chunk_pairs.extend(pairs)

            # Match against index (cross-chunk matching)
            if self._index_df is not None and self._index_df.height > 0:
                cross_pairs = self._match_against_index(chunk_df, matchkeys)
                chunk_pairs.extend(cross_pairs)

            # Add to index (sample representative records for future cross-chunk matching)
            self._add_to_index(chunk_df)

            # Accumulate
            self._all_pairs.extend(chunk_pairs)
            chunk_ids = chunk_df["__row_id__"].to_list()
            self._all_ids.extend(chunk_ids)
            self._row_offset += chunk_df.height
            self._total_processed += chunk_df.height

            elapsed = time.perf_counter() - chunk_start

            logger.info(
                "Chunk %d: %d records, %d pairs (%.1fs, %d rec/s)",
                self._chunk_count, chunk_df.height, len(chunk_pairs),
                elapsed, chunk_df.height / elapsed if elapsed > 0 else 0,
            )

            if on_chunk:
                on_chunk(self._chunk_count, self._total_processed, len(self._all_pairs))

        # Final clustering across all chunks
        logger.info("Clustering %d records, %d pairs...", len(self._all_ids), len(self._all_pairs))
        t_cluster = time.perf_counter()
        clusters = build_clusters(self._all_pairs, self._all_ids, max_cluster_size=100)
        cluster_time = time.perf_counter() - t_cluster

        multi_clusters = {k: v for k, v in clusters.items() if v["size"] > 1}
        total_time = time.perf_counter() - t_start

        return {
            "total_records": self._total_processed,
            "total_pairs": len(self._all_pairs),
            "total_clusters": len(multi_clusters),
            "chunks_processed": self._chunk_count,
            "chunk_size": self.chunk_size,
            "total_time": round(total_time, 2),
            "cluster_time": round(cluster_time, 2),
            "records_per_second": round(self._total_processed / total_time) if total_time > 0 else 0,
        }

    def _read_csv_chunks(self, path: Path):
        """Stream a CSV file in fixed-size row chunks.

        Uses ``pl.scan_csv(path).slice(offset, chunk_size).collect()`` so
        each chunk materializes independently. The full file is never
        held in memory — only the current chunk plus matchkey-relevant
        slices accumulated in ``_index_records``. Necessary for true
        out-of-core behavior at 5M+ rows on commodity hardware.

        The kwargs match what ``pl.read_csv`` historically accepted in
        this method (utf8-lossy + ignore_errors) with a plain fallback
        for older Polars versions that reject ``encoding=`` on the lazy
        path.
        """
        # infer_schema_length=0 forces all columns to Utf8. Without this,
        # scan_csv samples the first ~100 rows and may type a column as
        # int64 (e.g. an all-numeric ZIP column whose later values are
        # mixed-format strings). Downstream transforms like .lower() then
        # fail on int values. The eager pl.read_csv() path didn't hit this
        # because it scanned the whole file before inferring.
        try:
            lf = pl.scan_csv(
                path,
                encoding="utf8-lossy",
                ignore_errors=True,
                infer_schema_length=0,
            )
        except TypeError:
            # Some Polars versions don't accept encoding= on scan_csv.
            lf = pl.scan_csv(str(path), ignore_errors=True, infer_schema_length=0)

        offset = 0
        while True:
            chunk = lf.slice(offset, self.chunk_size).collect()
            if chunk.height == 0:
                break
            yield chunk
            offset += self.chunk_size

    def _read_parquet_chunks(self, path: Path):
        """Read Parquet in chunks."""
        total = pl.scan_parquet(path).select(pl.len()).collect().item()

        for offset in range(0, total, self.chunk_size):
            chunk = pl.scan_parquet(path).slice(offset, self.chunk_size).collect()
            if chunk.height == 0:
                break
            yield chunk

    def _block_key_column(
        self, df: pl.DataFrame, key_config: Any,
    ) -> pl.DataFrame:
        """Add a ``__block_key__`` column by delegating to
        ``blocker._build_block_key_expr`` (single source of truth: native
        fast path, shared AND per-field transforms).
        """
        from goldenmatch.core.blocker import _build_block_key_expr

        return df.with_columns(_build_block_key_expr(key_config))

    def _slim_columns(self, matchkeys: list) -> set[str]:
        """Columns to keep on the cross-chunk index slice.

        ``__row_id__`` plus every matchkey field plus every blocking-key
        field. Everything else is dropped to keep the index small.
        """
        keep: set[str] = {"__row_id__"}
        for mk in matchkeys:
            for f in mk.fields:
                if f.field:
                    keep.add(f.field)
        if self.config.blocking:
            for bk in self.config.blocking.keys or []:
                for fname in bk.fields:
                    keep.add(fname)
        return keep

    def _match_against_index(
        self, chunk_df: pl.DataFrame, matchkeys: list,
    ) -> list[tuple[int, int, float]]:
        """Vectorized cross-chunk matching via Polars.

        Concatenates the slim slice of the current chunk with the
        persistent index, recomputes the matchkey-derived columns over
        the joint frame, then runs the same ``find_exact_matches`` /
        ``build_blocks`` + ``score_blocks_parallel`` machinery the
        within-chunk path uses. Filters the result to **cross-pairs**
        (one row in the current chunk, one in the index) — pairs that
        are wholly within the index were already scored on the chunk
        that introduced them; pairs wholly within the current chunk
        were already scored by the within-chunk pass.

        Replaces the prior Python double-loop, which was O(chunk_size
        × index_size) with a per-row Python overhead that dominated
        wall time at scale.
        """
        from goldenmatch.core.matchkey import compute_matchkeys
        from goldenmatch.core.scorer import find_exact_matches

        assert self._index_df is not None

        # Project chunk down to the same slim shape as the index so the
        # vertical concat has uniform columns.
        keep_cols = self._slim_columns(matchkeys)
        chunk_slim = chunk_df.select([c for c in keep_cols if c in chunk_df.columns])

        joint = pl.concat([chunk_slim, self._index_df], how="vertical")
        joint_df = compute_matchkeys(joint.lazy(), matchkeys).collect()

        chunk_lo = self._row_offset
        chunk_hi = self._row_offset + chunk_df.height

        def _is_cross(a: int, b: int) -> bool:
            return (chunk_lo <= a < chunk_hi) != (chunk_lo <= b < chunk_hi)

        cross_pairs: list[tuple[int, int, float]] = []
        matched_pairs: set[tuple[int, int]] = set()

        for mk in matchkeys:
            if mk.type == "exact":
                for a, b, s in find_exact_matches(joint_df.lazy(), mk):
                    if _is_cross(a, b):
                        cross_pairs.append((min(a, b), max(a, b), s))
                        matched_pairs.add((min(a, b), max(a, b)))

        if self.config.blocking:
            for mk in matchkeys:
                if mk.type == "weighted":
                    weighted_pairs = self._match_weighted_cross_chunk(
                        chunk_df, joint_df, mk, matched_pairs, chunk_lo, chunk_hi,
                    )
                    for a, b, s in weighted_pairs:
                        cross_pairs.append((min(a, b), max(a, b), s))

        return cross_pairs

    def _match_weighted_cross_chunk(
        self,
        chunk_df: pl.DataFrame,
        joint_df: pl.DataFrame,
        mk: Any,
        matched_pairs: set[tuple[int, int]],
        chunk_lo: int,
        chunk_hi: int,
    ) -> list[tuple[int, int, float]]:
        """Cross-chunk weighted matching with block-keyed index lookup.

        For ``strategy="static"`` blocking, takes the bucketed
        ``_index_by_block`` path: per blocking-key config, group the
        chunk by block key, look up each block in the index dict,
        score only mixed (chunk × index) blocks via
        ``score_blocks_parallel`` with ``target_ids=chunk_ids``. Pure-
        index blocks are never instantiated.

        For other strategies, falls back to B's behavior: ``build_blocks``
        over the joint frame, score, post-filter to cross-pairs.

        Algorithmically, the static path turns the cross-chunk work per
        chunk from O(joint_size × avg_block) into O(sum over chunk
        blocks K of |chunk[K]| × |index[K]|). Pure-index blocks
        contribute zero work instead of full block² scoring.
        """
        from goldenmatch.core.blocker import BlockResult, build_blocks
        from goldenmatch.core.scorer import score_blocks_parallel

        blocking = self.config.blocking
        assert blocking is not None

        is_static = (
            blocking.strategy == "static"
            and bool(blocking.keys)
            and self._index_by_block is not None
        )
        if not is_static:
            # Fallback: B-style joint blocking + post-filter to cross-pairs.
            blocks = build_blocks(joint_df.lazy(), blocking)
            return [
                (a, b, s)
                for a, b, s in score_blocks_parallel(blocks, mk, matched_pairs)
                if (chunk_lo <= a < chunk_hi) != (chunk_lo <= b < chunk_hi)
            ]

        # Static-blocking fast path: per-key bucket lookup.
        chunk_ids: set[int] = set(range(chunk_lo, chunk_hi))
        synthetic_blocks: list[BlockResult] = []
        seen_block_keys: set[str] = set()
        assert self._index_by_block is not None  # narrow for type-checker
        for key_idx, key_config in enumerate(blocking.keys or []):
            chunk_keyed = self._block_key_column(chunk_df, key_config)
            index_dict = self._index_by_block[key_idx]
            # partition_by(single col, as_dict=True) returns dict[key_value,
            # df_without_partition_col]. Polars 1.x returns the value as a
            # tuple when as_dict is used — handle both shapes defensively.
            partitions = chunk_keyed.partition_by(
                "__block_key__", as_dict=True, include_key=False,
            )
            for raw_key, chunk_subset in partitions.items():
                bk = raw_key[0] if isinstance(raw_key, tuple) else raw_key
                index_subset = index_dict.get(bk)
                if index_subset is None or index_subset.height == 0:
                    continue
                # Don't re-emit the same block-key bucket twice if two
                # blocking-key configs collide on the same string.
                dedup_key = f"{key_idx}::{bk}"
                if dedup_key in seen_block_keys:
                    continue
                seen_block_keys.add(dedup_key)
                # Slim down chunk_subset to the same columns as index_subset,
                # then stack. compute_matchkeys was already applied on the
                # joint frame upstream; re-stacking slim frames is cheap.
                cols = [c for c in index_subset.columns if c in chunk_subset.columns]
                combined = pl.concat(
                    [chunk_subset.select(cols), index_subset.select(cols)],
                    how="vertical",
                )
                synthetic_blocks.append(
                    BlockResult(block_key=bk, df=combined.lazy(), strategy="static"),
                )

        if not synthetic_blocks:
            return []

        return list(score_blocks_parallel(
            synthetic_blocks, mk, matched_pairs, target_ids=chunk_ids,
        ))

    def _add_to_index(self, chunk_df: pl.DataFrame) -> None:
        """Append the chunk's slim slice to the persistent index frame.

        Slim = ``__row_id__`` + matchkey fields + blocking-key fields.
        Stored as a Polars DataFrame (was ``list[dict]``) so the
        cross-chunk match step can vectorize via ``pl.concat`` +
        ``compute_matchkeys`` + ``find_exact_matches`` /
        ``score_blocks_parallel`` instead of Python double-loops.
        """
        matchkeys = self.config.get_matchkeys()
        keep_cols = self._slim_columns(matchkeys)
        available = [c for c in keep_cols if c in chunk_df.columns]
        slim_df = chunk_df.select(available)
        if self._index_df is None:
            self._index_df = slim_df
        else:
            self._index_df = pl.concat([self._index_df, slim_df], how="vertical")

        # If blocking is static, also partition the slim slice by each
        # blocking key and append into the bucketed index. The bucketed
        # index lets cross-chunk matching look up only the relevant
        # block per chunk-row instead of building blocks over the full
        # joint frame each iteration.
        blocking = self.config.blocking
        if (
            blocking is not None
            and blocking.strategy == "static"
            and blocking.keys
        ):
            if self._index_by_block is None:
                self._index_by_block = [dict() for _ in blocking.keys]
            for key_idx, key_config in enumerate(blocking.keys):
                slim_keyed = self._block_key_column(slim_df, key_config)
                partitions = slim_keyed.partition_by(
                    "__block_key__", as_dict=True, include_key=False,
                )
                bucket = self._index_by_block[key_idx]
                for raw_key, part_df in partitions.items():
                    bk = raw_key[0] if isinstance(raw_key, tuple) else raw_key
                    existing = bucket.get(bk)
                    if existing is None:
                        bucket[bk] = part_df
                    else:
                        bucket[bk] = pl.concat([existing, part_df], how="vertical")
