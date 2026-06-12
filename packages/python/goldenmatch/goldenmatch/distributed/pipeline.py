"""Distributed pipeline orchestrator.

Phase 2 cheat-line (default): materialize the input via take_all, then run
in-memory dedupe_df.

Phase 4 (opt-in via GOLDENMATCH_DISTRIBUTED_PIPELINE=1): same call shape,
but `core.cluster.build_clusters` and `core.golden.build_golden_records_batch`
are now polymorphic on Ray Dataset, so callers that hand a Dataset to
those entry points get the distributed path. Phase 4's pipeline-level
implementation still collects for scoring (Phase 5 distributes that).

Phase 5 (opt-in via GOLDENMATCH_DISTRIBUTED_PIPELINE=2): fully distributed
load -> score -> cluster -> golden -> write. No driver-side take_all on input.
"""
from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

import polars as pl

if TYPE_CHECKING:
    from ray.data import Dataset

logger = logging.getLogger(__name__)


def _phase4_pipeline_enabled() -> bool:
    return os.environ.get("GOLDENMATCH_DISTRIBUTED_PIPELINE") == "1"


def _phase5_pipeline_enabled() -> bool:
    return os.environ.get("GOLDENMATCH_DISTRIBUTED_PIPELINE") == "2"


def run_dedupe_pipeline_distributed(ds: Dataset, **kwargs: Any):
    """Run dedupe on a Ray Dataset input.

    GOLDENMATCH_DISTRIBUTED_PIPELINE=2: Phase 5 streaming (no take_all on input).
    GOLDENMATCH_DISTRIBUTED_PIPELINE=1: Phase 4 scaffold (kept for compat).
    Unset: Phase 2 cheat-line (default; back-compat).
    """
    if _phase5_pipeline_enabled():
        return _run_phase5_pipeline(ds, **kwargs)
    if _phase4_pipeline_enabled():
        return _run_phase4_pipeline(ds, **kwargs)
    return _run_phase2_cheat_line(ds, **kwargs)


def _run_phase2_cheat_line(ds: Dataset, **kwargs: Any):
    from goldenmatch import dedupe_df

    rows = ds.take_all()
    df = pl.from_dicts(list(rows))
    return dedupe_df(df, **kwargs)


def _run_phase4_pipeline(ds: Dataset, **kwargs: Any):
    """Phase 4 v1: same behavior as Phase 2 cheat-line for now.

    The polymorphic dispatch in core.cluster.build_clusters and
    core.golden.build_golden_records_batch already routes Ray Dataset
    callers to the distributed path. Phase 5 retires the take_all here
    by distributing the scoring stage end-to-end.
    """
    from goldenmatch import dedupe_df

    rows = ds.take_all()
    df = pl.from_dicts(list(rows))
    return dedupe_df(df, **kwargs)


def _phase5_cluster(raw_pairs_ds: Dataset, cfg: Any) -> Dataset:
    """Choose the Phase-5 clustering step.

    When the block-shuffle recall-complete SCORING path is active (the same
    detection ``score_blocks_distributed`` uses), pairs cross input-partition
    boundaries, so per-partition Union-Find would under-merge -> route to the
    distributed WCC (``build_clusters_distributed`` forced to
    ``randomized_contraction``; ``two_phase`` head-wedges at 100M). Otherwise
    scoring is per-partition and ``local_cc_assignments`` (cheap, no driver
    collect, correct) is used -- the default.
    """
    from goldenmatch.distributed.clustering import (
        build_clusters_distributed,
        local_cc_assignments,
    )
    from goldenmatch.distributed.scoring import (
        _block_shuffle_enabled,
        _has_colocation_plan,
    )

    if _block_shuffle_enabled() and _has_colocation_plan(cfg):
        logger.info(
            "phase5 clustering: block-shuffle active -> build_clusters_distributed "
            "(randomized_contraction; cross-partition pairs need real WCC)",
        )
        return build_clusters_distributed(
            raw_pairs_ds, all_ids=None, algorithm="randomized_contraction",
        )
    logger.info("phase5 clustering: per-partition scoring -> local_cc_assignments")
    return local_cc_assignments(raw_pairs_ds)


