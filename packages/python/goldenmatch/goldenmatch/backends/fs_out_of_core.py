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

**End-to-end path: `run_fs_dedupe_streaming`.** Ties the two bounded mechanisms
together for single-box scale: prep frame -> DuckDB FILE (batched load,
`_load_frame_batched` keeps the load peak ~1x the frame, not the ~2-3x a full
`to_arrow()` copy would), FREE the frame, score from the store
(`score_fs_out_of_core`), cluster, then STREAM unique/dupes/golden to parquet
(`stream_fs_dedupe_output`, O(N) output via DuckDB `COPY`, never a result frame).
So peak stays ~1x the prepared frame (only during the load) instead of the
in-memory ~1.65 GB/M accumulation -- e.g. 50M is ~1x frame (~15-25 GB, FITS
64 GB) vs the in-memory ~82 GB OOM. Load-peak reduction below ~1x frame (stream
input parquet -> DuckDB during prep, never materialise it) + the CI proof that
50M completes are the remaining work. See
`docs/superpowers/specs/2026-07-20-fs-frame-residency-bucket-streaming-design.md`.

**Parity.** Block membership is derived with the SAME `_build_block_key_expr` +
null/sentinel key filter + `multi_pass` `(pass_sig, block_key)` semantics as
`build_blocks`/`score_buckets`, and each block is scored by the SAME
`score_probabilistic_bucket_native` kernel, so the emitted pair set is identical
to `score_buckets` — ABSENT oversized blocks (a block over `max_block_size`:
`score_buckets` auto-splits, this scores it whole up to `max_block_rows`; a
bench-gated edge, exact parity where no block exceeds the cap). Cross-pass
duplicate pairs are deduped canonically in pass order, matching
`score_probabilistic_external_blocks`.

