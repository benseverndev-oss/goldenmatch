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
from sources.splits import entity_keys_from_edges, split_of

# synth_negatives is asked for a few extra candidates beyond the positive
# count so that dropping any that coincide with a gold match (see below)
# still leaves ~as many negatives as positives.
_NEGATIVE_OVERSAMPLE_FRAC = 0.5
_MIN_NEGATIVE_OVERSAMPLE = 2


class LeipzigSource:
    """PairSource for Leipzig-style CC-BY entity-matching benchmarks (e.g.
    Abt-Buy, Amazon-GoogleProducts, DBLP-Scholar): two record tables plus a
    perfect-mapping gold file of matches.

    Split assignment is at the ENTITY level: the gold mapping's (idA, idB)
    pairs are treated as edges over the combined tableA+tableB record ids,
    connected components (`entity_keys_from_edges`) group every A-side and
    B-side record that is transitively linked by a gold match into one
    entity, and that entity's canonical key is hashed via `split_of`. This
    means a record's split assignment no longer depends on which side of
    the pair it happens to be (`eid_a` vs `eid_b`), and a record chain
    linked through multiple gold matches lands in exactly one split.
    Negative pairs are then synthesized PER SPLIT, over that split's record
    pool only, so no negative can cross a split boundary.
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

    def _entities(self) -> dict[str, dict]:
        return {
            **read_id_table(self.root, "tableA.csv", "A"),
            **read_id_table(self.root, "tableB.csv", "B"),
        }

    def _key_of(self, entities: dict[str, dict], gold_pairs: list[tuple[str, str]]) -> dict[str, str]:
        return entity_keys_from_edges(entities.keys(), gold_pairs)

    def _split_of_record(self, key_of: dict[str, str], rid: str) -> str:
        return split_of(
            key_of[rid], seed=self.seed, val_frac=self.val_frac, test_frac=self.test_frac
        )

    def splits(self) -> dict[str, list[Row]]:
        entities = self._entities()
        gold_pairs = self._read_gold_pairs()
        # Connected components over the gold-match edges give every A-side
        # and B-side record a stable ENTITY key (Task 1 fix). A gold pair's
        # two records are always unioned into the same entity, so eid_a and
        # eid_b resolve to the same split -- no record/pair-level leakage.
        key_of = self._key_of(entities, gold_pairs)
        # Precompute once (not per-split-per-entity) -- record_split maps
        # every record id to its split via a single split_of hash each.
        record_split: dict[str, str] = {rid: self._split_of_record(key_of, rid) for rid in entities}

        out: dict[str, list[Row]] = {"train": [], "val": [], "test": []}

        for eid_a, eid_b in gold_pairs:
            split = record_split[eid_a]
            out[split].append(self._row(entities, eid_a, eid_b, "match"))

        # Negatives are synthesized PER SPLIT, over that split's record pool
        # only, so a negative pair can never straddle a split boundary.
        total_kept = 0
        total_positives = len(gold_pairs)
        for split, split_rows in out.items():
            n_positives = len(split_rows)
            if n_positives == 0:
                continue
            split_rec_ids = [rid for rid, rid_split in record_split.items() if rid_split == split]
            split_entities = {rid: entities[rid] for rid in split_rec_ids}
            oversample = max(
                _MIN_NEGATIVE_OVERSAMPLE, round(n_positives * _NEGATIVE_OVERSAMPLE_FRAC)
            )
            candidates = synth_negatives(
                split_entities,
                block_keys=self.block_fields,
                hard_frac=self.hard_frac,
                seed=self.seed,
                n=n_positives + oversample,
                # Namespace prefix ("A"/"B") is the partition -- negatives
                # must be strictly cross-table, matching the gold mapping.
                partition_of=lambda eid: eid[0],
            )

            kept = 0
            for eid_a, eid_b, _tag in candidates:
                if kept >= n_positives:
                    break
                if key_of.get(eid_a) == key_of.get(eid_b):
                    # A sampled "negative" that is actually a true match --
                    # dropping it is correctness-critical, not cosmetic. We test
                    # ENTITY-key equality (connected components over the gold edges),
                    # NOT direct gold-pair membership: on many-to-many sources
                    # (amazon_google, dblp_scholar) two records can belong to the
                    # same entity without a DIRECT gold edge between them, and a
                    # gold_set-membership check would emit that true match as a
                    # `no_match` -- silent training-label corruption.
                    continue
                out[split].append(self._row(entities, eid_a, eid_b, "no_match"))
                kept += 1
            total_kept += kept

        if total_kept < total_positives:
            warnings.warn(
                f"leipzig[{self.name}]: only {total_kept}/{total_positives} negatives "
                "after gold-filter + cross-table constraint",
                stacklevel=2,
            )

        return out

    def record_pools(self) -> dict[str, list[dict]]:
        """Per-split raw record pool (tableA + tableB records grouped by
        their entity's split), leakage-consistent with `splits()` -- used
        by a later task (hard-negative mining) that needs the full record
        pool for a split, not just the sampled pairs."""
        entities = self._entities()
        gold_pairs = self._read_gold_pairs()
        key_of = self._key_of(entities, gold_pairs)

        pools: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
        for rid, fields in entities.items():
            split = self._split_of_record(key_of, rid)
            pools[split].append(fields)
        return pools