def _run_phase5_pipeline(
    ds: Dataset,
    *,
    output_path: str | None = None,
    confidence_required: bool = True,
    config: Any | None = None,
    allow_red_config: bool = False,
    **kwargs: Any,
):
    """Phase 5 streaming: score -> cluster -> golden -> write, no driver take-alls.

    Fully distributed end to end. The cluster assignments stay a Ray Dataset
    all the way to golden -- the ``materialize_cluster_dict`` adapter (which
    collected every member + every pair into a driver ``dict[int, dict]`` and
    wedged the head at 100M) is gone, as is the ``dedup_pairs_distributed``
    driver-collect. Rows are annotated with ``__cluster_id__`` via a
    distributed hash join (not a broadcast member->cid dict), and golden is
    built by the distributed groupby. Proven on a real 4-node GCP cluster: the
    old path wedged the driver at 100M while workers sat idle.

    Requires a GLOBAL ``__row_id__`` on the input rows (carried in the data).
    Without it each partition synthesizes local ids that collide across
    partitions and WCC merges unrelated clusters -- see
    ``_score_partition_with_config``'s ``__row_id__`` guard and the generator.
    """
    from goldenmatch.config.schemas import GoldenRulesConfig
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.distributed.golden import build_golden_records_distributed
    from goldenmatch.distributed.scoring import score_blocks_distributed

    # 1. Honor an explicit caller config (#739); otherwise auto-configure via the
    #    Phase 2 controller (handles sample collect from Dataset). ``allow_red_config``
    #    is the documented escape hatch (post-#715, NOT ``confidence_required``), so it
    #    must reach ``auto_configure_df`` -- dropping it silently re-raised
    #    ``ControllerNotConfidentError`` at scale even when the caller opted in.
    if config is None:
        cfg = auto_configure_df(
            ds,
            confidence_required=confidence_required,
            allow_red_config=allow_red_config,
            _skip_finalize=True,
        )
    else:
        cfg = config

    # 2. Distributed scoring -> Ray Dataset of pairs (RAW: per-partition, so each
    #    component's edges are co-located in one block -- required by local_cc).
    raw_pairs_ds = score_blocks_distributed(ds, cfg)

    # 3. Connected components: branch on whether block-shuffle is active.
    #    block-shuffle OFF (default): scoring is per-partition, so components
    #    never span partitions -- local_cc_assignments (single map_batches,
    #    no driver collect, no distributed-WCC iteration) is correct and cheap.
    #    block-shuffle ON + co-location plan: pairs cross input-partition
    #    boundaries, so per-partition Union-Find would under-merge; route to
    #    build_clusters_distributed(algorithm="randomized_contraction") instead.
    assignments_ds = _phase5_cluster(raw_pairs_ds, cfg)

    # 4. Distributed hash join: annotate rows with __cluster_id__ (multi-member
    #    only). No broadcast member->cid dict.
    multi_ds = _join_assignments_distributed(ds, assignments_ds)

    # 5. Distributed golden (groupby __cluster_id__ via repartition + map_batches)
    #    -> a Ray Dataset. Stays distributed; NOT collected to the driver.
    rules = cfg.golden_rules or GoldenRulesConfig()
    user_columns = [c for c in _row_columns(ds) if not c.startswith("__")]
    golden_ds = build_golden_records_distributed(
        multi_ds, rules, user_columns=user_columns,
    )

    # 6. Distributed write: each partition writes its own part file. NEVER
    #    materialize golden on the driver -- build_golden_records_smart's
    #    materialize_golden_dataframe(...).to_dicts() pulled ~20M golden records
    #    to the head and wedged it (workers idle) at 100M. write_parquet on the
    #    Dataset keeps the whole tail distributed.
    if output_path is not None:
        golden_ds.write_parquet(output_path)

    from goldenmatch._api import DedupeResult
    return DedupeResult(
        clusters={},  # intentionally NOT materialized (the 100M head-wedge)
        golden=None,  # written to disk via output_path (distributed)
        config=cfg,
        stats={},
    )


def _row_columns(ds: Dataset) -> list[str]:
    """Column names of a Ray Dataset without forcing a data materialization
    (schema fetch only reads metadata)."""
    try:
        return list(ds.schema().names)
    except Exception:  # pragma: no cover - schema() shape varies by Ray version
        return []


