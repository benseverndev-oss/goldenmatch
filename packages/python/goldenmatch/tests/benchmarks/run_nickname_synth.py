"""Synthetic nickname-shape A/B benchmark.

Builds a tiny synthetic dataset where the dominant error mode is
**given-name nickname variation** (William↔Bill, Robert↔Bob, etc.) —
exactly the failure mode ``given_name_aliased_jw`` is built for. This
fixture exists because NCVR's corruption distribution doesn't exercise
nicknames (its corrupted names are typos, drops, abbreviations to single
letters — not name aliases), so the lift from the refdata scorer can't be
measured there.

This is direction #5's job, abridged: one shape, one comparison, one
demonstrable lift. The full enterprise-shape benchmark suite is future
work.

Usage::

    python tests/benchmarks/run_nickname_synth.py
"""
from __future__ import annotations

import random
from typing import Any

import goldenmatch
import goldenmatch.refdata  # noqa: F401  registers scorers
import polars as pl

SEED = 42

# Canonical → list of common nicknames. Pull from the bundled alias table
# but pick a curated subset so the test fixture is stable across data
# refreshes. These are all in the v1 bundle as of 2026-05-13.
NICKNAME_PAIRS: list[tuple[str, str]] = [
    ("William", "Bill"),
    ("Robert", "Bob"),
    ("Richard", "Rick"),
    ("Margaret", "Peggy"),
    ("Elizabeth", "Beth"),
    ("Catherine", "Kate"),
    ("James", "Jim"),
    ("Michael", "Mike"),
    ("Christopher", "Chris"),
    ("Patricia", "Pat"),
    ("Joseph", "Joe"),
    ("Thomas", "Tom"),
    ("Charles", "Chuck"),
    ("Samuel", "Sam"),
    ("Theodore", "Ted"),
    ("Anthony", "Tony"),
    ("Stephen", "Steve"),
    ("Daniel", "Dan"),
    ("Donald", "Don"),
    ("Susan", "Sue"),
]

def _build_fixture(n_dupes: int = 200, n_distractors: int = 600) -> tuple[pl.DataFrame, set[tuple]]:
    """Build a synthetic dataset with deliberate nickname-pair duplicates.

    Each duplicate pair shares an isolated surname (so no transitive
    cluster-join across pairs) and differs in first_name via a well-known
    nickname alias. Distractor records have unique-ish first AND last
    names so plain JW won't accidentally cluster them.

    Ground truth is the set of (record_id_a, record_id_b) duplicate pairs.
    """
    rng = random.Random(SEED)
    rows: list[dict] = []
    gt: set[tuple] = set()

    # Unique fictional surname per duplicate pair — isolates each pair to
    # its own block so clustering doesn't transitively join different
    # alias pairs that happen to share a surname.
    for i in range(n_dupes):
        canon, nick = rng.choice(NICKNAME_PAIRS)
        surname = f"Pairsurname{i:04d}"
        rid_a = f"P{i:04d}A"
        rid_b = f"P{i:04d}B"
        rows.append({"record_id": rid_a, "first_name": canon, "last_name": surname})
        rows.append({"record_id": rid_b, "first_name": nick, "last_name": surname})
        gt.add((min(rid_a, rid_b), max(rid_a, rid_b)))

    # Distractor first_names: random alpha strings, not aliases of any
    # known canonical, not similar to each other under JW.
    used_first = {p[0].lower() for p in NICKNAME_PAIRS} | {p[1].lower() for p in NICKNAME_PAIRS}
    distractor_firsts: set[str] = set()
    while len(distractor_firsts) < n_distractors:
        s = "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(8))
        if s in used_first or s in distractor_firsts:
            continue
        distractor_firsts.add(s)
    for i, first in enumerate(distractor_firsts):
        # Each distractor gets its own surname too so no cross-pair
        # clustering through shared last_name.
        rows.append({
            "record_id": f"D{i:04d}",
            "first_name": first.capitalize(),
            "last_name": f"Distinctsurname{i:04d}",
        })

    rng.shuffle(rows)
    return pl.DataFrame(rows), gt


def _build_config(first_name_scorer: str) -> Any:
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
        threshold=0.95,
        fields=[
            MatchkeyField(field="first_name", scorer=first_name_scorer, weight=1.0),
            MatchkeyField(field="last_name", scorer="jaro_winkler", weight=1.0),
        ],
    )
    blocking = BlockingConfig(
        strategy="static",
        keys=[BlockingKeyConfig(fields=["last_name"])],
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
    print(f"Fixture: {df.height:,} records, {len(gt):,} duplicate GT pairs (nickname-shape)")
    print()

    rid_lookup = df["record_id"].to_list()

    configs = [
        ("baseline (jaro_winkler on first_name)", "jaro_winkler"),
        ("refdata  (given_name_aliased_jw)     ", "given_name_aliased_jw"),
    ]
    metrics = []
    for label, scorer in configs:
        cfg = _build_config(scorer)
        result = goldenmatch.dedupe_df(df, config=cfg)
        m = _score(result, rid_lookup, gt)
        metrics.append((label, m))
        print(f"{label}")
        print(f"  precision={m['precision']:.4f}  recall={m['recall']:.4f}  "
              f"f1={m['f1']:.4f}  (tp={m['tp']}, fp={m['fp']}, fn={m['fn']})")
        print()

    base = metrics[0][1]
    ref = metrics[1][1]
    print(f"F1 delta:  {ref['f1'] - base['f1']:+.4f}  ({base['f1']:.4f} -> {ref['f1']:.4f})")
    print(f"P delta:   {ref['precision'] - base['precision']:+.4f}")
    print(f"R delta:   {ref['recall'] - base['recall']:+.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
