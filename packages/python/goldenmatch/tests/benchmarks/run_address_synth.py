"""Synthetic address-normalization A/B benchmark.

Demonstrates the lift from ``address_normalize`` on a workload where the
dominant error mode is USPS abbreviation variation: the same address
appears in two datasets with different long/short forms ("123 Main
Street", "123 Main St", "123 Main St."). Without normalization plain JW
often misses these; with the transform applied, both sides collapse to
the same canonical form.

Usage::

    python tests/benchmarks/run_address_synth.py
"""
from __future__ import annotations

import random
from typing import Any

import goldenmatch
import goldenmatch.refdata  # noqa: F401
import polars as pl

SEED = 42

STREET_STEMS = [
    "Main", "Oak", "Maple", "Birch", "Cedar", "Elm", "Pine", "Walnut",
    "Hickory", "Sycamore", "Spruce", "Magnolia", "Willow", "Cherry",
    "Chestnut", "Dogwood", "Holly", "Juniper", "Linden", "Mulberry",
    "Poplar", "Redwood", "Sassafras", "Tamarack", "Tulip",
]

# Pairs of (long form, short form) USPS variants for the dominant
# axis of variation in the fixture. Both sides of each pair are
# recognized by address_normalize.
SUFFIX_VARIANTS = [
    ("Street", "St"),
    ("Avenue", "Ave"),
    ("Boulevard", "Blvd"),
    ("Road", "Rd"),
    ("Drive", "Dr"),
    ("Lane", "Ln"),
    ("Place", "Pl"),
    ("Court", "Ct"),
    ("Circle", "Cir"),
    ("Way", "Wy"),
]

DIRECTIONAL_VARIANTS = [
    ("North", "N"),
    ("South", "S"),
    ("East", "E"),
    ("West", "W"),
    ("Northeast", "NE"),
    ("Southwest", "SW"),
]

UNIT_VARIANTS = [
    ("Apartment", "Apt"),
    ("Suite", "Ste"),
    ("Floor", "Fl"),
    ("Building", "Bldg"),
]


def _build_fixture(n_pairs: int = 200, n_distractors: int = 600) -> tuple[pl.DataFrame, set[tuple]]:
    rng = random.Random(SEED)
    rows: list[dict] = []
    gt: set[tuple] = set()

    used_addresses: set[str] = set()
    def _new_base() -> tuple[int, str]:
        while True:
            num = rng.randint(1, 9999)
            stem = rng.choice(STREET_STEMS)
            key = f"{num}|{stem}"
            if key not in used_addresses:
                used_addresses.add(key)
                return num, stem

    for i in range(n_pairs):
        num, stem = _new_base()
        long_suffix, short_suffix = rng.choice(SUFFIX_VARIANTS)
        # 50% of pairs also vary by directional or unit
        modifier = rng.choice(["none", "directional", "unit"])
        long_extra = short_extra = ""
        if modifier == "directional":
            long_d, short_d = rng.choice(DIRECTIONAL_VARIANTS)
            long_extra = f" {long_d}"
            short_extra = f" {short_d}"
        elif modifier == "unit":
            long_u, short_u = rng.choice(UNIT_VARIANTS)
            unit_num = rng.randint(1, 999)
            long_extra = f" {long_u} {unit_num}"
            short_extra = f" {short_u} {unit_num}"

        addr_long = f"{num} {stem} {long_suffix}{long_extra}"
        addr_short = f"{num} {stem} {short_suffix}{short_extra}"
        if rng.random() < 0.5:
            addr_long, addr_short = addr_short, addr_long

        rid_a = f"P{i:04d}A"
        rid_b = f"P{i:04d}B"
        rows.append({"record_id": rid_a, "address": addr_long})
        rows.append({"record_id": rid_b, "address": addr_short})
        gt.add((min(rid_a, rid_b), max(rid_a, rid_b)))

    for i in range(n_distractors):
        num, stem = _new_base()
        suffix, _ = rng.choice(SUFFIX_VARIANTS)
        rows.append({
            "record_id": f"D{i:04d}",
            "address": f"{num} {stem} {suffix}",
        })

    rng.shuffle(rows)
    return pl.DataFrame(rows), gt


def _build_config(transforms: list[str], threshold: float = 0.95) -> Any:
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    mk = MatchkeyConfig(
        name="address",
        type="weighted",
        threshold=threshold,
        fields=[
            MatchkeyField(
                field="address",
                scorer="jaro_winkler",
                weight=1.0,
                transforms=transforms,
            ),
        ],
    )
    # Block by leading house number — coarse but keeps blocks small.
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(
            fields=["address"],
            transforms=["first_token"],
        )],
    )
    return GoldenMatchConfig(matchkeys=[mk], blocking=blocking)


def _score(result: Any, rid_lookup: list[str], gt: set[tuple]) -> dict:
    found: set[tuple] = set()
    for c in result.clusters.values():
        members = sorted(c["members"])
        for i in range(len(members)):
            for j in range(i + 1, len(members)):
                a, b = rid_lookup[members[i]], rid_lookup[members[j]]
                found.add((min(a, b), max(a, b)))
    tp = len(found & gt)
    fp = len(found - gt)
    fn = len(gt - found)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f1}


def main() -> int:
    df, gt = _build_fixture()
    print(f"Fixture: {df.height:,} records, {len(gt):,} duplicate GT pairs "
          f"(USPS-abbreviation shape)")
    print()

    rid_lookup = df["record_id"].to_list()
    configs = [
        ("baseline (no transform)            ", []),
        ("baseline (lowercase only)          ", ["lowercase"]),
        ("refdata  (address_normalize)       ", ["address_normalize"]),
    ]
    metrics = []
    for label, transforms in configs:
        cfg = _build_config(transforms)
        result = goldenmatch.dedupe_df(df, config=cfg)
        m = _score(result, rid_lookup, gt)
        metrics.append((label, m))
        print(label)
        print(f"  precision={m['precision']:.4f}  recall={m['recall']:.4f}  "
              f"f1={m['f1']:.4f}  (tp={m['tp']}, fp={m['fp']}, fn={m['fn']})")
        print()

    base = metrics[0][1]
    best = max(metrics[1:], key=lambda m: m[1]["f1"])[1]
    print(f"F1 delta:  {best['f1'] - base['f1']:+.4f}  ({base['f1']:.4f} -> {best['f1']:.4f})")
    print(f"P delta:   {best['precision'] - base['precision']:+.4f}")
    print(f"R delta:   {best['recall'] - base['recall']:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
