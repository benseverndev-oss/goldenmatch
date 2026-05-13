"""Synthetic common-surname false-positive A/B benchmark.

Companion to ``run_nickname_synth.py`` (which demonstrates the
given-name scorer) and ``run_business_synth.py`` (legal-form
transform). This one demonstrates the **surname** scorer from PR #216:
``name_freq_weighted_jw`` down-weights matches on common surnames in
the borderline JW zone [0.70, 0.95]. The fixture is built to exercise
exactly that zone.

The fixture shape:

- **TPs (200 pairs)** — same person across two records, identical first
  name, identical surname drawn from a pool of common US Census names
  (Smith, Johnson, …) plus a corrupted-typo set where one side's surname
  is the canonical and the other is an OOV typo (Smith / Smiht). The
  OOV case verifies the scorer's pass-through-to-plain-JW degradation
  doesn't regress recall on real typos.

- **FP-candidate pairs (200)** — *different* people with the same first
  name and surnames drawn from the borderline-similar-common-name pool
  (Smith vs Smyth, Johnson vs Johnsen, Jones vs Jonas, Miller vs Millar,
  Martin vs Marten, White vs Whyte). Plain Jaro–Winkler scores the
  surname pair around 0.89–0.94 — borderline. With first_name=1.0
  contributing to the weighted matchkey, the combined score squeaks
  above a 0.92 threshold and a plain-JW config calls them duplicates.
  The refdata scorer down-weights both-sides-known common-surname
  pairs in this zone (Smith/Smyth IDF-weighted = 0.769, vs plain
  0.893) — the combined score drops below threshold and the pairs are
  correctly rejected.

- **Distractors (600)** — unique surnames + unique first names; should
  never cluster.

Each pair (TP or FP-candidate) shares a unique first name with its
partner so blocking on first_name puts the pair into its own 2-record
block. No cross-pair clustering interference.

Usage::

    python tests/benchmarks/run_surname_fp_synth.py
"""
from __future__ import annotations

import random
from typing import Any

import goldenmatch
import goldenmatch.refdata  # noqa: F401
import polars as pl

SEED = 42

# Common surnames where TPs draw an exact-match pair. All are top-20 in
# the bundled US Census table.
COMMON_SURNAMES_TP = [
    "Smith", "Johnson", "Williams", "Brown", "Jones",
    "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
    "Wilson", "Anderson", "Taylor", "Moore", "Jackson",
    "Thomas", "Martin", "White", "Robinson", "Walker",
]

# OOV-typo TPs: canonical common surname vs a single-char typo'd OOV
# version. Verifies the scorer's OOV pass-through preserves recall.
OOV_TYPO_PAIRS = [
    ("Smith", "Smiht"),     ("Johnson", "Johsnon"),
    ("Brown", "Borwn"),     ("Garcia", "Garca"),
    ("Wilson", "Wlison"),   ("Anderson", "Andresson"),
    ("Thomas", "Tohmas"),   ("Jackson", "Jcksaon"),
    ("Martin", "Mratin"),   ("Robinson", "Roibnson"),
]

# FP-candidate pairs: borderline-similar common surnames (both in the
# bundled table). Plain JW scores ~0.89-0.94. With the refdata scorer
# applied, both-sides-known triggers IDF down-weighting, dropping the
# score to ~0.77-0.84.
COMMON_SIMILAR_PAIRS = [
    ("Smith", "Smyth"),     # JW 0.893 -> refdata 0.769
    ("Johnson", "Johnsen"), # JW 0.943 -> refdata 0.818
    ("Jones", "Jonas"),     # JW 0.907 -> refdata 0.790
    ("Miller", "Millar"),   # JW 0.933 -> refdata 0.821
    ("Martin", "Marten"),   # JW 0.933 -> refdata 0.840
    ("White", "Whyte"),     # JW 0.893 -> refdata 0.793
]

FIRST_NAME_POOL = [
    "James", "Mary", "John", "Patricia", "Robert", "Jennifer", "Michael",
    "Linda", "William", "Elizabeth", "David", "Barbara", "Richard",
    "Susan", "Joseph", "Jessica", "Thomas", "Sarah", "Charles", "Karen",
    "Christopher", "Lisa", "Daniel", "Nancy", "Matthew", "Betty",
    "Anthony", "Margaret", "Mark", "Sandra", "Donald", "Ashley",
    "Steven", "Kimberly", "Paul", "Donna", "Andrew", "Emily", "Joshua",
    "Michelle", "Kenneth", "Carol", "Kevin", "Amanda", "Brian", "Melissa",
    "George", "Deborah", "Edward", "Stephanie",
]


