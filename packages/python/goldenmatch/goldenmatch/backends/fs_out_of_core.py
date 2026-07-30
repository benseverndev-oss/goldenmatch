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

**End-to-end path: `run_fs_dedupe_streaming`.** Ties the bounded mechanisms
together for single-box scale: prep frame -> DuckDB FILE (batched load,
`_load_frame_batched` keeps the load peak ~1x the frame, not the ~2-3x a full
`to_arrow()` copy would), FREE the frame (see below), score from the store
(`score_fs_out_of_core`), cluster (Arrow-native, no polars pair copy), then STREAM
unique/dupes/golden to parquet (`stream_fs_dedupe_output`, O(N) output via DuckDB
`COPY`, never a result frame). So peak stays ~1x the prepared frame (only during
the load) instead of the in-memory ~1.65 GB/M accumulation -- e.g. 50M is ~1x
frame (~15-25 GB, FITS 64 GB) vs the in-memory ~82 GB OOM.

**The back-half holds NEITHER a resident frame NOR an O(N) golden spike** (three
bounded levers, all default-on within the opt-in out-of-core route):
  - **Frame freed at the load boundary, not held through clustering + output.**
    The prepared frame is routed through a single-element HOLDER
    (`_unwrap_frame_holder`); every downstream stage reads the DuckDB `prep`
    table, never the frame, so once `_load_frame_batched` has copied it,
    `score_fs_out_of_core` nulls the holder slot and the ~1x frame (~5-17 GB at
    50M) is dropped BEFORE the memory-heavy back-half — instead of pinned resident
    as dead weight through it. The pipeline driver rebinds its own frame locals so
    the holder is the only strong reference. (Locked: `test_fs_out_of_core.py`
    weakref test.)
  - **DuckDB buffer bounded so `prep` spills to disk** (`_configure_ooc_duckdb`,
    `memory_limit` ~55% RAM + `temp_directory`) — without a cap DuckDB's default
    ~80%-of-RAM buffer would hold the WHOLE spilled `prep` in RAM (an equivalent
    ~17 GB copy of the frame), defeating the spill design and re-OOMing at 50M.
  - **Golden built in cluster-BATCHES, streamed via `ParquetWriter`**
    (`_fs_ooc_golden_batch_clusters`, default 500K clusters/batch) — the golden
    subset (non-oversized multi-member rows) can be MOST of N at full width, so
    materialising it whole was an O(N) resident spike (measured 7.8 GB at 400K
    rows × 40 cols; batched: 0.74 GB, −10.6x, byte-identical count) — the same
    peak the in-memory 25M golden fix (#334) removed by 500K-cluster batching.

  - **Clustering is memory-bounded by a Rust streaming Union-Find**
    (`GOLDENMATCH_FS_OOC_STREAM_CLUSTER`, `auto`). The one-shot
    `build_clusters_arrow` fetches the whole deduped edge set (O(pairs)) AND builds
    an `edge_pos`/`per_cluster_edges` structure for the confidence/bottleneck
    METADATA this path discards. `_cluster_streaming_from_duckdb` instead streams
    the DuckDB-spilled edges in batches into the native `StreamingClusterBuilder`,
    keeping ONLY the Union-Find parent map (O(members)); singletons fold via a
    DuckDB LEFT JOIN. A pure-Python UF was a NET memory regression (dict-based,
    ~15-20 GB at 100M members), so the kernel is Rust/Arrow-native — the compact
    parent map is the irreducible clustering floor. Same connected-component
    partitions as the one-shot (union-strategy-invariant, parity-tested); `auto`
    routes to it past `GOLDENMATCH_FS_OOC_STREAM_CLUSTER_MIN_PAIRS`, and it falls
    back to the one-shot kernel on an older wheel lacking the symbol.

Remaining work: load-peak reduction below ~1x frame (stream input parquet ->
DuckDB during prep, never materialise the frame at all) + the CI proof that 50M
completes. See
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


def _fs_ooc_spill_pairs_enabled() -> bool:
    """Spill the Arrow pair STREAM to the DuckDB file instead of accumulating every
    wave's pair table in RAM (default ON within the already-opt-in out-of-core
    route).

    The arrow path otherwise holds `pair_tables` — one `PAIR_STREAM` table PER WAVE
    — resident across the whole run, then concatenates + dedups them: an O(pairs)
    driver structure (~1.6 GB at 50M / ~67M pairs, plus the dedup output) that is
    the Arrow-side twin of the prep frame the DuckDB spill already bounds. When on,
    each wave is appended to a DuckDB `pair_stream` table (spills to disk under the
    `memory_limit`), and clustering reads the max-score canonical dedup + best-score
    link filter straight out of DuckDB — so only the BOUNDED deduped edge set is
    ever materialised in RAM (the minimum the Union-Find needs), never the raw
    stream. `GOLDENMATCH_FS_OOC_SPILL_PAIRS=0` restores the in-RAM arrow
    accumulation (the byte-parity oracle)."""
    v = os.environ.get("GOLDENMATCH_FS_OOC_SPILL_PAIRS")
    if v is None:
        return True
    return v.strip().lower() in ("1", "true", "yes", "on")


def _fs_ooc_stream_cluster_mode() -> str:
    """Route for the out-of-core CLUSTERING step: ``auto`` (default) / ``1`` / ``0``.

    ``1`` forces the memory-bounded Rust streaming Union-Find
    (``StreamingClusterBuilder``): edges stream from the DuckDB-spilled
    ``pair_stream`` in waves into a kernel that keeps ONLY the parent map
    (O(members)), never the O(pairs) deduped edge set the one-shot
    ``build_clusters_arrow`` fetches (plus its `edge_pos`/`per_cluster_edges`
    metadata structures the streaming path discards). ``0`` forces the one-shot
    Rust arrow path (the byte-parity oracle for the partitions). ``auto`` uses
    streaming only past ``GOLDENMATCH_FS_OOC_STREAM_CLUSTER_MIN_PAIRS`` — below
    that the one-shot fetch is cheap and its native kernel is a hair faster."""
    v = os.environ.get("GOLDENMATCH_FS_OOC_STREAM_CLUSTER")
    if v is None:
        return "auto"
    v = v.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return "1"
    if v in ("0", "false", "no", "off"):
        return "0"
    return "auto"


def _fs_ooc_stream_cluster_min_pairs() -> int:
    """``auto``-route threshold: use the streaming Union-Find once the deduped edge
    set reaches this many pairs (default 20M — where the one-shot fetch starts to
    matter). ``GOLDENMATCH_FS_OOC_STREAM_CLUSTER_MIN_PAIRS`` overrides."""
    v = os.environ.get("GOLDENMATCH_FS_OOC_STREAM_CLUSTER_MIN_PAIRS")
    if v and v.strip().isdigit() and int(v) > 0:
        return int(v)
    return 20_000_000


def _fs_ooc_wcc_batch() -> int:
    """Edge-batch size for the streaming Union-Find scan (bounds the per-wave
    resident edge rows). ``GOLDENMATCH_FS_OOC_STREAM_CLUSTER_BATCH`` overrides;
    ``0``/invalid -> the 2M default."""
    v = os.environ.get("GOLDENMATCH_FS_OOC_STREAM_CLUSTER_BATCH")
    if v and v.strip().isdigit() and int(v) > 0:
        return int(v)
    return 2_000_000


def _streaming_cluster_symbol_available() -> bool:
    """True iff the native clustering kernel is enabled AND the published/in-tree
    wheel actually exports ``StreamingClusterBuilder`` (the #688-class wheel-skew
    guard — an older wheel degrades gracefully to the one-shot arrow path)."""
    try:
        from goldenmatch.core._native_loader import native_enabled, native_module
        if not native_enabled("clustering"):
            return False
        nm = native_module()
        return nm is not None and hasattr(nm, "StreamingClusterBuilder")
    except Exception:  # noqa: BLE001 - any loader hiccup -> fall back to one-shot
        return False


def _use_streaming_cluster(con: Any, pair_sink: str) -> bool:
    """Decide whether to cluster via the streaming Union-Find. Requires the native
    symbol; ``auto`` additionally gates on the deduped-edge count."""
    mode = _fs_ooc_stream_cluster_mode()
    if mode == "0" or not _streaming_cluster_symbol_available():
        return False
    if mode == "1":
        return True
    n = con.execute(f"SELECT count(*) FROM {_sql_ident(pair_sink)}").fetchone()[0]
    return int(n or 0) >= _fs_ooc_stream_cluster_min_pairs()


def _fs_ooc_golden_batch_clusters() -> int:
    """How many multi-member clusters the streaming golden build materialises per
    batch. The golden subset (non-oversized multi-member rows) can be MOST of N at
    full width, so fetching it whole (one ``fetch_arrow_table`` + ``build_golden_
    records_batch``) is an O(N) resident spike — the same peak the in-memory 25M
    golden fix (#334) removed by processing clusters in 500K batches. This bounds
    the streaming golden peak to one batch's member rows regardless of N.
    ``GOLDENMATCH_FS_OOC_GOLDEN_BATCH`` overrides; ``0``/invalid -> the 500K
    default."""
    v = os.environ.get("GOLDENMATCH_FS_OOC_GOLDEN_BATCH")
    if v and v.strip().isdigit() and int(v) > 0:
        return int(v)
    return 500_000


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


def _fs_ooc_duckdb_memory_limit() -> str | None:
    """DuckDB ``memory_limit`` for the out-of-core FS connections, or ``None`` to
    leave DuckDB's default.

    Without a cap, DuckDB's default buffer is ~80% of system RAM, so at scale it
    holds the ENTIRE spilled ``prep`` table resident in RAM — an equivalent copy
    of the prepared frame — which defeats the spill-to-disk design (the load
    would then keep two ~17 GB copies at 50M and OOM regardless of the Python
    frame being freed). Bounding it forces DuckDB to spill ``prep`` to its temp
    dir and STREAM the sorted block scan from disk, leaving RAM for the Python
    back-half (pair stream + golden build).

    ``GOLDENMATCH_FS_OOC_DUCKDB_MEMORY`` overrides: a DuckDB size string like
    ``'8GB'``, or ``'0'``/``'off'``/``'default'`` to leave DuckDB's default. The
    default caps DuckDB at ~55% of system RAM (min 1 GB), leaving ~45% headroom
    for the driver process."""
    v = os.environ.get("GOLDENMATCH_FS_OOC_DUCKDB_MEMORY")
    if v is not None:
        v = v.strip()
        if v.lower() in ("0", "off", "", "default"):
            return None
        return v
    try:
        total = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except (ValueError, OSError, AttributeError):
        return None
    gib = int(total * 0.55 / (1 << 30))
    if gib < 1:
        return None
    return f"{gib}GB"


def _configure_ooc_duckdb(con: Any, resolved_path: str) -> None:
    """Bound DuckDB's buffer + point its spill dir at the on-disk db, so the
    out-of-core scorer streams ``prep`` from disk instead of buffering it all in
    RAM. No-op for an ``:memory:`` connection (nothing to spill; tests / tiny
    frames). Best-effort: a PRAGMA that a given DuckDB build rejects is swallowed
    so the run never breaks on a version quirk."""
    if resolved_path == ":memory:":
        return
    lim = _fs_ooc_duckdb_memory_limit()
    try:
        if lim:
            con.execute(f"PRAGMA memory_limit='{lim}'")
        con.execute(f"PRAGMA temp_directory={_sql_lit(resolved_path + '.tmp')}")
    except Exception:  # noqa: BLE001 - a rejected PRAGMA must never break the run
        pass


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


def _unwrap_frame_holder(prepared_df: Any) -> tuple[Any, list | None]:
    """Return ``(frame, holder)``.

    The out-of-core FS scorer only needs the prepared frame for the ONE-TIME
    DuckDB load — every downstream stage (score / cluster / output) reads from
    the spilled ``prep`` table, never the frame. So the streaming pipeline routes
    the frame through a single-element ``[frame]`` HOLDER (all its own driver
    locals rebound to ``None``); once ``_load_frame_batched`` has copied it to
    DuckDB, ``score_fs_out_of_core`` sets ``holder[0] = None`` to drop the caller
    chain's only remaining strong reference, so the ~1× prepared frame is NOT
    resident as dead weight through clustering + output (the ~17 GB / 50M lever).

    A bare frame (direct callers / tests) yields ``holder=None`` — no release,
    byte-identical to the pre-holder behavior."""
    if isinstance(prepared_df, list):
        return prepared_df[0], prepared_df
    return prepared_df, None


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
    pair_sink: str | None = None,
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

    _frame, _holder = _unwrap_frame_holder(prepared_df)
    del prepared_df
    native = _tf(_frame).native
    del _frame
    if is_polars_lazyframe(native):
        native = native.collect()
    keep = _needed_columns(native, mk, blocking_config)
    # Project to the scoring columns (dead columns never reach disk). `.select`
    # shares column buffers with `native`, so this is ~free.
    proj = native.select(keep)

    _resolved_path = _resolve_db_path(db_path)
    con = duckdb.connect(_resolved_path)
    _configure_ooc_duckdb(con, _resolved_path)
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
        # Frame is now spilled to DuckDB; every downstream stage reads `prep`,
        # never the frame. Drop the caller chain's last strong reference (the
        # holder slot) so the ~1x prepared frame is freed BEFORE clustering +
        # output rather than held resident as dead weight (the ~17 GB / 50M
        # lever). No-op for a bare-frame caller (holder is None).
        if _holder is not None:
            _holder[0] = None
            del _holder
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
        _spill = _arrow and pair_sink is not None  # spill waves to DuckDB, not RAM
        out: list[tuple[int, int, float]] = []
        seen: set[tuple[int, int]] = set()
        pair_tables: list = []  # arrow mode: one PAIR_STREAM pa.Table per wave
        _sink_created = [False]  # spill mode: lazily CREATE the sink on wave 1
        if _arrow:
            from goldenmatch.backends.score_buckets import pairs_to_pair_stream
        if _spill:
            con.execute(f"DROP TABLE IF EXISTS {_sql_ident(pair_sink)}")

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
                if not wave:
                    return
                wave_tbl = pairs_to_pair_stream(wave)
                if _spill:
                    # Append this wave to the DuckDB sink and DROP it from RAM, so
                    # the pair stream never accumulates in the driver (it spills to
                    # disk under the memory_limit). Clustering reads the dedup back.
                    con.register("_gm_pairwave", wave_tbl)
                    if not _sink_created[0]:
                        con.execute(
                            f"CREATE TABLE {_sql_ident(pair_sink)} AS "
                            "SELECT * FROM _gm_pairwave"
                        )
                        _sink_created[0] = True
                    else:
                        con.execute(
                            f"INSERT INTO {_sql_ident(pair_sink)} "
                            "SELECT * FROM _gm_pairwave"
                        )
                    con.unregister("_gm_pairwave")
                else:
                    pair_tables.append(wave_tbl)
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
                ).fetch_record_batch(1 << 16)

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
        if _spill:
            # Pairs live in the DuckDB `pair_sink` table (spilled). The caller
            # (run_fs_dedupe_streaming) clusters straight from it; nothing to hand
            # back in RAM. Ensure the table EXISTS even when no wave emitted a pair
            # (all-singleton run) so the caller's read is uniform.
            if not _sink_created[0]:
                con.execute(
                    f"CREATE TABLE {_sql_ident(pair_sink)} "
                    "(id_a BIGINT, id_b BIGINT, score DOUBLE)"
                )
            return pair_sink
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
    import pyarrow.parquet as _pq

    # golden = non-oversized multi-member; the subset can be MOST of N at full
    # width, so build it in cluster-BATCHES (like the in-memory #334 fix) and
    # STREAM each batch's golden rows to the parquet via a ParquetWriter — peak
    # stays ~one batch's member rows regardless of N, never the whole subset.
    # `gbatch` buckets qualifying clusters into fixed-size groups by cid order so
    # each batch's member-row fetch is bounded; ranges keep clusters whole (a
    # cluster's rows never split across batches).
    _golden_rules_resolved = (
        golden_rules if golden_rules is not None else _default_golden_rules()
    )
    _batch_clusters = _fs_ooc_golden_batch_clusters()
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE gsizes AS "
        f"SELECT cid, (row_number() OVER (ORDER BY cid) - 1) // {int(_batch_clusters)} "
        f"AS gbatch FROM sizes WHERE n > 1 AND n <= {int(max_cluster_size)}"
    )
    _n_batches = con.execute(
        "SELECT coalesce(max(gbatch), -1) + 1 FROM gsizes"
    ).fetchone()[0]
    golden_count = 0
    _writer = None
    _writer_schema = None
    try:
        for _b in range(int(_n_batches)):
            batch_tbl = con.execute(
                f"SELECT {_sel}, a.__cluster_id__ {base_join} "
                f"JOIN gsizes g ON a.__cluster_id__ = g.cid WHERE g.gbatch = {int(_b)}"
            ).fetch_arrow_table()
            if not batch_tbl.num_rows:
                continue
            records = build_golden_records_batch(
                pl.from_arrow(batch_tbl), _golden_rules_resolved
            )
            del batch_tbl
            if not records:
                continue
            golden_count += len(records)
            gt = pl.DataFrame(records).to_arrow()
            if _writer is None:
                _writer_schema = gt.schema
                _writer = _pq.ParquetWriter(golden_path, _writer_schema)
            else:
                # Later batches must share the first batch's schema (all-null
                # columns can infer a narrower type in a small batch); cast to it.
                if gt.schema != _writer_schema:
                    gt = gt.cast(_writer_schema)
            _writer.write_table(gt)
            del gt, records
    finally:
        if _writer is not None:
            _writer.close()
    con.execute("DROP TABLE IF EXISTS gsizes")
    if golden_count == 0 and _os.path.exists(golden_path):
        # No golden rows this run: remove a golden.parquet left by a PRIOR run
        # into the same out_dir, so the on-disk file set matches the returned
        # golden_path=None / golden_count=0 (unique/dupes are COPY-overwritten;
        # only golden is conditionally written, so only it can go stale).
        _os.unlink(golden_path)

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
    con: Any,
    pair_table: Any,
    max_cluster_size: int,
    link_threshold: float | None,
) -> tuple[Any, int]:
    """Arrow-native path: dedup the ``PAIR_STREAM`` table with the Rust
    ``dedup_pairs_arrow`` kernel, then cluster with ``build_clusters_arrow_native``
    (Rust Union-Find via the C Data Interface — no Python ``dict[int, dict]``).
    Returns ``(assignments pa.Table {__row_id__, __cluster_id__}, n_pairs)`` — the
    Arrow assignments feed ``stream_fs_dedupe_output`` directly, so the scored
    pairs never become Python objects here.

    Fully ARROW-NATIVE: the max-score dedup + link-threshold filter + Union-Find
    all read the pair stream's Arrow buffers directly (no ``pl.from_arrow`` copy of
    the whole pair set — the ~20 B/pair stream is never doubled into a polars
    frame), keeping this path polars-free and the cluster-stage residency at ~1×
    the pair stream instead of ~2×."""
    import pyarrow as pa
    import pyarrow.compute as pc

    from goldenmatch.core.cluster import build_clusters_arrow_native
    from goldenmatch.core.pairs import dedup_pairs_max_score_arrow_table

    # Cross-pass dedup: canonical (min, max), max score — the Arrow-native
    # replacement for the Python `seen` set. Union-Find membership is invariant to
    # which duplicate's score survives, so this is cluster-parity-safe. Reassign so
    # the pre-dedup stream is dropped once the kernel returns the collapsed table.
    pair_table = dedup_pairs_max_score_arrow_table(pair_table)
    if link_threshold is not None:
        # Cluster only linked pairs; sub-link pairs are review candidates the
        # in-memory pipeline surfaces separately and never clusters. Filtering
        # AFTER max-score dedup means a pair links iff its BEST cross-pass score
        # clears the cut (== per-wave filter-then-dedup, since max is monotone).
        pair_table = pair_table.filter(
            pc.greater_equal(pair_table.column("score"), link_threshold)
        )
    n_pairs = pair_table.num_rows

    all_ids = _prep_all_ids(con)
    cf = build_clusters_arrow_native(
        pair_table, all_ids=all_ids, max_cluster_size=max_cluster_size, backend="arrow",
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


def _normalize_assignments(cf: Any) -> Any:
    """``ClusterFrames.assignments`` → a ``pa.Table`` renamed to the
    ``(__row_id__, __cluster_id__)`` shape ``stream_fs_dedupe_output`` joins on."""
    import pyarrow as pa

    asn = cf.assignments
    if not isinstance(asn, pa.Table):
        asn = asn.to_arrow()
    return asn.rename_columns([
        "__cluster_id__" if c == "cluster_id" else "__row_id__"
        for c in asn.column_names
    ])


def _cluster_arrow_native_from_duckdb(
    con: Any,
    pair_sink: str,
    max_cluster_size: int,
    link_threshold: float | None,
) -> tuple[Any, int]:
    """Cluster from the DuckDB-SPILLED ``pair_sink`` table — the Arrow-side twin of
    the DuckDB prep spill.

    The scored pairs were streamed to disk wave-by-wave (never accumulated in RAM),
    so the raw O(pairs) stream is never driver-resident. The max-score canonical
    dedup + best-score link filter run IN DuckDB (``GROUP BY least/greatest`` →
    ``max(score)`` → ``HAVING``, which spills under the ``memory_limit``), so only
    the BOUNDED deduped edge set is materialised into Arrow for the Rust Union-Find
    — the minimum the kernel needs. Semantically identical to
    ``_cluster_arrow_native``'s ``dedup_pairs_max_score_arrow_table`` + score filter
    (canonical ``(min, max)``, max score, link iff best score clears the cut), so
    the cluster assignments match (parity-tested)."""
    from goldenmatch.core.cluster import build_clusters_arrow_native

    having = ""
    if link_threshold is not None:
        # Link iff the BEST cross-pass score clears the cut (== dedup-then-filter).
        having = f"HAVING max(score) >= {float(link_threshold)!r}"
    deduped = con.execute(
        f"SELECT least(id_a, id_b) AS id_a, greatest(id_a, id_b) AS id_b, "
        f"max(score) AS score FROM {_sql_ident(pair_sink)} "
        f"GROUP BY least(id_a, id_b), greatest(id_a, id_b) {having}"
    ).fetch_arrow_table()
    n_pairs = deduped.num_rows

    all_ids = _prep_all_ids(con)
    cf = build_clusters_arrow_native(
        deduped, all_ids=all_ids, max_cluster_size=max_cluster_size, backend="arrow",
    )
    return _normalize_assignments(cf), n_pairs


def _cluster_streaming_from_duckdb(
    con: Any,
    pair_sink: str,
    link_threshold: float | None,
) -> tuple[Any, int]:
    """Cluster with the MEMORY-BOUNDED Rust streaming Union-Find — clustering RAM
    is the parent map (O(members)), never the O(pairs) deduped edge set.

    The max-score canonical dedup + best-score link filter run IN DuckDB (spills
    under the ``memory_limit``) into a ``wcc_edges`` table, which is then STREAMED
    in bounded batches into ``StreamingClusterBuilder.union_batch`` — the native
    kernel keeps only the Union-Find parent map, so the edges never become a
    resident array (nor the one-shot kernel's ``edge_pos``/``per_cluster_edges``
    metadata structures). ``assignments()`` returns ``(member_id, cluster_id)`` for
    pair-touched members (cluster_id = component min member id); singletons fold to
    their own id via a DuckDB LEFT JOIN, so no O(N) id list is materialised. Same
    connected-component partitions as ``_cluster_arrow_native_from_duckdb`` (the
    grouping is union-strategy-invariant), so it's cluster-parity-safe."""
    import pyarrow as pa

    from goldenmatch.core._native_loader import native_module

    having = ""
    if link_threshold is not None:
        having = f"HAVING max(score) >= {float(link_threshold)!r}"
    con.execute(
        f"CREATE OR REPLACE TEMP TABLE wcc_edges AS "
        f"SELECT least(id_a, id_b) AS id_a, greatest(id_a, id_b) AS id_b "
        f"FROM {_sql_ident(pair_sink)} "
        f"GROUP BY least(id_a, id_b), greatest(id_a, id_b) {having}"
    )
    n_pairs = int(con.execute("SELECT count(*) FROM wcc_edges").fetchone()[0] or 0)

    builder = native_module().StreamingClusterBuilder()
    reader = con.execute(
        "SELECT id_a, id_b FROM wcc_edges"
    ).fetch_record_batch(_fs_ooc_wcc_batch())
    for batch in reader:
        builder.union_batch(batch.column("id_a"), batch.column("id_b"))

    member_arr, cid_arr = builder.assignments()
    con.register(
        "wcc_lbl_arrow",
        pa.table({"member_id": member_arr, "__cluster_id__": cid_arr}),
    )
    con.execute("CREATE OR REPLACE TEMP TABLE wcc_labels AS SELECT * FROM wcc_lbl_arrow")
    con.unregister("wcc_lbl_arrow")

    # Fold singletons (rows in no edge) to their own cluster in DuckDB — no O(N)
    # id list in the driver; the assignment table is built by the store.
    asn = con.execute(
        "SELECT p.__row_id__ AS __row_id__, "
        "coalesce(l.__cluster_id__, p.__row_id__) AS __cluster_id__ "
        "FROM prep p LEFT JOIN wcc_labels l ON p.__row_id__ = l.member_id"
    ).fetch_arrow_table()
    con.execute("DROP TABLE IF EXISTS wcc_edges")
    con.execute("DROP TABLE IF EXISTS wcc_labels")
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

    **The Arrow pair stream itself SPILLS to the DuckDB file** (default;
    ``GOLDENMATCH_FS_OOC_SPILL_PAIRS=0`` restores the in-RAM accumulation). Even the
    ~20 B/pair Arrow stream is O(pairs) and accumulates across every wave (~2.4 GB
    at 100M pairs) — the Arrow-side twin of the prep frame the DuckDB spill already
    bounds. So each wave is appended to a DuckDB ``pair_stream`` table (spills under
    the ``memory_limit``), and the max-score canonical dedup + best-score link
    filter run IN DuckDB, leaving only the BOUNDED deduped edge set resident for the
    Union-Find. With this, the whole streaming back-half holds NO O(N)-growing
    driver structure — prep, golden, AND pairs are all disk-bounded.

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
    # Spill the pair STREAM to the same DuckDB file instead of accumulating every
    # wave's Arrow table in the driver — the Arrow-side twin of the prep spill.
    # Clustering then reads the max-score dedup + link filter straight out of
    # DuckDB (bounded), so the raw O(pairs) stream is never RAM-resident. Only
    # within the arrow-cluster route; `=0` restores the in-RAM accumulation.
    spill_pairs = arrow_stream and _fs_ooc_spill_pairs_enabled()
    try:
        # 1+2: load frame into the persistent file + score (frame freed on return).
        pairs = score_fs_out_of_core(
            prepared_df, blocking_config, mk, matched_pairs, em_result,
            target_ids=target_ids, db_path=db_path,
            emit="arrow" if arrow_stream else "tuples",
            pair_sink="pair_stream" if spill_pairs else None,
        )
        con = duckdb.connect(db_path)
        _configure_ooc_duckdb(con, db_path)
        try:
            if spill_pairs:
                # `pairs` is the DuckDB sink table name (always created, possibly
                # empty -> an all-singleton run). Cluster with the memory-bounded
                # streaming Union-Find at the large-N tail (O(members) parent map,
                # no O(pairs) edge fetch); the one-shot arrow kernel below the
                # threshold / when the native symbol is absent.
                if _use_streaming_cluster(con, pairs):
                    assignments, n_pairs = _cluster_streaming_from_duckdb(
                        con, pairs, link_threshold,
                    )
                else:
                    assignments, n_pairs = _cluster_arrow_native_from_duckdb(
                        con, pairs, max_cluster_size, link_threshold,
                    )
            elif arrow_stream:
                assignments, n_pairs = _cluster_arrow_native(
                    con, pairs, max_cluster_size, link_threshold,
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
