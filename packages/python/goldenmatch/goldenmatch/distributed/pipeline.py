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


def _run_phase5_pipeline(
    ds: Dataset,
    *,
    output_path: str | None = None,
    confidence_required: bool = True,
    **kwargs: Any,
):
    """Phase 5 streaming: score -> cluster -> golden -> write, no take_all on input.

    Honest scope: we still materialize cluster aggregates to driver via Phase 3's
    materialize_cluster_dict adapter. The Phase 5 win is the per-stage distribution
    and the elimination of the entry-side take_all.
    """
    from goldenmatch.core.autoconfig import auto_configure_df
    from goldenmatch.core.cluster import build_clusters
    from goldenmatch.core.golden import build_golden_records_batch
    from goldenmatch.distributed.scoring import (
        dedup_pairs_distributed,
        score_blocks_distributed,
    )

    # 1. Auto-configure via Phase 2 controller (handles sample collect from Dataset).
    cfg = auto_configure_df(ds, confidence_required=confidence_required, _skip_finalize=True)

    # 2. Distributed scoring -> Ray Dataset of pairs.
    raw_pairs_ds = score_blocks_distributed(ds, cfg)
    pairs_ds = dedup_pairs_distributed(raw_pairs_ds)

    # 3. Clustering via Phase 3 polymorphic dispatch on Ray Dataset pairs.
    #    build_clusters detects Ray Dataset and routes to materialize_cluster_dict.
    clusters_dict = build_clusters(pairs_ds)

    # 4. Annotate each row with __cluster_id__ via map_batches + broadcast dict.
    multi_ds = _join_clusters_to_rows(ds, clusters_dict)

    # 5. Golden via Phase 4 polymorphic dispatch (handles both pl.DataFrame and Dataset).
    golden = build_golden_records_batch(multi_ds, cfg.golden_rules)

    # 6. Write output.
    if output_path is not None:
        _write_golden_output(golden, output_path)

    from goldenmatch._api import DedupeResult
    return DedupeResult(
        clusters=clusters_dict,
        golden=None,  # written to disk via output_path
        config=cfg,
    )


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

    def _annotate(batch):  # batch: pa.Table -> pa.Table
        import polars as pl
        import ray as _ray
        df = pl.from_arrow(batch)
        assert isinstance(df, pl.DataFrame)
        if "__row_id__" not in df.columns:
            df = df.with_row_index("__row_id__").with_columns(
                pl.col("__row_id__").cast(pl.Int64)
            )
        lookup = _ray.get(map_ref)
        keys = list(lookup.keys())
        vals = list(lookup.values())
        out = df.with_columns(
            pl.col("__row_id__").replace_strict(
                keys, vals, default=-1,
            ).alias("__cluster_id__")
        ).filter(pl.col("__cluster_id__") != -1)
        return out.to_arrow()

    return ds.map_batches(_annotate, batch_format="pyarrow")


def _write_golden_output(golden, output_path: str) -> None:
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
