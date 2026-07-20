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

from typing import Any

from goldenmatch.config.schemas import BlockingConfig, MatchkeyConfig


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
    import numpy as np
    import polars as pl

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
    # Project to the scoring columns before landing in DuckDB (the frame's dead
    # columns never reach disk). Arrow for a zero-copy DuckDB load.
    proj = native.select(keep) if is_polars_dataframe(native) else native.select(keep)
    arrow_tbl = proj.to_arrow() if hasattr(proj, "to_arrow") else proj

    con = duckdb.connect(db_path or ":memory:")
    try:
        con.register("prep_arrow", arrow_tbl)
        con.execute("CREATE TABLE prep AS SELECT * FROM prep_arrow")
        con.unregister("prep_arrow")
        del arrow_tbl, proj, native
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

        for pass_config in passes:
            # Compute the block key exactly as build_blocks does, then group by it
            # to get the row-ids per block. The grouping pass pulls ONLY
            # __row_id__ + this pass's blocking columns from DuckDB (a thin
            # index, not the full scoring frame); the row DATA is pulled per
            # block below. (Phase 2 pushes even this key derivation into a
            # streaming/SQL pass so only the row_id lists transit Python.)
            key_expr = _build_block_key_expr(pass_config)
            _key_cols = ["__row_id__"] + [
                f for f in dict.fromkeys(pass_config.fields)
                if f != "__row_id__"
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

            for block_key, ids in zip(
                grouped["__block_key__"].to_list(),
                grouped["__row_id__"].to_list(),
            ):
                if block_key is None or len(ids) < 2:
                    continue
                if len(ids) > max_block_rows:
                    # Oversized: score_buckets auto-splits; we cap to avoid the
                    # quadratic. Documented parity edge (bench-gated).
                    ids = ids[:max_block_rows]
                # Pull ONLY this block's rows from DuckDB (bounded memory).
                id_list = ",".join(str(int(r)) for r in ids)
                block_tbl = con.execute(
                    f"SELECT * FROM prep WHERE __row_id__ IN ({id_list})"
                ).arrow()
                block_pl = pl.from_arrow(block_tbl)

                if use_native:
                    pairs = score_probabilistic_bucket_native(
                        block_pl, [block_pl.height], mk, em_result, frozen_exclude,
                    )
                else:
                    pairs = prob_scorer(block_pl, frozen_exclude)

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
        return out
    finally:
        con.close()