def _join_assignments_distributed(
    rows_ds: Dataset,
    assignments_ds: Dataset,
    *,
    num_partitions: int | None = None,
) -> Dataset:
    """Annotate each input row with ``__cluster_id__`` via a DISTRIBUTED hash
    join -- the scale replacement for ``_join_clusters_to_rows``'s broadcast
    ``member_to_cid`` dict (which was O(members) on the driver).

    ``assignments_ds`` rows: {member_id, cluster_id, cluster_size, oversized}.
    Only MULTI-MEMBER clusters survive (``cluster_size > 1``), matching the old
    join's ``size > 1`` filter -- singletons are dropped (golden's contract).
    The join is ``rows.__row_id__ == assignments.member_id`` (inner), so only
    rows belonging to a multi-member cluster flow downstream, each carrying its
    ``__cluster_id__``.
    """
    import os

    import polars as pl

    if num_partitions is None:
        cpu = os.cpu_count() or 16
        num_partitions = min(256, max(4, cpu * 4))

    def _project_multi(batch: Any) -> Any:  # pa.Table -> pa.Table
        df = pl.from_arrow(batch)
        assert isinstance(df, pl.DataFrame)
        if df.height == 0:
            return pl.DataFrame(
                schema={"member_id": pl.Int64, "__cluster_id__": pl.Int64},
            ).to_arrow()
        df = df.filter(pl.col("cluster_size") > 1)
        out = df.select(
            pl.col("member_id").cast(pl.Int64),
            pl.col("cluster_id").cast(pl.Int64).alias("__cluster_id__"),
        )
        return out.to_arrow()

    assign = assignments_ds.map_batches(_project_multi, batch_format="pyarrow")

    joined = rows_ds.join(
        assign,
        join_type="inner",
        num_partitions=num_partitions,
        on=("__row_id__",),
        right_on=("member_id",),
    )

    def _drop_member(batch: Any) -> Any:  # pa.Table -> pa.Table
        df = pl.from_arrow(batch)
        assert isinstance(df, pl.DataFrame)
        if "member_id" in df.columns:
            df = df.drop("member_id")
        return df.to_arrow()

    return joined.map_batches(_drop_member, batch_format="pyarrow")


def _join_clusters_to_rows(ds: Dataset, clusters: dict) -> Dataset:
    """Annotate each row with __cluster_id__ via a small broadcast lookup.

    Only multi-member clusters are kept; singletons are filtered out.
    The cluster dict is small (~cluster_count entries); broadcast via
    ray.put to avoid re-serializing it per batch.
    """
    import ray

    member_to_cid: dict[int, int] = {}
    for cid, info in clusters.items():
        if info.get("size", 0) > 1:
            for m in info["members"]:
                member_to_cid[m] = cid

    if not member_to_cid:
        # No multi-member clusters; return an empty slice of the input schema.
        return ds.limit(0)

    map_ref = ray.put(member_to_cid)

    def _annotate(batch: Any) -> Any:  # noqa: ANN401  # batch: pa.Table -> pa.Table
        import polars as pl
        import ray as _ray
        df = pl.from_arrow(batch)
        assert isinstance(df, pl.DataFrame)
        if "__row_id__" not in df.columns:
            df = df.with_row_index("__row_id__").with_columns(
                pl.col("__row_id__").cast(pl.Int64)
            )
        lookup_raw = _ray.get(map_ref)
        lookup: dict[int, int] = dict(lookup_raw) if not isinstance(lookup_raw, dict) else lookup_raw
        keys = list(lookup.keys())
        vals = list(lookup.values())
        out = df.with_columns(
            pl.col("__row_id__").replace_strict(
                keys, vals, default=-1,
            ).alias("__cluster_id__")
        ).filter(pl.col("__cluster_id__") != -1)
        return out.to_arrow()

    return ds.map_batches(_annotate, batch_format="pyarrow")


def _write_golden_output(golden: Any, output_path: str) -> None:
    """Write golden records to parquet at end of Phase 5 pipeline."""
    if isinstance(golden, list):
        if golden:
            pl.DataFrame(golden).write_parquet(output_path)
        return
    # Polars DataFrame
    if hasattr(golden, "write_parquet"):
        golden.write_parquet(output_path)
    else:
        pl.DataFrame(golden).write_parquet(output_path)
