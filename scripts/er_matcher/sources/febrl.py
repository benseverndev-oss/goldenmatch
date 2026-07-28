"""FEBRL synthetic-person loader (Task 5 of the multi-source ER data
pipeline). FEBRL ships a SINGLE table of person records where each real
entity appears as one "org" (original) record plus one or more "dup"
(corrupted duplicate) records; matches are same-entity record pairs,
non-matches are different-entity pairs. CPU/box-safe: stdlib only (csv,
pathlib, warnings), never imports torch/transformers/network.

Dataset shape expected under `root`:
  febrl_sample.csv -- header
    rec_id,given_name,surname,street,suburb,postcode,phone,soc_sec_id
  `rec_id` encodes entity + role, e.g. `rec-0-org`, `rec-0-dup-0`,
  `rec-1-dup-1`.
"""

from __future__ import annotations

import csv
import warnings
from pathlib import Path

from sources.base import Row
from sources.negatives import synth_negatives
from sources.splits import split_of


def _entity_of(rec_id: str) -> str:
    """Parse a FEBRL `rec_id` (e.g. `rec-0-org`, `rec-1-dup-0`) into its
    entity id (`rec-0`, `rec-1`) by dropping the `-org`/`-dup-<k>` role
    suffix -- the first two hyphen-joined tokens."""
    parts = rec_id.split("-")
    return "-".join(parts[:2])


def _is_org(rec_id: str) -> bool:
    """True if `rec_id`'s role token is `org` (the original record for its
    entity) rather than `dup-<k>` (a corrupted duplicate)."""
    parts = rec_id.split("-")
    return len(parts) >= 3 and parts[2] == "org"


class FebrlSource:
    """PairSource for FEBRL-style synthetic person data: a single record
    table where each entity contributes one "org" record plus zero or
    more "dup" records. Splits are assigned at the ENTITY level (unlike
    Leipzig's record/pair-level split) since every record belonging to a
    FEBRL entity lives in the same source table -- clean entity-level
    no-leak across train/val/test is both achievable and required here.
    Positives and negatives are generated WITHIN each split so no
    entity's records ever appear in more than one split.
    """

    license = "MPL-1.1"
    attribution = "FEBRL (ANU); Christen 2005"

    def __init__(
        self,
        root: Path,
        *,
        name: str = "febrl",
        domain: str = "person",
        block_fields: list[str] | None = None,
        seed: int = 0,
        val_frac: float = 0.15,
        test_frac: float = 0.15,
        hard_frac: float = 0.5,
    ) -> None:
        self.name = name
        self.root = Path(root)
        self.domain = domain
        self.block_fields = block_fields if block_fields is not None else ["surname"]
        self.seed = seed
        self.val_frac = val_frac
        self.test_frac = test_frac
        self.hard_frac = hard_frac

    def _read_records(self) -> dict[str, dict]:
        records: dict[str, dict] = {}
        with open(self.root / "febrl_sample.csv", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rec_id = row["rec_id"]
                fields = {k: v for k, v in row.items() if k != "rec_id"}
                records[rec_id] = fields
        return records

    def _row(self, records: dict[str, dict], eid_a: str, eid_b: str, label: str) -> Row:
        return {
            "a": records[eid_a],
            "b": records[eid_b],
            "label": label,
            "domain": self.domain,
            "source": "febrl",
            "dataset": self.name,
            "eid_a": eid_a,
            "eid_b": eid_b,
        }

    def splits(self) -> dict[str, list[Row]]:
        records = self._read_records()

        entities: dict[str, list[str]] = {}
        for rec_id in records:
            entities.setdefault(_entity_of(rec_id), []).append(rec_id)

        entity_split: dict[str, str] = {
            eid: split_of(eid, seed=self.seed, val_frac=self.val_frac, test_frac=self.test_frac)
            for eid in entities
        }

        out: dict[str, list[Row]] = {"train": [], "val": [], "test": []}

        for eid, rec_ids in entities.items():
            if len(rec_ids) < 2:
                continue
            split = entity_split[eid]
            rec_ids_sorted = sorted(rec_ids)
            org_ids = [r for r in rec_ids_sorted if _is_org(r)]
            anchor = org_ids[0] if org_ids else rec_ids_sorted[0]
            # Star topology: anchor paired with each OTHER record, not full
            # pairwise -- e.g. dup-0 vs dup-1 is never itself emitted as a
            # match. Acceptable simplification (every pair is still
            # same-entity via the shared anchor, just not exhaustive).
            for other in rec_ids_sorted:
                if other == anchor:
                    continue
                out[split].append(self._row(records, anchor, other, "match"))

        for split, split_rows in out.items():
            n_positives = len(split_rows)
            if n_positives == 0:
                continue

            split_rec_ids = [
                rec_id
                for eid, rec_ids in entities.items()
                if entity_split[eid] == split
                for rec_id in rec_ids
            ]
            split_records = {rid: records[rid] for rid in split_rec_ids}

            candidates = synth_negatives(
                split_records,
                block_keys=self.block_fields,
                hard_frac=self.hard_frac,
                seed=self.seed,
                n=n_positives,
            )

            kept = 0
            for eid_a, eid_b, _tag in candidates:
                if _entity_of(eid_a) == _entity_of(eid_b):
                    # A sampled "negative" whose two records belong to the
                    # SAME entity is actually a match, not a negative --
                    # dropping it is correctness-critical, not cosmetic.
                    continue
                out[split].append(self._row(records, eid_a, eid_b, "no_match"))
                kept += 1

            if kept < n_positives:
                warnings.warn(
                    f"febrl[{self.name}]: only {kept}/{n_positives} negatives "
                    f"in split {split!r} after same-entity filter",
                    stacklevel=2,
                )

        return out

    def record_pools(self) -> dict[str, list[dict]]:
        """Per-split raw record pool (every record grouped by its entity's
        split via `_entity_of`), leakage-consistent with `splits()` -- used
        by a later task (hard-negative mining) that needs the full record
        pool for a split, not just the sampled pairs."""
        records = self._read_records()

        entities: dict[str, list[str]] = {}
        for rec_id in records:
            entities.setdefault(_entity_of(rec_id), []).append(rec_id)

        entity_split: dict[str, str] = {
            eid: split_of(eid, seed=self.seed, val_frac=self.val_frac, test_frac=self.test_frac)
            for eid in entities
        }

        pools: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
        for rec_id, fields in records.items():
            pools[entity_split[_entity_of(rec_id)]].append(fields)
        return pools
