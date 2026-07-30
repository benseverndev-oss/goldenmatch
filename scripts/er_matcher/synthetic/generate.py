"""Lean synthetic ``PairSource`` (Phase 1b, Task 4).

``SyntheticSource`` ties the three preceding units together -- ``vocab`` +
``schemas.build_record`` compose a plausible base record per entity across the
three domains, ``corruption.corrupt_record`` turns it into a "duplicate", and
the shared ``sources`` helpers (``synth_negatives`` / ``split_of``) draw
negatives and assign entity-level splits. It MIRRORS ``sources/febrl.py``:
entity->split via ``split_of``, star-topology positives (base anchor vs each
dup), per-split negatives with a same-entity drop, and ``record_pools()``
grouping every record by its entity's split -- with ``_entities_and_splits``
factored out so ``splits()`` and ``record_pools()`` stay leakage-consistent.

Differences from febrl (both correctness-critical):
  * Records are GENERATED, not read from a CSV. Each entity gets a base record
    plus one duplicate that is guaranteed distinct from the base (a duplicate
    that no channel happened to touch under the ``light`` profile would be a
    byte-identical copy, which both defeats the point of a "positive" pair and
    collides in ``record_pools()``; the draw is re-rolled on the same rng until
    it differs, which stays deterministic).
  * ``synth_negatives`` is called once PER DOMAIN per split -- each domain has
    its own ``name_field`` (``last`` / ``legal_name`` / ``name``), so a single
    cross-domain call would block on the wrong field and never produce genuine
    hard negatives for two of the three domains.

CPU/box-safe: stdlib + the local synthetic/sources modules only, never imports
torch/transformers/network.
"""

from __future__ import annotations

import random
import warnings

from corruption import corrupt_record
from schemas import DOMAINS, build_record
from sources.base import Row
from sources.negatives import synth_negatives
from sources.splits import split_of
from vocab import Vocab

# Round-robin assignment order; also the domain set covered by every run.
_DOMAIN_ORDER = ("crm_contact", "organization", "business")


def _entity_of(rec_id: str) -> str:
    """Parse a generated record id (``<domain>:<i>:<role>``, e.g.
    ``crm_contact:0:base``) into its entity id (``crm_contact:0``) by dropping
    the trailing ``:base``/``:dup`` role token."""
    return rec_id.rsplit(":", 1)[0]


def _base_id(eid: str) -> str:
    return f"{eid}:base"


def _dup_id(eid: str) -> str:
    return f"{eid}:dup"


