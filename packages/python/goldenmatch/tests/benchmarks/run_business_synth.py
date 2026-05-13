"""Synthetic business-name A/B benchmark.

Demonstrates the lift from ``legal_form_strip`` on a workload where the
dominant error mode is legal-form variation: the same company appears in
two datasets with different corporate suffixes ("Acme Inc.", "Acme
Incorporated", "Acme Corp."). Without normalization plain JW often
scores these below the dedupe threshold; with the transform applied, the
stripped forms match exactly.

Usage::

    python tests/benchmarks/run_business_synth.py
"""
from __future__ import annotations

import random
from typing import Any

import goldenmatch
import goldenmatch.refdata  # noqa: F401  registers transform
import polars as pl

SEED = 42

# Real-feel business stems + the legal-form variations the same business
# might appear under across sources. All forms are in the bundled list.
BUSINESS_STEMS = [
    "Acme", "Pioneer", "Summit", "Helix", "Anchor",
    "Northwind", "Birchwood", "Crestmont", "Lumen", "Harbor",
    "Vanguard", "Cobalt", "Stellar", "Riverline", "Quincy",
    "Brightway", "Beacon", "Forge", "Latitude", "Cascade",
    "Meridian", "Atlas", "Vector", "Halcyon", "Pinnacle",
]

LEGAL_FORM_VARIANTS = [
    ("Inc.", "Incorporated"),
    ("Inc", "Corp."),
    ("Corp.", "Corporation"),
    ("LLC", "Limited Liability Company"),
    ("LLC", "L.L.C."),
    ("Co.", "Company"),
    ("Ltd.", "Limited"),
    ("LLP", "Limited Liability Partnership"),
    ("GmbH", "AG"),
    ("Pty Ltd", "Pty. Ltd."),
]


def _build_fixture(n_pairs: int = 200, n_distractors: int = 600) -> tuple[pl.DataFrame, set[tuple]]:
    rng = random.Random(SEED)
    rows: list[dict] = []
    gt: set[tuple] = set()

    # Generate enough unique stems by suffixing — naive ints would risk
    # JW-similar names (Stem1 vs Stem2 are nearly identical). Use random
    # alpha suffixes.
    used: set[str] = set()
    def _new_stem() -> str:
        while True:
            stem = rng.choice(BUSINESS_STEMS) + "_" + "".join(
                rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(6)
            )
            if stem not in used:
                used.add(stem)
                return stem

    for i in range(n_pairs):
        stem = _new_stem()
        suffix_a, suffix_b = rng.choice(LEGAL_FORM_VARIANTS)
        if rng.random() < 0.5:
            suffix_a, suffix_b = suffix_b, suffix_a
        rid_a = f"P{i:04d}A"
        rid_b = f"P{i:04d}B"
        rows.append({"record_id": rid_a, "company_name": f"{stem} {suffix_a}"})
        rows.append({"record_id": rid_b, "company_name": f"{stem} {suffix_b}"})
        gt.add((min(rid_a, rid_b), max(rid_a, rid_b)))

    # Distractors: unrelated company names with random legal forms.
    for i in range(n_distractors):
        stem = _new_stem()
        suffix, _ = rng.choice(LEGAL_FORM_VARIANTS)
        rows.append({"record_id": f"D{i:04d}", "company_name": f"{stem} {suffix}"})

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
        name="company",
        type="weighted",
        threshold=threshold,
        fields=[
            MatchkeyField(
                field="company_name",
                scorer="jaro_winkler",
                weight=1.0,
                transforms=transforms,
            ),
        ],
    )
    # Block by the first letter of company_name — coarse but enough to
    # keep blocks small while letting alias-equivalent pairs land
    # together (Acme Inc. and Acme Incorporated both start with 'A').
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(
            fields=["company_name"],
            transforms=["lowercase", "first_token"],
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
          f"(legal-form-variation shape)")
    print()

    rid_lookup = df["record_id"].to_list()
    configs = [
        ("baseline (no transform)            ", []),
        ("refdata  (legal_form_strip)        ", ["legal_form_strip"]),
        ("refdata  (legal_form_strip+lower)  ", ["legal_form_strip", "lowercase"]),
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
