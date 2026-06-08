#!/usr/bin/env python
"""Dataset loaders for the probabilistic accuracy panel.

Each loader returns (records, truth):
  records: polars.DataFrame with a 'record_id' column + matchable fields
  truth:   polars.DataFrame with columns {record_id, cluster_id}

historical_50k is Splink's home-turf biographical dataset (Wikidata historical
people, with a ground-truth cluster label). Loaded via splink_datasets when
splink is installed, else from a vendored parquet under the gitignored
tests/benchmarks/datasets/.
"""
from __future__ import annotations

import logging
from pathlib import Path

import polars as pl

logger = logging.getLogger(__name__)

REPO = Path(__file__).resolve().parents[2]
DATASETS_DIR = REPO / "packages" / "python" / "goldenmatch" / "tests" / "benchmarks" / "datasets"


class DatasetUnavailable(RuntimeError):
    """Raised when a dataset's data or its loader dependency is missing."""


def _historical_50k() -> tuple[pl.DataFrame, pl.DataFrame]:
    df = None
    try:
        from splink import splink_datasets  # type: ignore
    except ImportError:
        splink_datasets = None  # type: ignore
    if splink_datasets is not None:
        try:
            df = pl.from_pandas(splink_datasets.historical_50k)  # type: ignore
        except Exception as e:  # splink present but dataset unusable -> try vendored
            logger.warning(
                "splink_datasets.historical_50k failed (%s); trying vendored parquet", e
            )
            df = None
    if df is None:
        vendored = DATASETS_DIR / "historical_50k.parquet"
        if not vendored.exists():
            raise DatasetUnavailable(
                "install `goldenmatch[bench]` (for splink_datasets) or vendor "
                f"{vendored}"
            )
        df = pl.read_parquet(vendored)

    # historical_50k columns: unique_id, cluster, first_name, surname, dob,
    # birth_place, postcode_fake, occupation, ...
    df = df.rename({"unique_id": "record_id", "cluster": "cluster_id"})
    truth = df.select(["record_id", "cluster_id"])
    records = df.drop("cluster_id")
    return records, truth


_LOADERS = {
    "historical_50k": _historical_50k,
    # DBLP-ACM / Febrl3 / NCVR / synthetic adapters added in Task 0.2.
}


def load_dataset(name: str) -> tuple[pl.DataFrame, pl.DataFrame]:
    if name not in _LOADERS:
        raise KeyError(f"unknown dataset {name!r}; have {sorted(_LOADERS)}")
    return _LOADERS[name]()
