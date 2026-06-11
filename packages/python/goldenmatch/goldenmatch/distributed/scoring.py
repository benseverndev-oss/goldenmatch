"""Distributed scoring via per-partition dedupe + cross-partition pair dedup.

Phase 5 of the Splink-Spark parity roadmap. See
docs/superpowers/specs/2026-05-19-phase-5-multi-node-parity-design.md.

Strategy: each partition runs the full in-memory dedupe_df pipeline up
through scoring (cheap on a small partition), and we emit the resulting
scored_pairs list as rows. Cross-partition collisions are deduped by
dedup_pairs_distributed.

This is intentionally coarse -- we don't try to distribute scoring at a
finer granularity than partition. The win at scale is that each
partition's scorer runs in parallel on a different worker.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pyarrow as pa  # noqa: F401  used in inline type annotation comments
    from ray.data import Dataset

    from goldenmatch.config.schemas import GoldenMatchConfig

logger = logging.getLogger(__name__)

# Per-task CPU reservation for the scoring map_batches call.
#
# History:
#   - num_cpus=1 (original, pre-PR #395): OOM at 50M -- 7 concurrent
#     gm.dedupe_df calls @ ~5 GB each = ~35 GB worker RAM > 64 GB cap.
#   - num_cpus=4 (PR #395): Ray reserved 4 CPU for downstream HashAggregate,
#     leaving only 4 for scoring -> 1 task at a time -> 0% progress at 52 min.
#   - num_cpus=1 + narrow kernel (PR #397, #396): OOM at 50M -- the kernel
#     stripped controller/clustering/golden but didn't touch score_buckets,
#     which still allocates ~5-9 GB cdist matrices per partition. 7 concurrent
#     * ~5 GB = ~30 GB > 64 GB - object_store(8 GB) - parquet(4.4 GB) - driver.
#   - num_cpus=2 (current): 8 free CPU / 2 = 4 concurrent * ~5 GB = ~20 GB
#     worker RAM. Fits with headroom. Trades parallelism for survival.
#
# The real fix for 50M-on-64GB is smaller n_buckets inside score_buckets so
# per-partition cdist matrices stay small. That's a separate lift (#???).
# This setting is the practical knob until then.
#
# Override via GOLDENMATCH_DISTRIBUTED_SCORE_NUM_CPUS for different shapes.
_SCORE_NUM_CPUS = int(os.environ.get("GOLDENMATCH_DISTRIBUTED_SCORE_NUM_CPUS", "2"))


def _block_shuffle_enabled() -> bool:
    """Gate for the blocking-key-aware shuffle scoring path (issue #844).

    Default ON as of the #844 finish line: ``score_blocks_distributed``
    co-locates records that share a blocking key (or exact-matchkey value)
    before scoring, closing the cross-partition recall hole that the legacy
    per-partition path left open (it under-merged inversely with partition
    count). This was gated opt-in until the recall-complete path was validated
    end-to-end at 100M -- it now is: full e2e in 9.2 min with byte-exact cluster
    recovery, and the per-group scoring wall that made it non-viable is fixed
    (``_score_colocated_groups`` scores each partition in one vectorized pass).

    Set ``GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE=0`` to restore the legacy
    per-partition behavior. NOTE: when on, scored pairs cross input-partition
    boundaries, so clustering routes through the distributed
    randomized-contraction WCC (``_phase5_cluster``), which on a MULTI-NODE
    cluster requires a SHARED ``GOLDENMATCH_DISTRIBUTED_WCC_SCRATCH=gs://...``
    path (enforced in ``randomized_contraction_wcc``).
    """
    return os.environ.get("GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE", "1") not in (
        "0", "", "false", "False", "no", "off",
    )


def _has_colocation_plan(config: GoldenMatchConfig) -> bool:
    """True when the config gives the block-shuffle path something to key on:
    at least one blocking pass/key or at least one exact matchkey. When False,
    ``score_blocks_distributed`` falls back to the legacy per-partition path
    (no co-location signal to exploit anyway)."""
    matchkeys = config.get_matchkeys() or []
    if any(getattr(mk, "type", None) == "exact" for mk in matchkeys):
        return True
    blocking = getattr(config, "blocking", None)
    if blocking is not None and (blocking.passes or blocking.keys):
        return True
    return False


def score_blocks_distributed(
    df_ds: Dataset,
    config: GoldenMatchConfig,
) -> Dataset:
    """Distributed per-partition scoring -> Ray Dataset of {id_a, id_b, score}.

    Two paths:
      * default: ``_score_blocks_legacy`` scores each input partition in
        isolation (byte-identical to the prior behavior).
      * opt-in (``GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE=1``):
        ``_score_blocks_block_shuffle`` co-locates records that share a
        blocking key / exact-matchkey value BEFORE scoring, fixing the
        cross-partition recall hole in issue #844. Kept opt-in because the
        shuffle makes pairs cross input-partition boundaries, so clustering
        then needs a real distributed WCC rather than ``local_cc_assignments``
        (the WCC-at-scale question gates flipping the default).
    """
    if _block_shuffle_enabled() and _has_colocation_plan(config):
        return _score_blocks_block_shuffle(df_ds, config)
    return _score_blocks_legacy(df_ds, config)


def _score_blocks_legacy(
    df_ds: Dataset,
    config: GoldenMatchConfig,
) -> Dataset:
    """Per-partition fuzzy + exact scoring via the narrow scoring kernel.

    Returns a Ray Dataset of {id_a, id_b, score} rows. Cross-partition
    collisions stay; caller invokes dedup_pairs_distributed to canonicalize.

    Each worker runs ``_score_partition_with_config`` -- scoring only,
    no controller, no clustering, no golden records. The driver auto-
    configures once on a sample (Phase 2) before dispatch; workers
    receive the committed config and execute the cheap scoring kernel.

    NOTE: scores each arbitrary input partition in isolation, so two records in
    different partitions are never compared. With blocking-unaware partitioning
    (the default loader does ``ds.repartition(n)``), cross-partition duplicates
    are missed and recall scales inversely with partition count (issue #844).
    The opt-in block-shuffle path closes that gap.
    """

    def _score_partition(batch: Any) -> Any:  # batch: pa.Table -> pa.Table
        import copy

        import polars as pl
        import pyarrow as pa

        from goldenmatch.core.pipeline import _score_partition_with_config

        df = pl.from_arrow(batch)
        assert isinstance(df, pl.DataFrame)
        if df.height < 2:
            return pa.table({"id_a": [], "id_b": [], "score": []})

        # Force the in-memory bucket backend so the per-partition scorer
        # doesn't recursively try to distribute. Kernel honors this too.
        if hasattr(config, "model_copy"):
            local_cfg = config.model_copy()
        else:
            local_cfg = copy.deepcopy(config)
        local_cfg.backend = "bucket"

        try:
            pairs = _score_partition_with_config(df, local_cfg)
        except Exception as e:
            logger.warning("partition scoring failed: %s", e)
            return pa.table({"id_a": [], "id_b": [], "score": []})

        if not pairs:
            return pa.table({"id_a": [], "id_b": [], "score": []})
        return pa.table({
            "id_a":  [int(a) for a, _b, _s in pairs],
            "id_b":  [int(b) for _a, b, _s in pairs],
            "score": [float(s) for _a, _b, s in pairs],
        })

    logger.info(
        "score_blocks_distributed: dispatching with num_cpus=%d per task "
        "(GOLDENMATCH_DISTRIBUTED_SCORE_NUM_CPUS to override)",
        _SCORE_NUM_CPUS,
    )
    return df_ds.map_batches(
        _score_partition,
        batch_format="pyarrow",
        num_cpus=_SCORE_NUM_CPUS,
    )


def _attach_colocation_keys(df: Any, config: GoldenMatchConfig) -> Any:
    """Explode one record into ``(record, __keyid__, __block_key__)`` rows -- one
    per co-location key the record participates in: every blocking pass (block
    key) and every exact matchkey (matchkey value). Pure Polars, no Ray.

    The original record columns pass through UNTOUCHED so the downstream scorer
    re-preps normally; only ``__keyid__`` / ``__block_key__`` are added. The key
    is computed on the STANDARDIZED view (mirroring the in-memory blocker, which
    standardizes before blocking) so the shuffle key agrees with the within-group
    re-block -- computing it on raw fields would silently drop pairs.

    Returns a Polars DataFrame (possibly empty) with the two extra columns.
    """
    import polars as pl

    from goldenmatch.core.blocker import _build_block_key_expr

    matchkeys = config.get_matchkeys() or []

    std_df = df
    std = getattr(config, "standardization", None)
    if std is not None and getattr(std, "rules", None):
        try:
            from goldenmatch.core.standardize import apply_standardization
            std_df = apply_standardization(df.lazy(), std.rules).collect()
        except Exception as e:  # fall back to raw fields for keying
            logger.warning("block-shuffle: standardization for keys failed: %s", e)
            std_df = df

    pieces: list[Any] = []

    blocking = getattr(config, "blocking", None)
    if blocking is not None:
        passes = blocking.passes or blocking.keys or []
        for i, key_cfg in enumerate(passes):
            try:
                key_series = std_df.select(_build_block_key_expr(key_cfg))["__block_key__"]
                pieces.append(
                    df.with_columns(
                        key_series.cast(pl.Utf8).alias("__block_key__"),
                        pl.lit(f"pass:{i}").alias("__keyid__"),
                    )
                )
            except Exception as e:
                logger.warning("block-shuffle: pass %d key build failed: %s", i, e)

    exact_mks = [mk for mk in matchkeys if getattr(mk, "type", None) == "exact"]
    if exact_mks:
        try:
            from goldenmatch.core.matchkey import compute_matchkeys
            mk_df = compute_matchkeys(std_df.lazy(), exact_mks).collect()
            for mk in exact_mks:
                col = f"__mk_{mk.name}__"
                if col not in mk_df.columns:
                    continue
                pieces.append(
                    df.with_columns(
                        mk_df[col].cast(pl.Utf8).alias("__block_key__"),
                        pl.lit(f"exact:{mk.name}").alias("__keyid__"),
                    )
                )
        except Exception as e:
            logger.warning("block-shuffle: exact matchkey key build failed: %s", e)

    if not pieces:
        return df.clear().with_columns(
            pl.lit(None).cast(pl.Utf8).alias("__block_key__"),
            pl.lit(None).cast(pl.Utf8).alias("__keyid__"),
        )

    exploded = pl.concat(pieces, how="vertical_relaxed")
    # Drop null/blank keys (no co-location signal; mirrors the blocker's null
    # filter -- records with a null block key must not all share one block).
    return exploded.filter(
        pl.col("__block_key__").is_not_null()
        & (pl.col("__block_key__").str.strip_chars() != "")
    )


def _score_colocated_groups(
    df: Any, config: GoldenMatchConfig,
) -> list[tuple[int, int, float]]:
    """Score the co-located records in this batch in a SINGLE vectorized pass.
    Returns ``list[(id_a, id_b, score)]``.

    #844 (b): the original implementation looped
    ``df.group_by([__keyid__, __block_key__])`` and ran the full per-partition
    kernel ONCE PER GROUP -- ~20M fixed-overhead invocations at 100M (standardize
    / compute_matchkeys / a ``.collect()`` per ~5-row group), which was THE e2e
    wall (0/64 score-tasks finished in 25 min on the real run). It was also
    redundant: the kernel's ``bucket`` backend already groups by the blocking key
    internally. So drop the co-location columns, de-duplicate by ``__row_id__``
    (a record can appear in this partition via several co-location keys that
    hashed here), and score the whole partition ONCE.

    Equivalence to the loop (parity-tested):
      * Exact matchkeys are found by ``_score_partition_with_config``'s
        whole-partition self-join; all records sharing an exact value are
        co-located in this partition by construction, so the pair set is
        identical to scoring each exact group separately.
      * Weighted matchkeys are bucketed by the blocking config; every record
        sharing a blocking key is co-located in this partition, so re-blocking
        re-derives the same groups the loop scored.
      * Over-emitted duplicate edges (a pair surfaced under more than one key)
        remain harmless -- Union-Find is idempotent.
    """
    import copy

    from goldenmatch.core.pipeline import _score_partition_with_config

    if df.height < 2:
        return []

    if hasattr(config, "model_copy"):
        local_cfg = config.model_copy()
    else:
        local_cfg = copy.deepcopy(config)
    local_cfg.backend = "bucket"

    rec = df.drop(["__keyid__", "__block_key__"])
    # A record is exploded once per co-location key; several of its copies can
    # hash to this partition. Keep one per global __row_id__ so the kernel sees
    # each logical record once (else the self-join double-counts it in a bucket).
    if "__row_id__" in rec.columns:
        rec = rec.unique(subset=["__row_id__"], keep="any")
    if rec.height < 2:
        return []
    try:
        return _score_partition_with_config(rec, local_cfg)
    except Exception as e:
        logger.warning("block-shuffle: partition scoring failed: %s", e)
        return []


def _score_blocks_block_shuffle(
    df_ds: Dataset,
    config: GoldenMatchConfig,
) -> Dataset:
    """Blocking-key-aware shuffle scoring (issue #844, opt-in).

    1. Explode each record to ``(record, __keyid__, __block_key__)`` rows, one
       per blocking pass + exact matchkey it participates in.
    2. ``repartition(keys=[__keyid__, __block_key__])`` so every record sharing
       a co-location key lands in one partition, regardless of which arbitrary
       input partition it started in.
    3. Score within each co-located group. ``batch_size=None`` keeps a partition
       whole so a co-located group is never split across sub-batches (the same
       guard ``local_cc_assignments`` uses). Returns {id_a, id_b, score}.

    This is the recall-complete candidate generation the legacy path lacks. The
    downstream clustering step must use a real distributed WCC (not
    ``local_cc_assignments``), since pairs now cross input-partition boundaries.

    PERF NOTE (#844, measured on a real 5-node 100M run, 2026-06-11): this path
    is the e2e wall, NOT the WCC (the WCC clears 200M edges in 266s in
    isolation). TWO costs:

    1. PER-GROUP SCORING -- FIXED. ``_score_colocated_groups`` used to loop
       ``df.group_by([__keyid__, __block_key__])`` and run the full kernel ONCE
       PER GROUP (~20M fixed-overhead invocations at 100M; 0/64 score-tasks
       finished in 25 min). It now scores the whole partition in one vectorized
       pass -- the bucket backend already groups by the blocking key internally,
       so the loop was redundant. See ``_score_colocated_groups``.

    2. FULL-RECORD SHUFFLE -- still open (secondary). ``_explode`` emits a copy
       of the FULL record per co-location key (#passes + #exact matchkeys), so
       the shuffle moves ``N_keys x N_rows x full_record_width`` (~13-27 GB at
       100M). ``_score`` only needs ``__row_id__`` + config-referenced fields.
       Fix (deferred behind the backend-parity gate + a wide-record bench):
       project ``df`` to ``{__row_id__} U columns referenced by
       matchkeys/blocking/standardization`` BEFORE ``_attach_colocation_keys``
       (big win on wide records). Secondary: dedupe the explode when an exact
       matchkey's key equals a blocking pass's key (block + exact-match the same
       field doubles the copies).
    """
    cpu = os.cpu_count() or 16
    n_parts = min(256, max(4, cpu * 4))

    def _explode(batch: Any) -> Any:  # pa.Table -> pa.Table
        import polars as pl
        df = pl.from_arrow(batch)
        assert isinstance(df, pl.DataFrame)
        return _attach_colocation_keys(df, config).to_arrow()

    def _score(batch: Any) -> Any:  # pa.Table -> pa.Table
        import polars as pl
        import pyarrow as pa
        df = pl.from_arrow(batch)
        assert isinstance(df, pl.DataFrame)
        pairs = _score_colocated_groups(df, config)
        if not pairs:
            return pa.table({"id_a": [], "id_b": [], "score": []})
        return pa.table({
            "id_a":  [int(a) for a, _b, _s in pairs],
            "id_b":  [int(b) for _a, b, _s in pairs],
            "score": [float(s) for _a, _b, s in pairs],
        })

    exploded = df_ds.map_batches(_explode, batch_format="pyarrow")
    colocated = exploded.repartition(n_parts, keys=["__keyid__", "__block_key__"])
    logger.info(
        "score_blocks_distributed: BLOCK-SHUFFLE path (opt-in via "
        "GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE); %d shuffle partitions, "
        "num_cpus=%d per task",
        n_parts, _SCORE_NUM_CPUS,
    )
    return colocated.map_batches(
        _score, batch_format="pyarrow", batch_size=None, num_cpus=_SCORE_NUM_CPUS,
    )


def _dedup_num_partitions() -> int:
    """Hash-shuffle partition count for the distributed pair dedup. Mirrors
    the golden build's `min(256, max(4, cpu*4))` heuristic: enough partitions
    for parallelism, capped so shuffle coordination stays cheap."""
    cpu = os.cpu_count() or 16
    return min(256, max(4, cpu * 4))


def dedup_pairs_distributed(pairs_ds: Dataset) -> Dataset:
    """Cross-partition pair dedup. Canonicalizes (id_a, id_b) to (min, max)
    and keeps the maximum score per canonical pair.

    Fully distributed -- no driver collect. The prior implementation
    (v42c cheat-line) collected ALL canonical pairs to the driver via
    `list(canonical.iter_rows())`, deduped in Polars, and round-tripped
    back. At 5M-realistic that's ~18M pairs (~tolerable); at 100M it's the
    primary head-wedge -- hundreds of millions of Python dict rows on the
    driver (proven on a real 4-node GCP cluster: workers idle, head OOM).

    The fix is the hash-shuffle the old comment said was "the right
    architectural answer": after canonicalization, `id_a == min(pair)`, so
    every copy of a given canonical pair shares the SAME `id_a`. Hash-
    partitioning on `id_a` (`repartition(keys=["id_a"])`) co-locates all
    copies of each pair in one partition, and a per-partition Polars
    `group_by(["id_a","id_b"]).max()` then dedups locally -- the same
    co-location trick `build_golden_records_distributed` uses on
    `__cluster_id__`, avoiding Ray's single-partition `groupby().max()`
    HashAggregate hang. Output schema {id_a, id_b, score}.
    """

    def _canonicalize(batch: Any) -> Any:  # batch: pa.Table -> pa.Table
        import polars as pl
        df = pl.from_arrow(batch)
        assert isinstance(df, pl.DataFrame)
        out = df.with_columns([
            pl.min_horizontal("id_a", "id_b").alias("id_a"),
            pl.max_horizontal("id_a", "id_b").alias("id_b"),
        ])
        return out.to_arrow()

    canonical = pairs_ds.map_batches(_canonicalize, batch_format="pyarrow")

    # Hash-partition on id_a so identical canonical pairs co-locate, then
    # dedup within each partition. No driver materialization.
    repartitioned = canonical.repartition(
        _dedup_num_partitions(), keys=["id_a"],
    )

    def _dedup_within_partition(batch: Any) -> Any:  # pa.Table -> pa.Table
        import polars as pl
        df = pl.from_arrow(batch)
        assert isinstance(df, pl.DataFrame)
        if df.height == 0:
            return df.to_arrow()
        out = df.group_by(["id_a", "id_b"]).max()
        # Polars group_by(...).max() keeps key columns and aggregates the
        # rest. Normalize the score column name if Polars renamed it.
        if "score" not in out.columns:
            for c in out.columns:
                if c not in ("id_a", "id_b"):
                    out = out.rename({c: "score"})
                    break
        return out.to_arrow()

    return repartitioned.map_batches(
        _dedup_within_partition, batch_format="pyarrow",
    )