Supports `static`/`multi_pass` blocking (what FS auto-config emits). Raises
`NotImplementedError` otherwise (e.g. `sorted_neighborhood`) so callers can fall
back to `score_buckets`.
"""
from __future__ import annotations

import concurrent.futures
import os
import tempfile
from collections.abc import Sequence
from typing import Any

from goldenmatch.config.schemas import BlockingConfig, MatchkeyConfig


def _sql_lit(s: Any) -> str:
    """A SQL single-quoted string literal with quote-doubling, so a path that
    contains a single quote can't break (or inject into) a DuckDB ``COPY ... TO``
    statement built via f-string. Used for the caller-supplied output paths."""
    return "'" + str(s).replace("'", "''") + "'"


def _sql_ident(name: Any) -> str:
    """A double-quoted SQL identifier with quote-doubling, so a data-derived
    column name that contains a double quote can't break (or inject into) an
    f-string-built ``SELECT``/``COPY`` column list. Matches the connector
    quoting convention used elsewhere in the repo."""
    return '"' + str(name).replace('"', '""') + '"'


def _fs_ooc_workers() -> int:
    """Thread-pool size for out-of-core block scoring. Mirrors the in-memory FS
    scorer's ``GOLDENMATCH_FS_WORKERS`` (default ``min(16, cpu)``); the native
    kernel + numpy scorer release the GIL, so threads give real parallelism."""
    v = os.environ.get("GOLDENMATCH_FS_WORKERS")
    if v and v.strip().isdigit() and int(v) > 0:
        return int(v)
    return min(16, (os.cpu_count() or 4))


def _fs_ooc_wave_rows() -> int:
    """Max resident block-rows scored per parallel wave (bounds peak: one wave of
    buffered blocks + their pair results, not the whole pass). ``0``/invalid ->
    the 2M default."""
    v = os.environ.get("GOLDENMATCH_FS_OOC_WAVE_ROWS")
    if v and v.strip().isdigit() and int(v) > 0:
        return int(v)
    return 2_000_000


def _ooc_debug_on() -> bool:
    """``GOLDENMATCH_FS_OOC_DEBUG=1`` prints a per-phase progress line for the
    out-of-core FS scorer (load, and per blocking pass: block-map build +
    scan+score wall + block count). Off by default, output-invariant. Gives the
    long-running >=25M streaming leg live progress instead of a blank spinner."""
    return os.environ.get("GOLDENMATCH_FS_OOC_DEBUG", "0").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _fs_out_of_core_legacy_flag() -> bool:
    """The legacy standalone opt-in ``GOLDENMATCH_FS_OUT_OF_CORE=1``. Retained as
    an alias for ``GOLDENMATCH_FS_BLOCK_SOURCE=duckdb`` (see
    ``resolve_fs_block_source``) so existing runs/benches keep working."""
    return os.environ.get("GOLDENMATCH_FS_OUT_OF_CORE", "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def resolve_fs_block_source() -> str:
    """Single resolver for HOW the FS (probabilistic) bucket route sources its
    blocks — the one knob that governs both bounded-streaming lanes so callers
    (and users) reason about one env var, not two.

    Returns one of:
      - ``"eager"``  — today's default: materialize the slim frame + eager
        ``partition_by`` into all ``n_buckets`` (byte-identical to pre-streaming).
      - ``"frame"``  — in-RAM bounded streaming (the spec's ``FrameBlockSource``):
        slice one bucket off the keyed frame on demand inside ``score_buckets``,
        holding only ``max_workers`` slices live. Lower peak, same box.
      - ``"sequential"`` — in-RAM sequential Arrow-native / Rust batch scoring
        (``score_fs_sequential_arrow``): block-key group in polars/Arrow, gather
        each block, stream through the Rust kernel to an Arrow edge stream, then
        ONE Rust WCC stitch at the end (``run_fs_dedupe_sequential``). No DuckDB,
        no on-disk sorted scans — the fast pure-Arrow path while the frame fits
        RAM.
      - ``"duckdb"`` — out-of-core: spill the prepared table to a DuckDB file and
        stream blocks + O(N) output (``score_fs_out_of_core`` /
        ``run_fs_dedupe_streaming``). The ≥40M single-box scale path.

    Resolution (``GOLDENMATCH_FS_BLOCK_SOURCE`` is authoritative when set to a
    recognized value; the legacy ``GOLDENMATCH_FS_OUT_OF_CORE=1`` is honored only
    when ``GOLDENMATCH_FS_BLOCK_SOURCE`` is unset/empty):
      - ``FS_BLOCK_SOURCE=frame`` → ``"frame"``
      - ``FS_BLOCK_SOURCE=sequential`` → ``"sequential"``
      - ``FS_BLOCK_SOURCE=duckdb`` → ``"duckdb"``
      - ``FS_BLOCK_SOURCE=eager`` / ``auto`` / unset → ``"eager"`` (+ legacy alias)

    ``auto`` currently resolves to ``eager`` — the measured RAM-floor
    auto-escalation to ``sequential``/``duckdb`` is the documented Phase-1
    follow-on; until it lands (and the default is flipped on a CI scale gate)
    ``auto`` stays byte-identical to today."""
    v = os.environ.get("GOLDENMATCH_FS_BLOCK_SOURCE", "").strip().lower()
    if v == "frame":
        return "frame"
    if v == "sequential":
        return "sequential"
    if v == "duckdb":
        return "duckdb"
    if v in ("", "auto", "eager"):
        # Unset/auto/eager: honor the legacy standalone flag, else eager.
        return "duckdb" if _fs_out_of_core_legacy_flag() else "eager"
    # Unrecognized value: fail safe to eager (never silently pick a scale path),
    # but still honor the legacy flag so it can't be masked by a typo'd source.
    return "duckdb" if _fs_out_of_core_legacy_flag() else "eager"


def fs_out_of_core_enabled() -> bool:
    """Whether the FS bucket scorer routes through the OUT-OF-CORE DuckDB path
    (``score_fs_out_of_core`` / ``run_fs_dedupe_streaming``) instead of the
    in-memory ``score_buckets`` — the opt-in scale option for datasets past the
    ~40M single-box wall. Default OFF (byte-identical to today).

    Now a thin view over ``resolve_fs_block_source`` so the two historical env
    vars (``GOLDENMATCH_FS_OUT_OF_CORE=1`` and ``GOLDENMATCH_FS_BLOCK_SOURCE=duckdb``)
    are one decision, not two independent booleans."""
    return resolve_fs_block_source() == "duckdb"


def fs_streaming_route() -> str | None:
    """Which END-TO-END streaming FS dedupe orchestrator (score → WCC → parquet)
    the current env selects, or ``None`` for the default in-memory pipeline.

      - ``"sequential"`` → ``run_fs_dedupe_sequential`` (in-RAM Arrow/Rust batches,
        ``GOLDENMATCH_FS_BLOCK_SOURCE=sequential``).
      - ``"duckdb"`` → ``run_fs_dedupe_streaming`` (out-of-core DuckDB spill,
        ``GOLDENMATCH_FS_OUT_OF_CORE=1`` / ``GOLDENMATCH_FS_BLOCK_SOURCE=duckdb``).

    ``eager``/``frame`` are NOT end-to-end streaming routes (``frame`` only changes
    ``score_buckets``'s in-bucket residency), so they return ``None`` and the
    caller keeps the default pipeline."""
    src = resolve_fs_block_source()
    return src if src in ("sequential", "duckdb") else None


def _fs_ooc_arrow_cluster_enabled() -> bool:
    """Arrow-native pair stream + Rust clustering for the out-of-core streaming FS
    path (default ON within the already-opt-in out-of-core route).

    When on, ``run_fs_dedupe_streaming`` scores into a ``PAIR_STREAM`` ``pa.Table``
    (never a ``list[tuple]``), dedups it with the native ``dedup_pairs_arrow``
    kernel, and clusters with ``build_clusters_arrow_native`` (Rust Union-Find via
    the C Data Interface) — so the scored pairs never enter Python as objects and
    the Union-Find never builds a ``dict[int, dict]``.
    ``GOLDENMATCH_FS_OOC_ARROW_CLUSTER=0`` restores the ``list[tuple]`` + Python
    ``build_clusters`` path (the rollback lever). Distinct from the in-memory
    ``GOLDENMATCH_FS_ARROW_STREAM`` (which gates ``score_buckets``'s per-bucket
    Arrow accumulation, a different code path)."""
    v = os.environ.get("GOLDENMATCH_FS_OOC_ARROW_CLUSTER")
    if v is None:
        return True
    return v.strip().lower() in ("1", "true", "yes", "on")


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
    emit: str = "tuples",
) -> Any:
    """Score FS blocks out-of-core from a DuckDB-resident prepared table.

    ``db_path=None`` → in-memory DuckDB (the frame is loaded once, then blocks
    stream from it — bounded downstream, but the load itself is resident; a file
    path spills the table to disk).

    ``emit="tuples"`` (default) returns ``list[(a, b, score)]`` with the
    cross-pass first-seen canonical dedup — byte-identical to the reference and
    the shape ``_score_probabilistic_matchkey`` consumes.

    ``emit="arrow"`` returns a ``PAIR_STREAM_SCHEMA`` ``pa.Table``
    (``id_a``/``id_b`` int64, ``score`` float64) so the scored pairs NEVER
    accumulate as Python objects across the run: each WAVE's kernel output is
    converted to Arrow immediately (via ``pairs_to_pair_stream``) and appended,
    and the cross-pass ``seen`` set is DROPPED — the downstream Rust Union-Find
    (``build_clusters_arrow_native``) collapses duplicate edges, so the dedup is
    a memory optimisation, not correctness (the caller runs
    ``dedup_pairs_max_score_arrow`` on the concatenated stream if it wants a
    deduped pair count). This is the path that keeps 50M+ off the ~16 GB
    ``list[tuple]`` Python-object floor. ``target_ids`` still applies per wave.
    """
    import duckdb
    import polars as pl
    import pyarrow as pa

    from goldenmatch.core.blocker import _build_block_key_expr
    from goldenmatch.core.frame import (
        is_polars_lazyframe,
    )
    from goldenmatch.core.frame import (
        to_frame as _tf,
    )
    from goldenmatch.core.probabilistic import (
        _fs_native_eligible,
        _fs_vectorized_enabled,
        _fs_vectorized_supported,
        probabilistic_block_scorer,
        score_probabilistic_bucket_native,
        score_probabilistic_vectorized_batch,
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
        import time as _time
        _dbg = _ooc_debug_on()
        _t_load = _time.perf_counter()
        _load_frame_batched(con, proj)
        del proj, native
        con.execute("CREATE INDEX ix_rid ON prep(__row_id__)")
        if _dbg:
            print(f"[fs-ooc] load+index {_time.perf_counter()-_t_load:.1f}s",
                  flush=True)

        # Choose the FS scorer once (native kernel vs vectorized), like score_buckets.
        use_native = _fs_native_eligible(mk)
        prob_scorer = None if use_native else probabilistic_block_scorer(mk, em_result)
        frozen_exclude = frozenset(matched_pairs)

        passes = (
            list(blocking_config.passes or [])
            if blocking_config.strategy == "multi_pass"
            else list(blocking_config.keys or [])
        )

        _arrow = emit == "arrow"
        out: list[tuple[int, int, float]] = []
        seen: set[tuple[int, int]] = set()
        pair_tables: list = []  # arrow mode: one PAIR_STREAM pa.Table per wave
        if _arrow:
            from goldenmatch.backends.score_buckets import pairs_to_pair_stream

        # Score blocks in PARALLEL across a bounded wave, mirroring the in-memory
        # `score_buckets` ThreadPoolExecutor -- the native FS kernel (and the
        # numpy `prob_scorer`) release the GIL, so N cores give ~Nx on the
        # scoring phase. `_score_one` is a PURE function (no shared state); the
        # per-pair target/dedup merge stays single-threaded and IN BLOCK ORDER,
        # so the emitted pair set + cross-pass first-seen-wins semantics are
        # byte-identical to the serial path.
        _workers = _fs_ooc_workers()
        wave_rows = _fs_ooc_wave_rows()  # cap resident block-rows per wave
        # Batch the wave into FEW scorer calls instead of one-per-block. Person
        # data makes tens of thousands of tiny blocks per pass; one native call
        # per block was ~60s/pass (the measured OOC wall). The native kernel
        # isolates blocks by a `size_list` (one call scores a whole
        # block-contiguous frame), and the numpy vectorized scorer amortizes one
        # SxS matrix per field across a batch of blocks -- exactly the in-memory
        # `_score_one_bucket` batched-native call + `score_probabilistic_blocks_
        # batched`. Byte-identical pair set + scores (blocks are independent; the
        # size_list / per-block spans isolate them), so the existing parity tests
        # against the per-block reference gate this.
        _use_vec_batch = (
            not use_native
            and _fs_vectorized_enabled()
            and _fs_vectorized_supported(mk)
        )

        def _score_one(block_pl):
            # Per-block fallback (unsupported scorer path).
            if block_pl.height < 2:
                return ()
            if use_native:
                return score_probabilistic_bucket_native(
                    block_pl, [block_pl.height], mk, em_result, frozen_exclude,
                )
            return prob_scorer(block_pl, frozen_exclude)

        def _score_native_chunk(blocks):
            # ONE native call for a group of block-frames: concat them
            # block-contiguous + pass their per-block sizes. The kernel scores
            # WITHIN each block only, so this is byte-identical to scoring the
            # blocks one at a time. OOC blocks are capped at max_block_rows and
            # are all >=2 rows (the mapping filters), so no oversized handling.
            if not blocks:
                return ()
            frame = blocks[0] if len(blocks) == 1 else pl.concat(blocks)
            return score_probabilistic_bucket_native(
                frame, [b.height for b in blocks], mk, em_result, frozen_exclude,
            )

        def _chunk_blocks(blocks, k):
            # Partition block-frames into <= k order-preserving groups of ~equal
            # rows so the thread pool still parallelizes (one native call each).
            if k <= 1 or len(blocks) <= 1:
                return [blocks]
            target = max(1, sum(b.height for b in blocks) // k)
            groups: list = []
            cur: list = []
            cur_rows = 0
            for b in blocks:
                cur.append(b)
                cur_rows += b.height
                if cur_rows >= target and len(groups) < k - 1:
                    groups.append(cur)
                    cur, cur_rows = [], 0
            if cur:
                groups.append(cur)
            return groups

        def _merge(results) -> None:
            # results: per-block pair lists, IN submission (block) order.
            if _arrow:
                # Convert THIS wave's pairs to one Arrow table and drop the
                # Python tuples -- pairs never accumulate as objects across the
                # run. No `seen` dedup (Union-Find collapses dup edges); only the
                # `target_ids` membership filter (match-across-files) is kept.
                wave: list[tuple[int, int, float]] = []
                for pairs in results:
                    for a, b, s in pairs:
                        if target_ids is not None and (
                            (a in target_ids) == (b in target_ids)
                        ):
                            continue
                        wave.append((a, b, s))
                if wave:
                    pair_tables.append(pairs_to_pair_stream(wave))
                return
            for pairs in results:
                for a, b, s in pairs:
                    if target_ids is not None and (
                        (a in target_ids) == (b in target_ids)
                    ):
                        continue
                    key = (a, b) if a < b else (b, a)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append((a, b, s))

        with concurrent.futures.ThreadPoolExecutor(max_workers=_workers) as _ex:
            buf: list = []
            buf_rows = 0

            def _flush() -> None:
                nonlocal buf, buf_rows
                if not buf:
                    return
                if use_native:
                    # One native call per ~worker-sized chunk (executor.map
                    # preserves order -> block-order merge).
                    _merge(_ex.map(_score_native_chunk, _chunk_blocks(buf, _workers)))
                elif _use_vec_batch:
                    # One SxS batch over the whole wave (numpy vectorized path).
                    _merge((
                        score_probabilistic_vectorized_batch(
                            buf, mk, em_result, frozen_exclude
                        ),
                    ))
                else:
                    # Unsupported scorer: per-block fallback (order preserved).
                    _merge(_ex.map(_score_one, buf))
                buf = []
                buf_rows = 0

            for pass_config in passes:
                if _dbg:
                    _t_pass = _time.perf_counter()
                # 1) Thin-index grouping: pull ONLY __row_id__ + this pass's
                #    blocking columns (not the scoring frame), derive the block key
                #    exactly as build_blocks does, and assign each valid block
                #    (>=2 rows, capped at max_block_rows) a sequential id -> a flat
                #    (row_id, __blk__) map. The map is built VECTORISED in polars
                #    (filter/head/with_row_index/explode) -- no per-row Python.
                key_expr = _build_block_key_expr(pass_config)
                _key_cols = ["__row_id__"] + [
                    f for f in dict.fromkeys(pass_config.fields) if f != "__row_id__"
                ]
                _key_sel = ", ".join(_sql_ident(c) for c in _key_cols)
                keyed = con.execute(f"SELECT {_key_sel} FROM prep").pl()
                mapping = (
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
                    .filter(pl.col("__row_id__").list.len() >= 2)
                    .with_columns(pl.col("__row_id__").list.head(max_block_rows))
                    .with_row_index("__blk__")
                    .select("__blk__", "__row_id__")
                    .explode("__row_id__")
                    .select(
                        pl.col("__row_id__").cast(pl.Int64),
                        pl.col("__blk__").cast(pl.Int64),
                    )
                    .collect()
                )
                del keyed
                if _dbg:
                    _map_dt = _time.perf_counter() - _t_pass
                    _map_h = mapping.height
                    _t_scan = _time.perf_counter()
                if mapping.height == 0:
                    continue

                # 2) One sorted scan: JOIN prep to the (row_id, __blk__) map and
                #    ORDER BY __blk__ (DuckDB's external sort spills to disk), then
                #    STREAM the result in Arrow batches. n_passes scans total, NOT
                #    one query per block (that was O(n_blocks) round-trips -> timed
                #    out at 1M). Peak = one batch + one wave of buffered blocks.
                con.register("blkmap_arrow", mapping.to_arrow())
                con.execute(
                    "CREATE OR REPLACE TEMP TABLE blkmap AS SELECT * FROM blkmap_arrow"
                )
                con.unregister("blkmap_arrow")
                del mapping

                reader = con.execute(
                    "SELECT p.*, m.__blk__ AS __blk__ FROM prep p "
                    "JOIN blkmap m ON p.__row_id__ = m.__row_id__ ORDER BY m.__blk__"
                ).to_arrow_reader(1 << 16)

                # 3) Split the sorted stream into blocks by __blk__ runs, buffer
                #    them, and score each WAVE in parallel. partition_by keeps row
                #    order; the LAST partition of each batch may continue in the
                #    next, so carry it forward and prepend.
                carry = None
                for batch in reader:
                    bpl = pl.from_arrow(pa.Table.from_batches([batch]))
                    if carry is not None:
                        bpl = pl.concat([carry, bpl])
                        carry = None
                    parts = bpl.partition_by("__blk__", maintain_order=True)
                    for p in parts[:-1]:
                        blk = p.drop("__blk__")
                        buf.append(blk)
                        buf_rows += blk.height
                        if buf_rows >= wave_rows:
                            _flush()
                    carry = parts[-1] if parts else None
                if carry is not None:
                    blk = carry.drop("__blk__")
                    buf.append(blk)
                    buf_rows += blk.height
                # Flush at the END of each pass so pass N's blocks are all merged
                # before pass N+1 -> preserves the pass-order first-seen dedup.
                _flush()
                con.execute("DROP TABLE IF EXISTS blkmap")
                if _dbg:
                    print(f"[fs-ooc] pass [{','.join(pass_config.fields)}] "
                          f"blockrows={_map_h} map {_map_dt:.1f}s "
                          f"scan+score {_time.perf_counter()-_t_scan:.1f}s",
                          flush=True)
        if _arrow:
            if pair_tables:
                return pa.concat_tables(pair_tables)
            from goldenmatch.backends.score_buckets import pairs_to_pair_stream
            return pairs_to_pair_stream([])
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
                    # Best-effort cleanup: a temp DB/WAL unlink failure is
                    # non-fatal (the OS reaps the tempdir), never break the run.
                    pass


def _balanced_ranges(
    sizes: Sequence[int], k: int
) -> list[tuple[int, int, int, int]]:
    """Partition contiguous blocks (given their per-block row ``sizes``) into <= k
    ORDER-PRESERVING groups of ~equal SCORING COST, so a thread pool saturates
    every core instead of one worker stalling on the big block while the rest go
    idle. FS scoring is O(block^2), so a block's cost is its within-block pair
    count (``s*s``), NOT its row count -- balancing by rows would pile several big
    blocks on one worker. Returns ``(start_block, end_block, row_offset,
    row_count)`` per group: ``row_offset``/``row_count`` index the block-contiguous
    gathered table (``slice``), ``start``/``end`` slice the ``size_list``."""
    n = len(sizes)
    if k <= 1 or n <= 1:
        return [(0, n, 0, sum(sizes))]
    costs = [s * s for s in sizes]
    target = max(1, sum(costs) // k)
    groups: list[tuple[int, int, int, int]] = []
    start = acc_cost = grp_rows = row_off = 0
    for i in range(n):
        acc_cost += costs[i]
        grp_rows += sizes[i]
        # Close a group once it holds ~its cost share, but keep enough blocks for
        # the remaining groups so we don't collapse to fewer than k.
        remaining_groups = k - 1 - len(groups)
        if (
            acc_cost >= target
            and remaining_groups >= 1
            and (n - (i + 1)) >= remaining_groups
        ):
            groups.append((start, i + 1, row_off, grp_rows))
            row_off += grp_rows
            start = i + 1
            acc_cost = grp_rows = 0
    groups.append((start, n, row_off, sum(sizes) - row_off))
    return groups


def score_fs_sequential_arrow(
    prepared_df: Any,
    blocking_config: BlockingConfig,
    mk: MatchkeyConfig,
    matched_pairs: set[tuple[int, int]],
    em_result,
    *,
    target_ids: set[int] | None = None,
    max_block_rows: int | None = None,
    emit: str = "tuples",
) -> Any:
    """IN-RAM sequential Arrow-native / Rust FS block scoring (no DuckDB).

    The in-RAM analogue of ``score_fs_out_of_core``, and the block-source the
    "sequential Arrow batches + WCC at the end" scale method wants: the block key
    is derived via the ``Frame`` seam's ``derive_block_key`` + ``filter_valid_key``
    (NO polars, NO DuckDB SQL / on-disk sorted scans), then block-key GROUPING +
    GATHER run in RUST -- ``build_block_index_arrow`` returns a row-position
    permutation (blocks contiguous, singletons dropped) + per-block sizes in one
    native call, and ``pa.Table.take(order)`` gathers each wave block-contiguous
    (no pyarrow ``group_by`` + per-block ``.as_py()`` round-trip; a pyarrow
    group_by fallback covers the missing-kernel case). Blocks stream through the
    SAME native FS kernel in bounded waves. With
    ``emit="arrow"`` the scored pairs are converted to a ``PAIR_STREAM`` ``pa.Table``
    per wave and the tuples dropped, so pairs never accumulate as Python objects;
    the downstream Rust WCC (``build_clusters_arrow_native``) collapses cross-pass
    duplicate edges, so no ``seen`` set is kept in arrow mode.

    Working set = the resident keyed frame + one WAVE of gathered block rows
    (bounded by ``GOLDENMATCH_FS_OOC_WAVE_ROWS``) + the accumulated Arrow edge
    stream (<< the frame). Use this when the prepared frame FITS in RAM and you
    want the fast pure-Arrow/Rust path with a single WCC stitch at the end;
    ``score_fs_out_of_core`` is the DuckDB-spilled variant for frames that don't.

    **Parity.** ``derive_block_key`` is the byte-equivalent Arrow twin of
    ``_build_block_key_expr`` (both -> ``core.arrow_derive.block_key``) and
    ``filter_valid_key`` is the same null/sentinel guard, with the same
    ``multi_pass`` ``(pass_sig, block_key)`` semantics as ``build_blocks`` /
    ``score_fs_out_of_core``, and the SAME ``score_probabilistic_bucket_native``
    kernel, so the emitted pair set is byte-identical to ``score_buckets`` ABSENT
    oversized blocks (capped at ``max_block_rows``). Within-block gather order is
    irrelevant (pairs are canonicalized + the kernel compares only within-block),
    so the wave gather need not sort within a block.
    """
    import numpy as np
    import pyarrow as pa

    from goldenmatch.backends.score_buckets import pairs_to_pair_stream
    from goldenmatch.core._native_loader import native_available, native_module
    from goldenmatch.core.frame import is_polars_lazyframe
    from goldenmatch.core.frame import (
        to_frame as _tf,
    )
    from goldenmatch.core.probabilistic import (
        _fs_native_eligible,
        _fs_vectorized_enabled,
        _fs_vectorized_supported,
        probabilistic_block_scorer,
        score_probabilistic_bucket_native,
        score_probabilistic_vectorized_batch,
    )

    if blocking_config.strategy not in ("static", "multi_pass"):
        raise NotImplementedError(
            f"score_fs_sequential_arrow supports static/multi_pass, not "
            f"{blocking_config.strategy!r}"
        )
    if em_result is None:
        raise ValueError("score_fs_sequential_arrow requires a trained em_result")

    max_block_size = blocking_config.max_block_size
    if max_block_rows is None:
        max_block_rows = max_block_size

    # Base as a pa.Table (ARROW-NATIVE, no polars). ``to_arrow`` handles a polars
    # frame (converts) or a pa.Table (returns as-is); a lazy frame is collected
    # first. The whole scorer runs on this table + the Arrow ``Frame`` seam. No
    # sort/positions bookkeeping: the Rust grouping kernel returns a row-position
    # permutation that indexes DIRECTLY into the keyed table (``keyed.take(order)``).
    _fw = _tf(prepared_df)
    if is_polars_lazyframe(_fw.native):
        _fw = _tf(_fw.native.collect())
    base = _fw.to_arrow()
    keep = _needed_columns(base, mk, blocking_config)
    base = base.select(keep)

    use_native = _fs_native_eligible(mk)
    prob_scorer = None if use_native else probabilistic_block_scorer(mk, em_result)
    frozen_exclude = frozenset(matched_pairs)
    _use_vec_batch = (
        not use_native
        and _fs_vectorized_enabled()
        and _fs_vectorized_supported(mk)
    )

    passes = (
        list(blocking_config.passes or [])
        if blocking_config.strategy == "multi_pass"
        else list(blocking_config.keys or [])
    )

    _arrow = emit == "arrow"
    out: list[tuple[int, int, float]] = []
    seen: set[tuple[int, int]] = set()
    pair_tables: list = []
    wave_rows = _fs_ooc_wave_rows()
    _workers = _fs_ooc_workers()
    _ex: Any = None  # bound to the ThreadPoolExecutor around the pass loop below

    def _merge(results) -> None:
        if _arrow:
            wave: list[tuple[int, int, float]] = []
            for pairs in results:
                for a, b, s in pairs:
                    if target_ids is not None and (
                        (a in target_ids) == (b in target_ids)
                    ):
                        continue
                    wave.append((a, b, s))
            if wave:
                pair_tables.append(pairs_to_pair_stream(wave))
            return
        for pairs in results:
            for a, b, s in pairs:
                if target_ids is not None and (
                    (a in target_ids) == (b in target_ids)
                ):
                    continue
                key = (a, b) if a < b else (b, a)
                if key in seen:
                    continue
                seen.add(key)
                out.append((a, b, s))

    def _score_wave(gathered: Any, sizes: list[int]) -> None:
        # ``gathered`` is one BLOCK-CONTIGUOUS frame (rows grouped by block, blocks
        # laid end-to-end) + ``sizes`` its per-block row counts -- exactly the
        # ``score_probabilistic_bucket_native`` contract.
        if not sizes:
            return
        if use_native:
            # Fan the wave across cores, BALANCED by scoring cost (block^2), so no
            # worker stalls on the big block while the rest idle. Each group is ONE
            # native kernel call over its block-contiguous slice + size_list; the
            # kernel releases the GIL, so this is real N-core parallelism.
            # ``executor.map`` preserves submission order -> block-order merge
            # (parity with the serial reference).
            groups = _balanced_ranges(sizes, _workers)
            if _ex is None or len(groups) <= 1:
                _merge((
                    score_probabilistic_bucket_native(
                        gathered, sizes, mk, em_result, frozen_exclude,
                    ),
                ))
            else:
                def _score_group(g, _g=gathered, _sz=sizes):
                    s, e, off, rows = g
                    return score_probabilistic_bucket_native(
                        _g.slice(off, rows), _sz[s:e], mk, em_result, frozen_exclude,
                    )
                _merge(_ex.map(_score_group, groups))
        else:
            # Split the block-contiguous gather back into per-block frames (cheap
            # zero-copy slices) for the vectorized-batch / per-block scorer paths.
            blocks = []
            off = 0
            for sz in sizes:
                blocks.append(gathered.slice(off, sz))
                off += sz
            if _use_vec_batch:
                _merge((
                    score_probabilistic_vectorized_batch(
                        blocks, mk, em_result, frozen_exclude
                    ),
                ))
            elif _ex is None:
                _merge(prob_scorer(b, frozen_exclude) for b in blocks)
            else:
                _merge(_ex.map(lambda b: prob_scorer(b, frozen_exclude), blocks))

    # Grouping kernel: Rust ``build_block_index_arrow`` groups keyed rows by block
    # key and returns a row-POSITION permutation (blocks contiguous, singletons
    # dropped) + per-block sizes in ONE native call -- no pyarrow ``group_by`` +
    # per-block ``.as_py()`` round-trip. Fall back to a pyarrow group_by (over a
    # synthetic position column) when the kernel symbol is absent.
    _native_group = (
        native_available()
        and hasattr(native_module(), "build_block_index_arrow")
    )

    def _group_positions(keyed: Any) -> tuple[np.ndarray, list[int]]:
        """(row-position permutation into ``keyed``, per-block sizes>=2)."""
        if _native_group:
            kc = keyed.column("__block_key__")
            key_arr = (
                pa.concat_arrays(kc.chunks)
                if isinstance(kc, pa.ChunkedArray)
                else kc
            )
            order_arr, sizes = native_module().build_block_index_arrow([key_arr])
            order_np = (
                order_arr.to_numpy(zero_copy_only=False)
                if hasattr(order_arr, "to_numpy")
                else np.asarray(order_arr, dtype=np.int64)
            )
            return order_np.astype(np.int64, copy=False), [int(s) for s in sizes]
        # pyarrow fallback: aggregate a synthetic position index per block key.
        keyed_p = keyed.append_column(
            "__pos__", pa.array(np.arange(keyed.num_rows, dtype=np.int64))
        )
        grouped = keyed_p.group_by("__block_key__").aggregate([("__pos__", "list")])
        parts: list[np.ndarray] = []
        sizes: list[int] = []
        for lst in grouped.column("__pos___list"):
            arr = np.asarray(lst.as_py(), dtype=np.int64)
            if arr.size < 2:
                continue
            parts.append(arr)
            sizes.append(int(arr.size))
        order_np = (
            np.concatenate(parts) if parts else np.empty(0, dtype=np.int64)
        )
        return order_np, sizes

    # ONE thread pool across all passes: `_score_wave` fans each wave's balanced
    # groups out over it (the native kernel releases the GIL -> real N-core
    # scoring). A plain create/shutdown (not `with`) keeps the pass loop indent.
    _ex = concurrent.futures.ThreadPoolExecutor(max_workers=_workers)
    try:
      for pass_config in passes:  # noqa: E111 (executor lives across passes)
        # ARROW-NATIVE block-key grouping (no polars): derive the block key via
        # the Frame seam's ``derive_block_key`` (the Arrow twin of
        # ``_build_block_key_expr`` -> ``core.arrow_derive.block_key``) + the
        # verbatim null/sentinel guard ``filter_valid_key``, then GROUP + GATHER in
        # Rust: ``build_block_index_arrow`` -> row-position permutation + sizes,
        # and ``keyed.take(order)`` gathers each wave block-contiguous.
        _bf = _tf(base)
        _bf = _bf.with_column(
            "__block_key__",
            _bf.derive_block_key(
                list(pass_config.fields),
                list(pass_config.transforms or []),
                field_transforms=getattr(pass_config, "field_transforms", None),
            ),
        )
        keyed = _bf.filter_valid_key("__block_key__").native
        if keyed.num_rows == 0:
            continue
        order_np, sizes = _group_positions(keyed)
        # Stream the permutation in bounded waves: accumulate whole blocks (capped
        # at ``max_block_rows``, the one non-parity edge) until ~wave_rows, then ONE
        # gather (``keyed.take``) + balanced parallel score, and release.
        wave_pos: list[np.ndarray] = []
        wave_sizes: list[int] = []
        wave_acc = 0
        off = 0

        def _flush_wave(wp, ws, _keyed=keyed):
            if not ws:
                return
            gathered = _keyed.take(pa.array(np.concatenate(wp)))
            _score_wave(gathered, ws)

        for s in sizes:
            seg = order_np[off:off + s]
            off += s
            if s > max_block_rows:  # oversized cap (the one non-parity edge)
                seg = seg[:max_block_rows]
            wave_pos.append(seg)
            wave_sizes.append(int(seg.shape[0]))
            wave_acc += int(seg.shape[0])
            if wave_acc >= wave_rows:
                _flush_wave(wave_pos, wave_sizes)
                wave_pos, wave_sizes, wave_acc = [], [], 0
        # Flush at the END of each pass so pass N is merged before pass N+1 (the
        # pass-order first-seen dedup for tuples mode; arrow mode is order-free).
        _flush_wave(wave_pos, wave_sizes)
    finally:
        _ex.shutdown(wait=True)

    if _arrow:
        if pair_tables:
            return pa.concat_tables(pair_tables)
        return pairs_to_pair_stream([])
    return out


def stream_fs_dedupe_output(
    con: Any,
    prep_table: str,
    assignments: Any,
    config: Any,
    out_dir: str,
    *,
    record_cols: list[str] | None = None,
) -> dict:
    """Stream the O(N) dedupe output (unique / dupes / golden) to parquet from a
    DuckDB store, BOUNDED — the piece that lets single-box FS clear 50M+.

    ``unique`` and ``dupes`` are the O(N) bulk (unique ~= most of N); they are
    written with DuckDB ``COPY (query) TO parquet``, which STREAMS the result to
    disk with NO Python materialisation. Only ``golden`` (bounded to
    multi-member-cluster rows) uses the in-memory ``build_golden_records_batch``.
    So peak stays bounded regardless of N — the result frame is never held.

    Args:
        con: DuckDB connection holding ``prep_table`` (``__row_id__`` + record
            columns; ``__xform_*`` helpers are excluded from the output).
        assignments: arrow Table / mapping of (``__row_id__``, ``__cluster_id__``)
            — one row per input record (singletons included, own cluster).
        record_cols: output columns; default = every ``prep_table`` column that
            is not a ``__xform_*`` helper.
    Returns paths + counts (NOT frames).
    """
    import os as _os

    import polars as pl
    import pyarrow as pa

    from goldenmatch.core.golden import build_golden_records_batch

    max_cluster_size = 100
    if getattr(config, "golden_rules", None) is not None:
        max_cluster_size = config.golden_rules.max_cluster_size
    golden_rules = getattr(config, "golden_rules", None)

    # (__row_id__, __cluster_id__) assignments -> a DuckDB table we can JOIN.
    if not isinstance(assignments, pa.Table):
        rids = [int(r) for r, _ in assignments]
        cids = [int(c) for _, c in assignments]
        assignments = pa.table(
            {"__row_id__": pa.array(rids, pa.int64()),
             "__cluster_id__": pa.array(cids, pa.int64())}
        )
    con.register("asn_arrow", assignments)
    con.execute("CREATE OR REPLACE TEMP TABLE asn AS SELECT * FROM asn_arrow")
    con.unregister("asn_arrow")
    con.execute(
        "CREATE OR REPLACE TEMP TABLE sizes AS "
        "SELECT __cluster_id__ AS cid, count(*) AS n FROM asn GROUP BY __cluster_id__"
    )

    all_cols = [d[0] for d in con.execute(f"DESCRIBE {prep_table}").fetchall()]
    if record_cols is None:
        record_cols = [c for c in all_cols if not c.startswith("__xform_")]
    _sel = ", ".join(f"p.{_sql_ident(c)}" for c in record_cols)

    _os.makedirs(out_dir, exist_ok=True)
    unique_path = _os.path.join(out_dir, "unique.parquet")
    dupes_path = _os.path.join(out_dir, "dupes.parquet")
    golden_path = _os.path.join(out_dir, "golden.parquet")

    base_join = (
        f"FROM {prep_table} p "
        "JOIN asn a ON p.__row_id__ = a.__row_id__ "
        "JOIN sizes s ON a.__cluster_id__ = s.cid"
    )
    # unique = singleton clusters; dupes = multi-member (oversized INCLUDED,
    # mirroring _finalize's size>1 dupe rule). Both STREAMED via COPY.
    con.execute(
        f"COPY (SELECT {_sel} {base_join} WHERE s.n = 1) "
        f"TO {_sql_lit(unique_path)} (FORMAT parquet)"
    )
    con.execute(
        f"COPY (SELECT {_sel}, a.__cluster_id__ {base_join} WHERE s.n > 1) "
        f"TO {_sql_lit(dupes_path)} (FORMAT parquet)"
    )
    # golden = non-oversized multi-member; bounded subset -> in-memory builder.
    golden_tbl = con.execute(
        f"SELECT {_sel}, a.__cluster_id__ {base_join} "
        f"WHERE s.n > 1 AND s.n <= {int(max_cluster_size)}"
    ).to_arrow_table()
    golden_count = 0
    if golden_tbl.num_rows:
        multi_df = pl.from_arrow(golden_tbl)
        records = build_golden_records_batch(
            multi_df,
            golden_rules if golden_rules is not None else _default_golden_rules(),
        )
        golden_count = len(records)
        pl.DataFrame(records).write_parquet(golden_path)
    elif _os.path.exists(golden_path):
        # No golden rows this run: remove a golden.parquet left by a PRIOR run
        # into the same out_dir, so the on-disk file set matches the returned
        # golden_path=None / golden_count=0 (unique/dupes are COPY-overwritten;
        # only golden is conditionally written, so only it can go stale).
        _os.unlink(golden_path)

    import pyarrow.parquet as _pq

    return {
        "unique_path": unique_path,
        "dupes_path": dupes_path,
        "golden_path": golden_path if golden_count else None,
        "unique_count": _pq.read_metadata(unique_path).num_rows,
        "dupes_count": _pq.read_metadata(dupes_path).num_rows,
        "golden_count": golden_count,
    }


def _default_golden_rules():
    from goldenmatch.config.schemas import GoldenRulesConfig

    return GoldenRulesConfig(default_strategy="most_complete")


def _prep_all_ids(con: Any) -> Sequence[int]:
    """Every ``__row_id__`` in ``prep`` — the singleton-folding id set. Singletons
    (rows in no pair) must be present in the cluster assignments or
    ``stream_fs_dedupe_output``'s INNER JOIN silently drops them.

    When the ids are CONTIGUOUS (min..max with no gaps — the pipeline-generated
    common case, since ``__row_id__`` is a dense global row index), return a
    ``range`` instead of materialising a 25-50M-element Python list (which
    ``fetchall()`` would, then get copied again into the downstream pyarrow
    int64 array in ``build_clusters_arrow_native``). ``__row_id__`` is unique per
    row, so ``max - min + 1 == count`` iff the set is exactly ``{min..max}``.
    Falls back to the explicit list when there are gaps (e.g. a filtered prep)."""
    lo, hi, n = con.execute(
        "SELECT min(__row_id__), max(__row_id__), count(*) FROM prep"
    ).fetchone()
    if not n:
        return []
    if int(hi) - int(lo) + 1 == int(n):
        return range(int(lo), int(hi) + 1)
    return [r[0] for r in con.execute("SELECT __row_id__ FROM prep").fetchall()]


def _prep_all_ids_frame(table: Any) -> Sequence[int]:
    """The pyarrow analogue of ``_prep_all_ids``: the full ``__row_id__`` set off a
    RESIDENT ``pa.Table`` (no DuckDB, no polars) — a ``range`` when contiguous
    (min..max, no gaps — the pipeline's dense global row index), else the explicit
    list. ``__row_id__`` is unique per row, so ``max - min + 1 == count`` iff
    exactly ``{min..max}``."""
    import pyarrow.compute as pc

    col = table.column("__row_id__")
    n = len(col)
    if not n:
        return []
    lo, hi = int(pc.min(col).as_py()), int(pc.max(col).as_py())
    if hi - lo + 1 == n:
        return range(lo, hi + 1)
    return col.to_pylist()


def _stream_fs_dedupe_output_arrow(
    frame: Any, assignments: Any, config: Any, out_dir: str
) -> dict:
    """DuckDB-FREE, POLARS-FREE O(N) dedupe output for the in-RAM sequential path.

    unique/dupes (the O(N) bulk) are built in pure pyarrow — the resident
    ``frame`` (``pa.Table``) is joined to the ``(row_id, cluster_id)`` assignments
    + per-cluster sizes with ``pa.Table.join`` and written via ``ParquetWriter``,
    so no polars touches the row-count-sized data. Only ``golden`` (the BOUNDED
    non-oversized multi-member subset) is handed to the polars-native
    ``build_golden_records_batch`` core primitive (an Arrow golden builder is a
    separate core port). Same unique=singleton / dupes=multi(oversized-incl) /
    golden=non-oversized-multi semantics + ``__xform_*`` exclusion as
    ``stream_fs_dedupe_output``."""
    import os as _os

    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as _pq

    from goldenmatch.core.golden import build_golden_records_batch

    max_cluster_size = 100
    golden_rules = getattr(config, "golden_rules", None)
    if golden_rules is not None:
        max_cluster_size = golden_rules.max_cluster_size

    if isinstance(assignments, pa.Table):
        asn = assignments
    else:
        asn = pa.table(
            {
                "__row_id__": pa.array([int(r) for r, _ in assignments], pa.int64()),
                "__cluster_id__": pa.array(
                    [int(c) for _, c in assignments], pa.int64()
                ),
            }
        )

    record_cols = [c for c in frame.column_names if not c.startswith("__xform_")]
    sizes = asn.group_by("__cluster_id__").aggregate([("__row_id__", "count")])
    sizes = sizes.rename_columns(
        ["__cluster_id__" if c == "__cluster_id__" else "__n__" for c in sizes.column_names]
    )

    # frame ⨝ assignments ⨝ sizes -- pure Arrow inner joins.
    joined = frame.join(asn, keys="__row_id__", join_type="inner").join(
        sizes, keys="__cluster_id__", join_type="inner"
    )
    n_col = joined.column("__n__")

    _os.makedirs(out_dir, exist_ok=True)
    unique_path = _os.path.join(out_dir, "unique.parquet")
    dupes_path = _os.path.join(out_dir, "dupes.parquet")
    golden_path = _os.path.join(out_dir, "golden.parquet")

    unique_tbl = joined.filter(pc.equal(n_col, 1)).select(record_cols)
    dupes_tbl = joined.filter(pc.greater(n_col, 1)).select(
        [*record_cols, "__cluster_id__"]
    )
    _pq.write_table(unique_tbl, unique_path)
    _pq.write_table(dupes_tbl, dupes_path)

    # golden = non-oversized multi-member; BOUNDED subset -> polars core builder.
    golden_tbl = joined.filter(
        pc.and_(pc.greater(n_col, 1), pc.less_equal(n_col, int(max_cluster_size)))
    ).select([*record_cols, "__cluster_id__"])
    golden_count = 0
    if golden_tbl.num_rows:
        import polars as pl

        records = build_golden_records_batch(
            pl.from_arrow(golden_tbl),
            golden_rules if golden_rules is not None else _default_golden_rules(),
        )
        golden_count = len(records)
        pl.DataFrame(records).write_parquet(golden_path)
    elif _os.path.exists(golden_path):
        _os.unlink(golden_path)

    return {
        "unique_path": unique_path,
        "dupes_path": dupes_path,
        "golden_path": golden_path if golden_count else None,
        "unique_count": unique_tbl.num_rows,
        "dupes_count": dupes_tbl.num_rows,
        "golden_count": golden_count,
    }


def _cluster_python(
    con: Any,
    pairs: list[tuple[int, int, float]],
    max_cluster_size: int,
    link_threshold: float | None,
) -> tuple[list[tuple[int, int]], int]:
    """Legacy path: Python Union-Find over the ``list[tuple]`` pair set. Returns
    ``([(row_id, cluster_id), …], n_pairs)``. Kept as the
    ``GOLDENMATCH_FS_OOC_ARROW_CLUSTER=0`` rollback lever."""
    from goldenmatch.core.cluster import build_clusters

    if link_threshold is not None:
        pairs = [p for p in pairs if p[2] >= link_threshold]
    all_ids = _prep_all_ids(con)
    clusters = build_clusters(pairs, all_ids=all_ids, max_cluster_size=max_cluster_size)
    assignments = [
        (m, cid) for cid, info in clusters.items() for m in info["members"]
    ]
    return assignments, len(pairs)


def _cluster_arrow_native(
    all_ids: Any,
    pair_table: Any,
    max_cluster_size: int,
    link_threshold: float | None,
) -> tuple[Any, int]:
    """Arrow-native path: dedup the ``PAIR_STREAM`` table with the Rust
    ``dedup_pairs_arrow`` kernel, then cluster with ``build_clusters_arrow_native``
    (Rust Union-Find via the C Data Interface — no Python ``dict[int, dict]``).
    ``all_ids`` is the full row-id set (range/list) for singleton folding —
    supplied by the caller (from DuckDB via ``_prep_all_ids(con)`` on the
    out-of-core path, or straight off the resident polars frame on the in-RAM
    sequential path), so this clusterer is DuckDB-agnostic. Returns
    ``(assignments pa.Table {__row_id__, __cluster_id__}, n_pairs)``."""
    import pyarrow as pa
    import pyarrow.compute as pc

    from goldenmatch.core.cluster import build_clusters_arrow_native
    from goldenmatch.core.pairs import dedup_pairs_max_score_arrow_table

    # Cross-pass dedup: canonical (min, max), max score via the Rust
    # ``dedup_pairs_arrow`` kernel over the pa.Table (NO polars) — the Arrow-native
    # replacement for the Python `seen` set. Union-Find membership is invariant to
    # which duplicate's score survives, so this is cluster-parity-safe.
    pairs_tbl = dedup_pairs_max_score_arrow_table(pair_table)
    if link_threshold is not None:
        # Cluster only linked pairs; sub-link pairs are review candidates the
        # in-memory pipeline surfaces separately and never clusters. Filtering
        # AFTER max-score dedup means a pair links iff its BEST cross-pass score
        # clears the cut (== per-wave filter-then-dedup, since max is monotone).
        pairs_tbl = pairs_tbl.filter(
            pc.greater_equal(pairs_tbl.column("score"), link_threshold)
        )
    n_pairs = pairs_tbl.num_rows

    cf = build_clusters_arrow_native(
        pairs_tbl, all_ids=all_ids, max_cluster_size=max_cluster_size, backend="arrow",
    )
    # ClusterFrames.assignments is {cluster_id, member_id} (pa.Table on the native
    # arrow lane, pl.DataFrame on the columnar fallback). Normalise to a pa.Table
    # renamed to the (__row_id__, __cluster_id__) shape the streamer joins on.
    asn = cf.assignments
    if not isinstance(asn, pa.Table):
        asn = asn.to_arrow()
    asn = asn.rename_columns([
        "__cluster_id__" if c == "cluster_id" else "__row_id__"
        for c in asn.column_names
    ])
    return asn, n_pairs


def run_fs_dedupe_streaming(
    prepared_df: Any,
    blocking_config: BlockingConfig,
    mk: MatchkeyConfig,
    em_result,
    config: Any,
    out_dir: str,
    *,
    matched_pairs: set[tuple[int, int]] | None = None,
    target_ids: set[int] | None = None,
    link_threshold: float | None = None,
) -> dict:
    """End-to-end SINGLE-BOX STREAMING FS dedupe: prep frame → DuckDB file, FREE
    the frame, score from the store, cluster, STREAM unique/dupes/golden to
    parquet, return paths + stats. Peak stays bounded (frame on disk, O(N) output
    streamed via COPY) — the path that clears 50M+ where in-memory OOMs.

    Ties the two tested mechanisms without refactoring: ``score_fs_out_of_core``
    with an explicit ``db_path`` file PERSISTS the ``prep`` table (only "auto" is
    cleaned), so ``stream_fs_dedupe_output`` reads the SAME file afterward. The
    prepared frame is resident only during the batched load inside scoring, never
    through clustering or output.

    **The scored pairs stay Arrow end-to-end** (default; ``GOLDENMATCH_FS_OOC_ARROW_CLUSTER=0``
    restores the ``list[tuple]`` + Python Union-Find path). ``score_fs_out_of_core``
    emits a ``PAIR_STREAM`` ``pa.Table`` instead of accumulating ``list[tuple]``,
    ``dedup_pairs_arrow`` collapses cross-pass duplicates in Rust, and
    ``build_clusters_arrow_native`` runs the Union-Find in Rust over the Arrow
    buffers (no ``dict[int, dict]`` materialisation, no per-pair Python object) —
    so peak RSS drops from the ~240 B/pair Python floor (~16 GB at 66M pairs) to
    the ~20 B/pair Arrow stream, and the clustering wall is native. Assignments
    stream straight into ``stream_fs_dedupe_output`` as an Arrow table.

    ``link_threshold``: when set, only pairs scoring ``>= link_threshold`` are
    CLUSTERED (lower-scoring pairs are review candidates the in-memory pipeline
    surfaces separately and never clusters — streaming has no review output, so
    they are simply dropped). Pass the ``link_threshold`` from
    ``_prepare_probabilistic_review_scoring`` alongside a review-cut ``scoring_mk``
    to match the in-memory clustering outcome exactly. ``None`` clusters every
    returned pair (the kernel scored at ``mk``'s own threshold)."""
    import os as _os
    import tempfile

    import duckdb

    matched_pairs = set(matched_pairs or ())
    max_cluster_size = 100
    if getattr(config, "golden_rules", None) is not None:
        max_cluster_size = config.golden_rules.max_cluster_size

    arrow_stream = _fs_ooc_arrow_cluster_enabled()
    fd, db_path = tempfile.mkstemp(prefix="gm_fs_stream_", suffix=".duckdb")
    _os.close(fd)
    _os.unlink(db_path)  # DuckDB creates it
    try:
        # 1+2: load frame into the persistent file + score (frame freed on return).
        pairs = score_fs_out_of_core(
            prepared_df, blocking_config, mk, matched_pairs, em_result,
            target_ids=target_ids, db_path=db_path,
            emit="arrow" if arrow_stream else "tuples",
        )
        con = duckdb.connect(db_path)
        try:
            if arrow_stream:
                assignments, n_pairs = _cluster_arrow_native(
                    _prep_all_ids(con), pairs, max_cluster_size, link_threshold,
                )
            else:
                assignments, n_pairs = _cluster_python(
                    con, pairs, max_cluster_size, link_threshold,
                )
            # 4: stream the O(N) output from the store.
            res = stream_fs_dedupe_output(con, "prep", assignments, config, out_dir)
        finally:
            con.close()
        res["pairs"] = n_pairs
        return res
    finally:
        for p in (db_path, db_path + ".wal"):
            try:
                if _os.path.exists(p):
                    _os.unlink(p)
            except OSError:
                # Best-effort cleanup: a temp DB/WAL unlink failure is non-fatal
                # (the OS reaps the tempdir), never break the run.
                pass


def run_fs_dedupe_sequential(
    prepared_df: Any,
    blocking_config: BlockingConfig,
    mk: MatchkeyConfig,
    em_result,
    config: Any,
    out_dir: str,
    *,
    matched_pairs: set[tuple[int, int]] | None = None,
    target_ids: set[int] | None = None,
    link_threshold: float | None = None,
) -> dict:
    """End-to-end IN-RAM SEQUENTIAL FS dedupe over the resident Arrow frame.

    The score -> cluster back-half runs through the **fused Fellegi-Sunter Rust
    kernel** (``run_match_fused_fs_arrow`` / ``…_multipass_arrow`` ->
    ``match_fused_fs``): block-key grouping, per-block FS scoring, AND the
    connected-components WCC all happen inside ONE Rust call that returns
    ``(__row_id__, __cluster_id__)`` cluster assignments directly — no Python block
    orchestration, no candidate-pair list, no separate edge-stream + WCC pass. This
    is the same arrow-native/Rust engine the in-memory dedupe short-circuit
    (``_run_fused_fs_short_circuit``) uses; the streaming path just feeds it the
    resident frame and streams the assignments to parquet. When the config is
    outside the fused kernel's covered surface (or the native kernel is absent), it
    falls back to ``score_fs_sequential_arrow`` (Arrow/Rust batch scoring +
    ``build_block_index_arrow`` grouping) + a Rust WCC over the Arrow edge stream.

    Output reuses the tested ``_stream_fs_dedupe_output_arrow`` (O(N) unique/dupes
    via pure pyarrow, golden only on the bounded multi-member subset), so the
    result shape matches ``run_fs_dedupe_streaming`` exactly. ``link_threshold``
    semantics match ``run_fs_dedupe_streaming`` (cluster only pairs whose best
    cross-pass score clears the cut).

    **No DuckDB, no polars** (except the bounded golden-record builder, an existing
    polars core primitive fed only the multi-member subset). The fast single-box
    path for frames that FIT in RAM; ``run_fs_dedupe_streaming`` is the
    DuckDB-spilled variant past RAM."""
    from goldenmatch.core.frame import is_polars_lazyframe
    from goldenmatch.core.frame import to_frame as _tf

    matched_pairs = set(matched_pairs or ())
    max_cluster_size = 100
    if getattr(config, "golden_rules", None) is not None:
        max_cluster_size = config.golden_rules.max_cluster_size

    # Materialize the resident record table (pa.Table) ONCE, then share it across
    # scoring + clustering + streamed output (a single frame conversion).
    _fw = _tf(prepared_df)
    if is_polars_lazyframe(_fw.native):
        _fw = _tf(_fw.native.collect())
    base = _fw.to_arrow()

    # ARROW-NATIVE / RUST FUSED PATH: grouping + FS scoring + connected-components
    # in one kernel call -> cluster assignments directly. dedupe-only (no
    # target_ids / matched_pairs exclude), which is exactly how the streaming
    # dedupe caller invokes this.
    assignments = None
    n_pairs = None
    if not target_ids and not matched_pairs:
        assignments = _fused_fs_assignments(
            base, blocking_config, mk, em_result, link_threshold,
        )

    if assignments is None:
        # Fallback (uncovered config / native kernel absent): Arrow/Rust batch
        # scorer (Rust block grouping via build_block_index_arrow) -> Arrow edge
        # stream -> Rust Union-Find WCC.
        pairs = score_fs_sequential_arrow(
            base, blocking_config, mk, matched_pairs, em_result,
            target_ids=target_ids, emit="arrow",
        )
        assignments, n_pairs = _cluster_arrow_native(
            _prep_all_ids_frame(base), pairs, max_cluster_size, link_threshold,
        )

    # O(N) unique/dupes streamed to parquet from the resident table (pure pyarrow).
    res = _stream_fs_dedupe_output_arrow(base, assignments, config, out_dir)
    res["pairs"] = n_pairs
    res["streaming"] = True
    res["sequential"] = True
    res["fused"] = n_pairs is None
    res["output_dir"] = out_dir
    return res


def _fused_fs_assignments(
    base: Any,
    blocking_config: BlockingConfig,
    mk: MatchkeyConfig,
    em_result: Any,
    link_threshold: float | None,
) -> Any | None:
    """Cluster the resident frame via the fused FS Rust kernel — grouping + FS
    scoring + connected-components in ONE ``match_fused_fs`` call — returning a
    ``(__row_id__, __cluster_id__)`` Table remapped to ``base``'s real
    ``__row_id__`` values, or ``None`` when the config is outside the fused
    kernel's covered surface / the native kernel is absent (caller falls back).

    The kernel emits links at the REAL ``link_threshold`` cut and CCs them, so the
    resulting partition matches the classic ``score -> filter(>=link) -> cluster``
    split. Column gathering + the ``GoldenMatchConfig`` shape mirror the in-memory
    ``_run_fused_fs_short_circuit`` exactly (byte-parity by construction)."""
    from goldenmatch.config.schemas import GoldenMatchConfig
    from goldenmatch.core.frame import to_frame as _tf
    from goldenmatch.core.fused_match import (
        match_fused_fs_multipass_ready,
        match_fused_fs_ready,
        run_match_fused_fs_arrow,
        run_match_fused_fs_multipass_arrow,
    )

    # Cut at the REAL link threshold (the caller's `mk` carries the review cut;
    # clustering uses `link_threshold`). Kernel computes it from EM when None.
    fused_mk = (
        mk.model_copy(update={"link_threshold": float(link_threshold)})
        if link_threshold is not None
        else mk
    )
    cfg = GoldenMatchConfig(blocking=blocking_config, matchkeys=[fused_mk])
    if not (match_fused_fs_ready(cfg) or match_fused_fs_multipass_ready(cfg)):
        return None

    # Gather the source columns the kernel reads as Arrow arrays (blocking key
    # fields + comparison fields + NE fields), via the same _fused_needed_src_cols
    # derivation the in-memory short-circuit uses.
    from goldenmatch.core.pipeline import _fused_needed_src_cols

    _fwbase = _tf(base)
    cols = [c for c in _fused_needed_src_cols(cfg) if c in _fwbase.columns]
    columns = {c: _fwbase.column(c).to_arrow() for c in cols}
    n_rows = base.num_rows
    fused_tbl = run_match_fused_fs_arrow(
        columns, cfg, em_result, n_rows=n_rows
    ) or run_match_fused_fs_multipass_arrow(
        columns, cfg, em_result, n_rows=n_rows
    )
    if fused_tbl is None:
        return None

    # The kernel keys clusters by POSITIONAL row id (0..n-1). Remap to base's real
    # __row_id__ (position i -> base.__row_id__[i]) so the streamed output joins
    # correctly even when __row_id__ is gapped (post-quarantine).
    pos = fused_tbl.column("__row_id__")
    real_ids = base.column("__row_id__").take(pos)
    idx = fused_tbl.schema.get_field_index("__row_id__")
    return fused_tbl.set_column(idx, "__row_id__", real_ids)
