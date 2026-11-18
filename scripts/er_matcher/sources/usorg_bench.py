"""USORG-Bench loader: US organization pairs labelled by government identifiers.

USORG-Bench (CC BY 4.0, https://huggingface.co/datasets/tensorust/usorg-bench)
pairs US company and organization records from public sources (USAspending,
SEC EDGAR, CMS, FCC, USAC, state business registries). A pair is a match when
both records carry the same SAM.gov UEI, SEC CIK, CMS certification number,
FCC FRN, USAC entity number or state filing number, and a non-match when they
carry different values. It ships PRE-LABELED pairs with entity-disjoint
train/dev/test splits, so (like Magellan) there is no negative synthesis here.
CPU/box-safe on the parse path: stdlib only (csv, pathlib, os); `urllib` is
only imported inside `fetch`, below the network guard.

Dataset shape expected under `root` (the two CSVs of the tagged release):
  records.csv -- `record_id,source,name,name_kind,address,city,state,zip5,id_type,entity_id`
  pairs.csv   -- `pair_id,left_id,right_id,label,...,split` (`label` 1/0,
                 `split` train/dev/test; `dev` maps to the canonical `"val"`)

Only `name, address, city, state, zip5` reach a Row. The other record columns
(`source`, `id_type`, `name_kind`, `entity_id`) describe how the label was made
and predict it on their own, so the benchmark's evaluation rule excludes them.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path

from sources.base import Row

# The fields a matcher may see (the benchmark's leaderboard rule).
FEATURE_FIELDS: tuple[str, ...] = ("name", "address", "city", "state", "zip5")

# Released split name -> canonical split key.
_SPLITS: dict[str, str] = {"train": "train", "dev": "val", "test": "test"}

# Pinned release: tag v1.0.1 of the HF dataset, with the SHA-256 of each file
# so a moved tag or a truncated download fails loudly.
_REVISION = "v1.0.1"
_BASE_URL = f"https://huggingface.co/datasets/tensorust/usorg-bench/resolve/{_REVISION}/data/"
_FILES: dict[str, str] = {
    "pairs.csv": "b54ba266817e1cc625a6d8d286399c5e679f6a782ff7767fe18e96a7dc52c5a9",
    "records.csv": "47e40a25643b4b9791db4721ae385243f67a501957fb19153bb6ef3858706ed0",
}


class UsorgBenchSource:
    """PairSource for USORG-Bench: pre-labeled organization pairs with the
    released entity-disjoint splits. CC BY 4.0, so redistribution and training
    use are allowed with attribution; `sources.yaml` registers it eval-only.
    """

    license = "CC-BY"
    attribution = (
        "USORG-Bench, Tensorust (CC BY 4.0), from USAspending, SEC EDGAR, CMS, "
        "FCC, USAC and CO/CT/NY/OR business registry records"
    )

    def __init__(self, name: str, root: Path, domain: str = "organization") -> None:
        self.name = name
        self.root = Path(root)
        self.domain = domain

    def _records(self) -> dict[str, dict[str, str]]:
        records: dict[str, dict[str, str]] = {}
        with open(self.root / "records.csv", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                records[row["record_id"]] = {k: row[k] for k in FEATURE_FIELDS}
        return records

    def _pairs(self) -> list[dict[str, str]]:
        with open(self.root / "pairs.csv", newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def _has_local_data(self) -> bool:
        return all((self.root / fname).is_file() for fname in _FILES)

    def load_from_dir(self) -> dict[str, list[Row]]:
        """PURE, no network: parse an already-fetched release directory into
        the three canonical splits, in file order."""
        records = self._records()
        out: dict[str, list[Row]] = {"train": [], "val": [], "test": []}
        for p in self._pairs():
            eid_a, eid_b = p["left_id"], p["right_id"]
            out[_SPLITS[p["split"]]].append(
                {
                    "a": records[eid_a],
                    "b": records[eid_b],
                    "label": "match" if int(p["label"]) == 1 else "no_match",
                    "domain": self.domain,
                    "source": "usorg_bench",
                    "dataset": self.name,
                    "eid_a": eid_a,
                    "eid_b": eid_b,
                }
            )
        return out

    def splits(self) -> dict[str, list[Row]]:
        if not self._has_local_data():
            self.fetch()
        return self.load_from_dir()

    def record_pools(self) -> dict[str, list[dict]]:
        """Per-split record pool. Every record belongs to exactly one split's
        pairs in the release, so the pools are disjoint like `splits()`."""
        records = self._records()
        seen: set[str] = set()
        pools: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
        for p in self._pairs():
            split = _SPLITS[p["split"]]
            for rid in (p["left_id"], p["right_id"]):
                if rid not in seen:
                    seen.add(rid)
                    pools[split].append(records[rid])
        return pools

    def fetch(self) -> None:
        """Download `pairs.csv` and `records.csv` of the pinned release into
        `self.root`, GUARDED behind GOLDENMATCH_ALLOW_FETCH like the other
        fetch sources, and check each file against its pinned SHA-256."""
        if os.environ.get("GOLDENMATCH_ALLOW_FETCH") != "1":
            raise RuntimeError(
                "network fetch disabled; set GOLDENMATCH_ALLOW_FETCH=1 to download "
                f"USORG-Bench {_REVISION} (CC BY 4.0) into {self.root}"
            )

        import hashlib
        import urllib.request

        self.root.mkdir(parents=True, exist_ok=True)
        headers = {"User-Agent": "goldenmatch-er-matcher-fetch/1.0"}
        for fname, expected in _FILES.items():
            path = self.root / fname
            if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == expected:
                continue
            req = urllib.request.Request(_BASE_URL + fname, headers=headers)
            with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310 (trusted host)
                data = resp.read()
            digest = hashlib.sha256(data).hexdigest()
            if digest != expected:
                raise RuntimeError(
                    f"usorg_bench fetch: {fname} sha256 {digest} != pinned {expected} "
                    f"({_BASE_URL + fname})"
                )
            path.write_bytes(data)
            print(f"[usorg_bench] downloaded {fname} ({len(data)} bytes, sha256={digest})")
