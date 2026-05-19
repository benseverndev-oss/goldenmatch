"""Distributed variants of full-df indicators (Phase 2).

Phase 2 uses bounded-sample collection: random_sample -> take -> Polars,
then call the in-memory indicator functions. The win is removing the
full-df materialization on the driver; the sample is small enough that
materializing IT is fine.

Phase 3 may push these to ds.aggregate / ds.map_batches if measured wall
exceeds budget.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

import polars as pl

from goldenmatch.core.complexity_profile import ColumnPrior, SparsityVerdict

if TYPE_CHECKING:
    from ray.data import Dataset

_INDICATOR_SAMPLE_CAP = 5000


def _collect_indicator_sample(ds: "Dataset", cap: int = _INDICATOR_SAMPLE_CAP) -> pl.DataFrame:
    total = ds.count()
    if total == 0:
        return pl.DataFrame()
    fraction = min(1.0, cap / total)
    rows = ds.random_sample(fraction=fraction).take(cap)
    return pl.from_dicts(list(rows))


def compute_column_priors_distributed(ds: "Dataset") -> dict[str, ColumnPrior]:
    from goldenmatch.core.indicators import compute_column_priors
    sample = _collect_indicator_sample(ds)
    return compute_column_priors(sample)


def estimate_sparse_match_signal_distributed(
    ds: "Dataset",
    exact_columns: list[str],
) -> SparsityVerdict:
    from goldenmatch.core.indicators import estimate_sparse_match_signal
    sample = _collect_indicator_sample(ds)
    return estimate_sparse_match_signal(sample, exact_columns=exact_columns)
