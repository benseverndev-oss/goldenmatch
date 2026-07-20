"""Out-of-core Fellegi-Sunter block scoring — stream block groups from a
DuckDB-resident prepared table through the native FS kernel with BOUNDED memory,
instead of holding the whole frame + all bucket partitions resident.

**Why this exists (the FS scale gap).** The default FS route (`score_buckets`)
is memory-bounded *per bucket* but still materializes the full prepared frame +
its `partition_by` buckets in the driver, so peak RSS is linear in N
(~0.95 GB + 1.3 GB/M measured) — a hard single-box wall at ~45M on 64 GB. And
`_fs_use_bucket_route` hands `backend=duckdb/ray/chunked` to a *single-node*
legacy scorer, so FS has NO out-of-core or distributed path today (the
scale-envelope doc's duckdb/ray tiers are weighted-path only). This module is
the first out-of-core FS scorer: the prepared records live in DuckDB (on disk
when `db_path` is a file), and blocks are pulled ONE GROUP AT A TIME, scored,
and discarded, so the SCORING phase is bounded (peak = one block group).

**LIMITATION - does NOT yet break the single-box wall (~40M, CI-measured).** The
frame is loaded into DuckDB here via ``frame -> to_arrow() -> CREATE TABLE``,
which materialises ~2-3x the frame IN RAM at load time; only AFTER load does
scoring stream bounded. So this makes the SCORER out-of-core, but the LOAD peak
is still ~frame-sized. Breaking the wall needs **Phase 2**: write the prepared
frame to DuckDB/parquet in streaming batches DURING prep (or read the input
parquet directly), so the frame never fully materialises - see
`docs/superpowers/specs/2026-07-20-fs-frame-residency-bucket-streaming-design.md`.
This module is the scoring half Phase 2 feeds from a disk-resident table.

**Parity.** Block membership is derived with the SAME `_build_block_key_expr` +
null/sentinel key filter + `multi_pass` `(pass_sig, block_key)` semantics as
`build_blocks`/`score_buckets`, and each block is scored by the SAME
`score_probabilistic_bucket_native` kernel, so the emitted pair set is identical
to `score_buckets` — ABSENT oversized blocks (a block over `max_block_size`:
`score_buckets` auto-splits, this scores it whole up to `max_block_rows`; a
bench-gated edge, exact parity where no block exceeds the cap). Cross-pass
duplicate pairs are deduped canonically in pass order, matching
`score_probabilistic_external_blocks`.

Supports `static`/`multi_pass` blocking (what FS auto-config emits, incl. the SN
bound → static passes). Raises `NotImplementedError` otherwise so callers can
fall back to `score_buckets`.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any

from goldenmatch.config.schemas import BlockingConfig, MatchkeyConfig


def fs_out_of_core_enabled() -> bool:
    """Opt-in scale switch for the out-of-core FS path (default OFF).

    `GOLDENMATCH_FS_OUT_OF_CORE=1` routes the FS bucket scorer through
    `score_fs_out_of_core` with a disk-resident prepared table instead of the
    in-memory `score_buckets` — the separate, opt-in scale option for datasets
    past the ~40M single-box wall. Off by default: byte-identical to today for
    every existing run."""
    return os.environ.get("GOLDENMATCH_FS_OUT_OF_CORE", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _resolve_db_path(db_path: str | None) -> str:
    """None → in-memory DuckDB (tests / small frames). ``"auto"`` → a tempfile
    that spills the prepared table to DISK, so post-load resident memory drops
    to the DuckDB buffer cache rather than the frame (the scale path)."""
    if db_path == "auto":
        fd, path = tempfile.mkstemp(prefix="gm_fs_ooc_", suffix=".duckdb")
        os.close(fd)
        os.unlink(path)  # DuckDB creates the file itself
        return path
    return db_path or ":memory:"


def _load_frame_batched(con, proj, batch_rows: int = 500_000) -> None:
    """Load a polars/arrow frame into DuckDB table ``prep`` in row-slice batches
    — slice → Arrow → append → free — so peak stays ~1× the frame instead of the
    full ``to_arrow()`` copy (~2-3×). ``slice`` is a zero-copy view; only one
    batch's Arrow buffer is live at a time on top of the resident frame."""
    import polars as pl

    pf = proj if isinstance(proj, pl.DataFrame) else pl.from_arrow(proj)
    n = pf.height
    off = 0
    created = False
    while off < n:
        sl = pf.slice(off, batch_rows).to_arrow()
        con.register("_gm_batch", sl)
        if not created:
            con.execute("CREATE TABLE prep AS SELECT * FROM _gm_batch")
            created = True
        else:
            con.execute("INSERT INTO prep SELECT * FROM _gm_batch")
        con.unregister("_gm_batch")
        off += sl.num_rows
        del sl
    if not created:  # empty frame — create the (empty) table from the schema
        con.register("_gm_batch", pf.head(0).to_arrow())
        con.execute("CREATE TABLE prep AS SELECT * FROM _gm_batch")
        con.unregister("_gm_batch")


