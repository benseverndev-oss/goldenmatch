"""Distributed sampling helper for the controller iteration loop."""
from __future__ import annotations
from typing import TYPE_CHECKING

import polars as pl

if TYPE_CHECKING:
    from ray.data import Dataset


def take_sample_distributed(ds: "Dataset", sample_cap: int = 20_000) -> pl.DataFrame:
    """Pull a bounded sample from a Ray Dataset as a Polars DataFrame.

    Used by AutoConfigController to materialize a per-iteration sample.
    Phase 2 leaves the iteration loop on Polars; Phase 3+ may distribute it.

    Sample is uniform random (not stratified). Stratification on the
    distributed path is a Phase 3 follow-up.
    """
    total = ds.count()
    if total == 0:
        return pl.DataFrame()
    if total <= sample_cap:
        rows = ds.take_all()
    else:
        fraction = sample_cap / total
        # Pull a small headroom so random_sample's approximate fraction
        # doesn't undershoot the cap on tiny inputs.
        rows = ds.random_sample(fraction=min(1.0, fraction * 1.5)).take(sample_cap)
    return pl.from_dicts(list(rows))
