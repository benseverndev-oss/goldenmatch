"""Cheat-line wrapper that materializes a Ray Dataset to Polars then
calls the in-memory dedupe pipeline.

Phase 2 deliberate concession. Phase 3 keeps the cheat-line for the
pipeline-level call, but `goldenmatch.core.cluster.build_clusters` is now
polymorphic on Ray Dataset input -- callers that pre-score and pass a
pairs Dataset get the distributed clustering path via
`goldenmatch.distributed.clustering.build_clusters_distributed`.

Phase 4 removes the materialize entirely by distributing golden record
build.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import polars as pl

if TYPE_CHECKING:
    from ray.data import Dataset


def run_dedupe_pipeline_distributed(ds: Dataset, **kwargs: Any):
    """Materialize the Ray Dataset and call dedupe_df.

    Phase 2/3 cheat-line: collects the full df to driver then runs the proven
    in-memory pipeline. `build_clusters` is already polymorphic on Ray Dataset
    pairs input (Phase 3). Phase 4 removes this materialize by distributing
    the golden record build stage.
    """
    from goldenmatch import dedupe_df

    rows = ds.take_all()
    df = pl.from_dicts(list(rows))
    return dedupe_df(df, **kwargs)
