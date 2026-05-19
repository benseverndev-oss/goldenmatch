"""Distributed pipeline orchestrator.

Phase 2 cheat-line (default): materialize the input via take_all, then run
in-memory dedupe_df.

Phase 4 (opt-in via GOLDENMATCH_DISTRIBUTED_PIPELINE=1): same call shape,
but `core.cluster.build_clusters` and `core.golden.build_golden_records_batch`
are now polymorphic on Ray Dataset, so callers that hand a Dataset to
those entry points get the distributed path. Phase 4's pipeline-level
implementation still collects for scoring (Phase 5 distributes that).
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

import polars as pl

if TYPE_CHECKING:
    from ray.data import Dataset


def _phase4_pipeline_enabled() -> bool:
    return os.environ.get("GOLDENMATCH_DISTRIBUTED_PIPELINE") == "1"


def run_dedupe_pipeline_distributed(ds: Dataset, **kwargs: Any):
    """Run dedupe on a Ray Dataset input.

    Phase 2 default: collect to driver, call dedupe_df.
    GOLDENMATCH_DISTRIBUTED_PIPELINE=1: same as default for now (Phase 5
    finishes the un-cheat). The polymorphic dispatch in build_clusters
    and build_golden_records_batch already routes Ray Dataset callers
    to the distributed path automatically.
    """
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
