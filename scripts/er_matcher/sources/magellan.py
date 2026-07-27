"""Magellan/DeepMatcher fetch-at-build EVAL loader (Task 6 of the
multi-source ER data pipeline). Unlike the Leipzig/FEBRL loaders, DeepMatcher
datasets ship PRE-LABELED candidate pairs (train/valid/test CSVs with an
explicit match label) -- there is NO negative synthesis here. This source is
EVAL-ONLY (never part of the training corpus) and its data is FETCHED at
build time under a cite-only license, so it is never committed to the repo.
CPU/box-safe on the parse path: stdlib only (csv, pathlib, os); `urllib` is
only imported inside `fetch`, below the network guard.

Dataset shape expected under `root`:
  tableA.csv -- header `id,<fields...>`
  tableB.csv -- header `id,<fields...>`
  train.csv, valid.csv, test.csv -- header `ltable_id,rtable_id,label`
    (`label` is `1`=match, `0`=no_match). `valid.csv` maps to the canonical
    `"val"` split key.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from sources.base import Row
from sources.csv_tables import read_id_table

# Split file name -> canonical split key. DeepMatcher's on-disk name for the
# validation split is `valid.csv`, but the rest of the pipeline (and Row's
# split dict) uses the canonical key "val" -- this table is where that
# rename happens.
_SPLIT_FILES: dict[str, str] = {"train": "train.csv", "val": "valid.csv", "test": "test.csv"}

# TODO(#magellan-fetch): real DeepMatcher mirror URL + expected sha256 per
# dataset. Left undocumented here since we can't exercise a real download in
# CI; the network GUARD below is what's tested, not this download logic.
_DEEPMATCHER_BASE_URL = "https://pages.cs.wisc.edu/~anhai/data/deepmatcher_data/"


class MagellanSource:
    """PairSource for Magellan/DeepMatcher benchmark datasets: two record
    tables (tableA/tableB) plus pre-labeled train/valid/test candidate-pair
    CSVs. `eval_only = True` -- callers must never fold this source's rows
    into a training corpus.
    """

    eval_only = True
    license = "cite-only"
    attribution = "Magellan/DeepMatcher (anhaidgroup); Konda et al. 2016"

    def __init__(self, name: str, root: Path, domain: str = "product") -> None:
        self.name = name
        self.root = Path(root)
        self.domain = domain

    def _read_split(self, filename: str, table_a: dict, table_b: dict) -> list[Row]:
        rows: list[Row] = []
        with open(self.root / filename, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ltable_id = row["ltable_id"]
                rtable_id = row["rtable_id"]
                eid_a = f"A:{ltable_id}"
                eid_b = f"B:{rtable_id}"
                rows.append(
                    {
                        "a": table_a[eid_a],
                        "b": table_b[eid_b],
                        "label": "match" if int(row["label"]) == 1 else "no_match",
                        "domain": self.domain,
                        "source": "magellan",
                        "dataset": self.name,
                        "eid_a": eid_a,
                        "eid_b": eid_b,
                    }
                )
        return rows

    def _has_local_data(self) -> bool:
        expected = ("tableA.csv", "tableB.csv", *_SPLIT_FILES.values())
        return self.root.exists() and all((self.root / fname).is_file() for fname in expected)

    def load_from_dir(self) -> dict[str, list[Row]]:
        """PURE, no network: parse an already-fetched Magellan dataset
        directory into the three canonical splits."""
        table_a = read_id_table(self.root, "tableA.csv", "A")
        table_b = read_id_table(self.root, "tableB.csv", "B")
        return {
            split: self._read_split(fname, table_a, table_b)
            for split, fname in _SPLIT_FILES.items()
        }

    def splits(self) -> dict[str, list[Row]]:
        if not self._has_local_data():
            self.fetch()
        return self.load_from_dir()

    def fetch(self) -> None:
        """Network fetch, GUARDED behind an explicit opt-in env var -- this
        source's data is cite-only-licensed and must never be downloaded
        (or committed) silently."""
        if os.environ.get("GOLDENMATCH_ALLOW_FETCH") != "1":
            raise RuntimeError(
                "network fetch disabled; set GOLDENMATCH_ALLOW_FETCH=1 to download "
                "Magellan data (eval-only, cite-only license)"
            )

        # TODO(#magellan-fetch): `import urllib.request` here and resolve the
        # real per-dataset archive URL under _DEEPMATCHER_BASE_URL + self.name,
        # download to self.root, verify against a known sha256, and unpack
        # tableA/tableB/train/valid/test CSVs. Not exercised here (no real
        # download in CI).
        raise NotImplementedError(
            f"Magellan fetch for dataset {self.name!r} is not yet implemented "
            f"(target base URL: {_DEEPMATCHER_BASE_URL})"
        )
