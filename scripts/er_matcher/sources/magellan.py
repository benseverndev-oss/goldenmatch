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

# Confirmed base for the "Structured" DeepMatcher benchmarks (verified via a
# direct HTTP 200 fetch of Structured/Walmart-Amazon/exp_data/{tableA,test}.csv;
# note the "data1" segment -- "data" 404s). Per-dataset files are direct CSVs
# (not zips) at f"{base}Structured/<UrlName>/exp_data/{tableA,tableB,train,valid,test}.csv".
_DEEPMATCHER_BASE_URL = "https://pages.cs.wisc.edu/~anhai/data1/deepmatcher_data/"

# `self.name` (dataset registry key, e.g. "walmart_amazon") -> the hyphenated
# "UrlName" DeepMatcher uses on disk (e.g. "Walmart-Amazon"). Explicit map for
# the two datasets this fetch has been verified against; fall back to a
# title-cased/hyphenated guess for anything else (may need its own entry here
# once verified -- DeepMatcher's naming isn't perfectly regular, e.g. casing).
_URL_NAMES: dict[str, str] = {"walmart_amazon": "Walmart-Amazon", "beer": "Beer"}


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
        (or committed) silently.

        Downloads the 5 direct CSVs (tableA, tableB, train, valid, test) for
        this dataset from the DeepMatcher "Structured" mirror into `self.root`,
        then validates each is non-empty and that the 3 pair-split files carry
        a `label` column in their header (catches an HTML error page silently
        landing where a CSV was expected, e.g. from a bad _URL_NAMES guess)."""
        if os.environ.get("GOLDENMATCH_ALLOW_FETCH") != "1":
            raise RuntimeError(
                "network fetch disabled; set GOLDENMATCH_ALLOW_FETCH=1 to download "
                "Magellan data (eval-only, cite-only license)"
            )

        import hashlib
        import urllib.request

        if self._has_local_data():
            print(f"[magellan] {self.name}: already present under {self.root}, skipping download")
            return

        url_name = _URL_NAMES.get(self.name, self.name.replace("_", "-").title())
        base_url = f"{_DEEPMATCHER_BASE_URL}Structured/{url_name}/exp_data/"
        self.root.mkdir(parents=True, exist_ok=True)

        filenames = ("tableA.csv", "tableB.csv", *_SPLIT_FILES.values())
        headers = {"User-Agent": "goldenmatch-er-matcher-fetch/1.0"}
        for fname in filenames:
            url = base_url + fname
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=120) as resp:  # noqa: S310 (trusted host)
                data = resp.read()
            digest = hashlib.sha256(data).hexdigest()
            (self.root / fname).write_bytes(data)
            print(
                f"[magellan] {self.name}: downloaded {fname} "
                f"({len(data)} bytes, sha256={digest}) -> {self.root / fname}"
            )

        for fname in filenames:
            path = self.root / fname
            if path.stat().st_size == 0:
                raise RuntimeError(
                    f"magellan fetch for {self.name!r}: {fname} downloaded empty from {base_url} "
                    f"(bad dataset name / _URL_NAMES mapping for url_name={url_name!r}?)"
                )
        for fname in ("train.csv", "valid.csv", "test.csv"):
            path = self.root / fname
            with open(path, encoding="utf-8") as f:
                header = f.readline()
            if "label" not in header:
                raise RuntimeError(
                    f"magellan fetch for {self.name!r}: {fname} header {header.strip()!r} does not "
                    "contain 'label' -- looks like an error page, not a CSV "
                    f"(check _URL_NAMES mapping for url_name={url_name!r} against {base_url})"
                )