def _build_fixture(n_tp: int = 200, n_fp: int = 200, n_distract: int = 600) -> tuple[pl.DataFrame, set[tuple]]:
    rng = random.Random(SEED)
    rows: list[dict] = []
    gt: set[tuple] = set()

    used_first: set[str] = set()
    def _new_first() -> str:
        while True:
            base = rng.choice(FIRST_NAME_POOL)
            suffix = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(4))
            candidate = f"{base}{suffix}"
            if candidate not in used_first:
                used_first.add(candidate)
                return candidate

    # 80% of TPs: identical surnames (exact match, common name)
    # 20% of TPs: OOV-typo (canonical / OOV variant) — verifies pass-through
    typo_quota = n_tp // 5
    for i in range(n_tp):
        first = _new_first()
        if i < typo_quota:
            canon, typo = rng.choice(OOV_TYPO_PAIRS)
            surnames = (canon, typo) if rng.random() < 0.5 else (typo, canon)
        else:
            surname = rng.choice(COMMON_SURNAMES_TP)
            surnames = (surname, surname)
        rid_a = f"TP{i:04d}A"
        rid_b = f"TP{i:04d}B"
        rows.append({"record_id": rid_a, "first_name": first, "last_name": surnames[0]})
        rows.append({"record_id": rid_b, "first_name": first, "last_name": surnames[1]})
        gt.add((min(rid_a, rid_b), max(rid_a, rid_b)))

    # FP candidates: different people, same first_name, borderline-similar
    # common surname pair. Each gets its OWN first name so it doesn't
    # collide with a TP block.
    for i in range(n_fp):
        first = _new_first()
        a, b = rng.choice(COMMON_SIMILAR_PAIRS)
        if rng.random() < 0.5:
            a, b = b, a
        rows.append({"record_id": f"FP{i:04d}A", "first_name": first, "last_name": a})
        rows.append({"record_id": f"FP{i:04d}B", "first_name": first, "last_name": b})

    # Distractors with unique first AND last names — no FP pressure.
    distract_lastnames: set[str] = set()
    def _new_lastname() -> str:
        while True:
            s = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(8))
            if s not in distract_lastnames and len(s) > 6:
                distract_lastnames.add(s)
                return s.capitalize()

    for i in range(n_distract):
        rows.append({
            "record_id": f"D{i:04d}",
            "first_name": _new_first(),
            "last_name": _new_lastname(),
        })

    rng.shuffle(rows)
    return pl.DataFrame(rows), gt


def _build_config(last_name_scorer: str, threshold: float = 0.92) -> Any:
    from goldenmatch.config.schemas import (
        BlockingConfig,
        BlockingKeyConfig,
        GoldenMatchConfig,
        MatchkeyConfig,
        MatchkeyField,
    )

    mk = MatchkeyConfig(
        name="person",
        type="weighted",
        threshold=threshold,
        fields=[
            MatchkeyField(field="first_name", scorer="jaro_winkler", weight=1.0),
            MatchkeyField(field="last_name", scorer=last_name_scorer, weight=1.0),
        ],
    )
    # Block on first_name: TPs and FP candidates each end up in their
    # own 2-record block; distractors land in singleton blocks (skipped).
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["first_name"])],
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


def _emit(line: str, results_path) -> None:
    """Print and (if path given) also append to a results file."""
    print(line, flush=True)
    if results_path is not None:
        with open(results_path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def main(results_path: str | None = None) -> int:
    if results_path is not None:
        # Truncate any prior run.
        open(results_path, "w", encoding="utf-8").close()
    df, gt = _build_fixture()
    _emit(
        f"Fixture: {df.height:,} records "
        f"({len(gt):,} TP pairs, {200} FP-candidate pairs, "
        f"{600} distractor singletons)",
        results_path,
    )
    _emit("", results_path)

    rid_lookup = df["record_id"].to_list()
    configs = [
        ("baseline (jaro_winkler on last_name)         ", "jaro_winkler"),
        ("refdata  (name_freq_weighted_jw on last_name)", "name_freq_weighted_jw"),
    ]
    metrics = []
    for label, scorer in configs:
        cfg = _build_config(scorer, threshold=0.92)
        result = goldenmatch.dedupe_df(df, config=cfg)
        m = _score(result, rid_lookup, gt)
        metrics.append((label, m))
        _emit(label, results_path)
        _emit(
            f"  precision={m['precision']:.4f}  recall={m['recall']:.4f}  "
            f"f1={m['f1']:.4f}  (tp={m['tp']}, fp={m['fp']}, fn={m['fn']})",
            results_path,
        )
        _emit("", results_path)

    base = metrics[0][1]
    ref = metrics[1][1]
    _emit(
        f"F1 delta:  {ref['f1'] - base['f1']:+.4f}  ({base['f1']:.4f} -> {ref['f1']:.4f})",
        results_path,
    )
    _emit(
        f"P delta:   {ref['precision'] - base['precision']:+.4f}  "
        f"({base['precision']:.4f} -> {ref['precision']:.4f})",
        results_path,
    )
    _emit(
        f"R delta:   {ref['recall'] - base['recall']:+.4f}  "
        f"({base['recall']:.4f} -> {ref['recall']:.4f})",
        results_path,
    )
    return 0


if __name__ == "__main__":
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--out", help="Also write the report to this file path.")
    args = p.parse_args()
    raise SystemExit(main(args.out))