def _needed_columns(prepared_native, mk: MatchkeyConfig, blocking: BlockingConfig) -> list[str]:
    """Columns the FS kernel + block-key derivation read: __row_id__/__source__,
    the matchkey fields (raw — the probabilistic scorer transforms internally),
    their __xform_* columns, NE fields, and every blocking group column."""
    from goldenmatch.core.blocker import collect_blocking_fields

    names = list(
        getattr(prepared_native, "column_names", None) or prepared_native.columns
    )
    keep: list[str] = []

    def _add(c: str) -> None:
        if c in names and c not in keep:
            keep.append(c)

    _add("__row_id__")
    _add("__source__")
    for c in names:
        if c.startswith("__xform_"):
            keep.append(c)
    for f in mk.fields or []:
        if getattr(f, "field", None):
            _add(f.field)
    for ne in (getattr(mk, "negative_evidence", None) or []):
        if getattr(ne, "field", None):
            _add(ne.field)
        for src in (getattr(ne, "derive_from", None) or []):
            _add(src)
    for col in collect_blocking_fields(blocking) if blocking else []:
        _add(col)
    return keep


def score_fs_out_of_core(
    prepared_df: Any,
    blocking_config: BlockingConfig,
    mk: MatchkeyConfig,
    matched_pairs: set[tuple[int, int]],
    em_result,
    *,
    target_ids: set[int] | None = None,
    db_path: str | None = None,
    max_block_rows: int | None = None,
) -> list[tuple[int, int, float]]:
    """Score FS blocks out-of-core from a DuckDB-resident prepared table.

    ``db_path=None`` → in-memory DuckDB (the frame is loaded once, then blocks
    stream from it — bounded downstream, but the load itself is resident; a file
    path spills the table to disk). Returns ``list[(a, b, score)]``.
    """
    import duckdb
    import polars as pl
    import pyarrow as pa

    from goldenmatch.core.blocker import _build_block_key_expr
    from goldenmatch.core.frame import (
        is_polars_dataframe,
        is_polars_lazyframe,
        to_frame as _tf,
    )
    from goldenmatch.core.probabilistic import (
        _fs_native_eligible,
        probabilistic_block_scorer,
        score_probabilistic_bucket_native,
    )

    if blocking_config.strategy not in ("static", "multi_pass"):
        raise NotImplementedError(
            f"score_fs_out_of_core supports static/multi_pass, not "
            f"{blocking_config.strategy!r}"
        )
    if em_result is None:
        raise ValueError("score_fs_out_of_core requires a trained em_result")

    max_block_size = blocking_config.max_block_size
    if max_block_rows is None:
        max_block_rows = max_block_size

    native = _tf(prepared_df).native
    if is_polars_lazyframe(native):
        native = native.collect()
    keep = _needed_columns(native, mk, blocking_config)
    # Project to the scoring columns (dead columns never reach disk). `.select`
    # shares column buffers with `native`, so this is ~free.
    proj = native.select(keep)

    _resolved_path = _resolve_db_path(db_path)
    con = duckdb.connect(_resolved_path)
    try:
        # BATCHED load: slice → Arrow → append → free, so peak stays ~1x the
        # frame (never the full `to_arrow()` copy that made this ~2-3x). With a
        # file db_path the table spills to disk, so post-load resident drops to
        # the DuckDB buffer cache, not the frame. This is the Phase-2 memory fix.
        _load_frame_batched(con, proj)
        del proj, native
        con.execute("CREATE INDEX ix_rid ON prep(__row_id__)")

        # Choose the FS scorer once (native kernel vs vectorized), like score_buckets.
        use_native = _fs_native_eligible(mk)
        prob_scorer = None if use_native else probabilistic_block_scorer(mk, em_result)
        frozen_exclude = frozenset(matched_pairs)

        passes = (
            list(blocking_config.passes or [])
            if blocking_config.strategy == "multi_pass"
            else list(blocking_config.keys or [])
        )

        out: list[tuple[int, int, float]] = []
        seen: set[tuple[int, int]] = set()

        def _score_block(block_pl) -> None:
            if block_pl.height < 2:
                return
            if use_native:
                pairs = score_probabilistic_bucket_native(
                    block_pl, [block_pl.height], mk, em_result, frozen_exclude,
                )
            else:
                pairs = prob_scorer(block_pl, frozen_exclude)
            for a, b, s in pairs:
                if target_ids is not None and ((a in target_ids) == (b in target_ids)):
                    continue
                key = (a, b) if a < b else (b, a)
                if key in seen:
                    continue
                seen.add(key)
                out.append((a, b, s))

        for pass_config in passes:
            # 1) Thin-index grouping: pull ONLY __row_id__ + this pass's blocking
            #    columns (not the scoring frame), derive the block key exactly as
            #    build_blocks does, and assign each valid block (>=2 rows, capped
            #    at max_block_rows) a sequential id -> a flat (row_id, __blk__) map.
            key_expr = _build_block_key_expr(pass_config)
            _key_cols = ["__row_id__"] + [
                f for f in dict.fromkeys(pass_config.fields) if f != "__row_id__"
            ]
            _key_sel = ", ".join(f'"{c}"' for c in _key_cols)
            keyed = con.execute(f"SELECT {_key_sel} FROM prep").pl()
            grouped = (
                keyed.lazy()
                .with_columns(key_expr)
                .filter(
                    pl.col("__block_key__").is_not_null()
                    & ~pl.col("__block_key__")
                        .str.strip_chars()
                        .str.to_lowercase()
                        .is_in(["nan", "null", "none"])
                )
                .group_by("__block_key__")
                .agg(pl.col("__row_id__"))
                .collect()
            )
            del keyed

            rids: list[int] = []
            seqs: list[int] = []
            seq = 0
            for ids in grouped["__row_id__"].to_list():
                if ids is None or len(ids) < 2:
                    continue
                capped = ids[:max_block_rows] if len(ids) > max_block_rows else ids
                rids.extend(int(r) for r in capped)
                seqs.extend([seq] * len(capped))
                seq += 1
            del grouped
            if not rids:
                continue

            # 2) One sorted scan: JOIN prep to the (row_id, __blk__) map and
            #    ORDER BY __blk__ (DuckDB's external sort spills to disk), then
            #    STREAM the result in Arrow batches. n_passes scans total, NOT
            #    one query per block (that was O(n_blocks) round-trips -> timed
            #    out at 1M). Peak = one batch + one carried block.
            map_tbl = pa.table(
                {
                    "__row_id__": pa.array(rids, pa.int64()),
                    "__blk__": pa.array(seqs, pa.int64()),
                }
            )
            con.register("blkmap_arrow", map_tbl)
            con.execute("CREATE OR REPLACE TEMP TABLE blkmap AS SELECT * FROM blkmap_arrow")
            con.unregister("blkmap_arrow")
            del map_tbl, rids, seqs

            reader = con.execute(
                "SELECT p.*, m.__blk__ AS __blk__ FROM prep p "
                "JOIN blkmap m ON p.__row_id__ = m.__row_id__ ORDER BY m.__blk__"
            ).fetch_record_batch(1 << 16)

            # 3) Split the sorted stream into blocks by __blk__ runs. partition_by
            #    keeps row order; the LAST partition of each batch may continue in
            #    the next, so carry it forward and prepend.
            carry = None
            for batch in reader:
                bpl = pl.from_arrow(pa.Table.from_batches([batch]))
                if carry is not None:
                    bpl = pl.concat([carry, bpl])
                    carry = None
                parts = bpl.partition_by("__blk__", maintain_order=True)
                for p in parts[:-1]:
                    _score_block(p.drop("__blk__"))
                carry = parts[-1] if parts else None
            if carry is not None:
                _score_block(carry.drop("__blk__"))
            con.execute("DROP TABLE IF EXISTS blkmap")
        return out
    finally:
        con.close()
        # Clean up the spilled tempfile (+ DuckDB's -wal sidecar) when we minted
        # it via db_path="auto"; a caller-supplied path is left for them to own.
        if db_path == "auto":
            for p in (_resolved_path, _resolved_path + ".wal"):
                try:
                    if os.path.exists(p):
                        os.unlink(p)
                except OSError:
                    pass