class SyntheticSource:
    """PairSource for lean multi-domain synthetic data. Each entity lives in
    exactly one domain and contributes one base record plus one corrupted
    duplicate; positives are same-entity (base vs dup), negatives are
    cross-entity within a domain (base vs base). Splits are assigned at the
    ENTITY level so no entity's records ever span two splits.

    Domains are assigned strictly round-robin. The plan's ``domain_weights``
    param was intentionally dropped for the lean cut (weighting is deferred);
    whoever wires Task 5's ``sources.yaml`` should not assume that kwarg exists.
    """

    license = "CC0-1.0"
    attribution = "Synthetic (Golden Suite); census-weighted vocab"

    def __init__(
        self,
        *,
        name: str = "synthetic",
        seed: int = 0,
        n_entities: int = 300,
        profile: str = "light",
        val_frac: float = 0.15,
        test_frac: float = 0.15,
        hard_frac: float = 0.5,
    ) -> None:
        self.name = name
        self.seed = seed
        self.n_entities = n_entities
        self.profile = profile
        self.val_frac = val_frac
        self.test_frac = test_frac
        self.hard_frac = hard_frac
        self._vocab = Vocab()

    def _generate(self) -> tuple[dict[str, dict], dict[str, str]]:
        """Generate every record. Returns ``(records, entity_domain)`` where
        ``records`` maps record id -> field dict (a ``:base`` and a ``:dup`` per
        entity) and ``entity_domain`` maps entity id -> its domain. Deterministic
        per ``seed``: each entity draws from a private ``random.Random`` seeded
        arithmetically from ``seed`` + the entity id (NOT Python's salted
        ``hash()``), so runs are byte-identical."""
        records: dict[str, dict] = {}
        entity_domain: dict[str, str] = {}
        for i in range(self.n_entities):
            domain = _DOMAIN_ORDER[i % len(_DOMAIN_ORDER)]
            eid = f"{domain}:{i}"
            entity_domain[eid] = domain
            strong_id = DOMAINS[domain].strong_id

            base = build_record(domain, self._vocab, random.Random(f"{self.seed}:{eid}:base"))
            # Re-roll the duplicate on the same (advancing) rng until it differs
            # from the base -- a byte-identical "duplicate" is not a corruption
            # and would collide with its base in record_pools(). Termination is
            # probabilistically-bounded, not deterministically-bounded: with many
            # independently-corrupted fields the re-roll succeeds on the first or
            # second try with overwhelming probability, so in practice it exits
            # essentially immediately (and stays deterministic per seed).
            dup_rng = random.Random(f"{self.seed}:{eid}:dup")
            dup = corrupt_record(base, strong_id=strong_id, rng=dup_rng, profile=self.profile)
            while dup == base:
                dup = corrupt_record(base, strong_id=strong_id, rng=dup_rng, profile=self.profile)

            records[_base_id(eid)] = base
            records[_dup_id(eid)] = dup
        return records, entity_domain

    def _row(self, records: dict[str, dict], eid_a: str, eid_b: str, domain: str, label: str) -> Row:
        return {
            "a": records[eid_a],
            "b": records[eid_b],
            "label": label,
            "domain": domain,
            "source": "synthetic",
            "dataset": self.name,
            "eid_a": eid_a,
            "eid_b": eid_b,
        }

    def _entities_and_splits(
        self, records: dict[str, dict]
    ) -> tuple[dict[str, list[str]], dict[str, str]]:
        """Group every record by its entity (``_entity_of``), then hash each
        entity's split once. Shared by ``splits()`` and ``record_pools()`` so the
        entity-grouping + split-hash logic lives in exactly one place (mirrors
        febrl)."""
        entities: dict[str, list[str]] = {}
        for rec_id in records:
            entities.setdefault(_entity_of(rec_id), []).append(rec_id)

        entity_split: dict[str, str] = {
            eid: split_of(eid, seed=self.seed, val_frac=self.val_frac, test_frac=self.test_frac)
            for eid in entities
        }
        return entities, entity_split

    def splits(self) -> dict[str, list[Row]]:
        records, entity_domain = self._generate()
        entities, entity_split = self._entities_and_splits(records)

        out: dict[str, list[Row]] = {"train": [], "val": [], "test": []}

        # Star-topology positives: the base record anchors, paired with each
        # (here: only) dup of the same entity. Same-entity by construction.
        for eid, rec_ids in entities.items():
            split = entity_split[eid]
            domain = entity_domain[eid]
            anchor = _base_id(eid)
            for other in sorted(rec_ids):
                if other == anchor:
                    continue
                out[split].append(self._row(records, anchor, other, domain, "match"))

        # Negatives per DOMAIN per split: block on that domain's name_field so
        # hard negatives genuinely collide on the name-bearing field for all
        # three domains. ``n`` matches the domain-split positive count (one per
        # entity) to keep the label balance ~0.5.
        for split in ("train", "val", "test"):
            for domain in _DOMAIN_ORDER:
                dom_split_eids = [
                    eid
                    for eid in entities
                    if entity_split[eid] == split and entity_domain[eid] == domain
                ]
                n_positives = len(dom_split_eids)
                if n_positives == 0:
                    continue

                # Represent each entity by its BASE record (mirrors febrl passing
                # per-record field dicts; the negatives helper keys on these ids).
                neg_records = {_base_id(eid): records[_base_id(eid)] for eid in dom_split_eids}
                candidates = synth_negatives(
                    neg_records,
                    block_keys=[DOMAINS[domain].name_field],
                    hard_frac=self.hard_frac,
                    seed=self.seed,
                    n=n_positives,
                )

                kept = 0
                for eid_a, eid_b, _tag in candidates:
                    if _entity_of(eid_a) == _entity_of(eid_b):
                        # A sampled "negative" whose two records belong to the
                        # SAME entity is actually a match -- dropping it is
                        # correctness-critical, not cosmetic. (Defensive: with one
                        # base record per entity here it cannot fire, but it keeps
                        # the febrl invariant explicit.)
                        continue
                    out[split].append(self._row(records, eid_a, eid_b, domain, "no_match"))
                    kept += 1

                if kept < n_positives:
                    warnings.warn(
                        f"synthetic[{self.name}]: only {kept}/{n_positives} negatives "
                        f"in split {split!r} domain {domain!r} after same-entity filter",
                        stacklevel=2,
                    )

        return out

    def record_pools(self) -> dict[str, list[dict]]:
        """Per-split raw record pool (every generated record grouped by its
        entity's split via ``_entity_of``), leakage-consistent with ``splits()``
        -- used by downstream FS enrichment that needs the full record pool for a
        split, not just the sampled pairs."""
        records, _entity_domain = self._generate()
        _entities, entity_split = self._entities_and_splits(records)

        pools: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
        for rec_id, fields in records.items():
            pools[entity_split[_entity_of(rec_id)]].append(fields)
        return pools
