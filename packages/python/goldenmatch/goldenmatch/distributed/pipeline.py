"""Cheat-line wrapper that materializes a Ray Dataset to Polars then
calls the in-memory dedupe pipeline.

Phase 2 deliberate concession. The point of Phase 2 is to remove driver-
side materialization for the *controller's full-df indicator stage*.
Once the controller commits a config and reaches _finalize, we re-collect
to Polars and run the proven in-memory pipeline. Phase 3 distributes the
scoring/clustering/golden stages so _finalize doesn't materialize.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import polars as pl

if TYPE_CHECKING:
    from ray.data import Dataset


def run_dedupe_pipeline_distributed(ds: Dataset, **kwargs: Any):
    """Materialize the Ray Dataset and call dedupe_df.

    Phase 2 deliberately collects the full df to driver here. Phase 3 will
    push scoring/clustering/golden into Ray actors so the collect goes away.
    """
    from goldenmatch import dedupe_df

    rows = ds.take_all()
    df = pl.from_dicts(list(rows))
    return dedupe_df(df, **kwargs)
