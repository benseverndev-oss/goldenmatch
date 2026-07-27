"""Leipzig CC-BY entity-matching benchmark loader (Task 4 of the multi-source
ER data pipeline). Turns a Leipzig-style two-record-table + perfect-mapping
gold file into labeled {a, b, label} `Row`s. CPU/box-safe: stdlib only
(csv, pathlib), never imports torch/transformers/network.

Dataset shape expected under `root`:
  tableA.csv  -- header `id,<fields...>`
  tableB.csv  -- header `id,<fields...>`
  mapping.csv -- header `idA,idB`; one row per gold (true-match) pair.
"""

from __future__ import annotations

import csv
import warnings
from pathlib import Path

from sources.base import Row
from sources.csv_tables import read_id_table
from sources.negatives import synth_negatives
from sources.splits import split_of

# synth_negatives is asked for a few extra candidates beyond the positive
# count so that dropping any that coincide with a gold match (see below)
# still leaves ~as many negatives as positives.
_NEGATIVE_OVERSAMPLE_FRAC = 0.5
_MIN_NEGATIVE_OVERSAMPLE = 2


class LeipzigSource:
    """PairSource for Leipzig-style CC-BY entity-matching benchmarks (e.g.
    Abt-Buy, Amazon-GoogleProducts, DBLP-Scholar): two record tables plus a
    perfect-mapping gold file of matches.

    Split assignment is done at the record/pair level (keyed by `eid_a` via
    `split_of`), NOT at a true cross-table entity-identity level. Strict
    entity-level no-leak across two *different* tables is a known hard
    problem and is out of scope here -- record/pair-level splitting is the
    pragmatic approach these benchmarks use elsewhere.
    """

    license = "CC-BY"
    attribution = "Leipzig DB Group; VLDB2010"

    def __init__(
        self,
        name: str,
        root: Path,
        domain: str,
        block_fields: list[str],
        seed: int,
        *,
        val_frac: float = 0.15,
        test_frac: float = 0.15,
        hard_frac: float = 0.5,
    ) -> None:
        self.name = name
        self.root = Path(root)
        self.domain = domain
        self.block_fields = block_fields
        self.seed = seed
        self.val_frac = val_frac
        self.test_frac = test_frac
        self.hard_frac = hard_frac

    def _read_gold_pairs(self) -> list[tuple[str, str]]:
        pairs: list[tuple[str, str]] = []
        with open(self.root / "mapping.csv", newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                pairs.append((f"A:{row['idA']}", f"B:{row['idB']}"))
        return pairs

    def _row(self, entities: dict[str, dict], eid_a: str, eid_b: str, label: str) -> Row:
        return {
            "a": entities[eid_a],
            "b": entities[eid_b],
            "label": label,
            "domain": self.domain,
            "source": "leipzig",
            "dataset": self.name,
            "eid_a": eid_a,
            "eid_b": eid_b,
        }

    def splits(self) -> dict[str, list[Row]]:
        entities: dict[str, dict] = {
            **read_id_table(self.root, "tableA.csv", "A"),
            **read_id_table(self.root, "tableB.csv", "B"),
        }
        gold_pairs = self._read_gold_pairs()
        # Order-normalized so a sampled negative is caught regardless of
        # which side synth_negatives happened to put first.
        gold_set = {(a, b) for a, b in gold_pairs} | {(b, a) for a, b in gold_pairs}

        out: dict[str, list[Row]] = {"train": [], "val": [], "test": []}

        def assign(row: Row) -> None:
            split = split_of(
                row["eid_a"], seed=self.seed, val_frac=self.val_frac, test_frac=self.test_frac
            )
            out[split].append(row)

        for eid_a, eid_b in gold_pairs:
            assign(self._row(entities, eid_a, eid_b, "match"))

        n_positives = len(gold_pairs)
        oversample = max(_MIN_NEGATIVE_OVERSAMPLE, round(n_positives * _NEGATIVE_OVERSAMPLE_FRAC))
        candidates = synth_negatives(
            entities,
            block_keys=self.block_fields,
            hard_frac=self.hard_frac,
            seed=self.seed,
            n=n_positives + oversample,
            # Namespace prefix ("A"/"B") is the partition -- negatives must
            # be strictly cross-table, matching what the gold mapping pairs.
            partition_of=lambda eid: eid[0],
        )

        kept = 0
        for eid_a, eid_b, _tag in candidates:
            if kept >= n_positives:
                break
            if (eid_a, eid_b) in gold_set:
                # A sampled "negative" that is actually a true match --
                # dropping it is correctness-critical, not cosmetic.
                continue
            assign(self._row(entities, eid_a, eid_b, "no_match"))
            kept += 1

        if kept < n_positives:
            warnings.warn(
                f"leipzig[{self.name}]: only {kept}/{n_positives} negatives "
                "after gold-filter + cross-table constraint",
                stacklevel=2,
            )

        return out
