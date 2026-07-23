#!/usr/bin/env python
"""Separation micro-benchmark for the FS `date_diff` comparator (spec
2026-07-23-fs-domain-comparators-design.md): does making the date scorer
magnitude-aware create the precision margin the edit-distance scorers cannot?

`date_diff` is a COMPARATOR -- it lives at the comparison-vector cell, mapping a
day-distance to a monotone [0,1] similarity. Its whole job is to separate a
data-entry TYPO (a few days) from a genuinely DIFFERENT birth date (a year+),
which the edit-distance `date`/`levenshtein` scorers conflate because both read
as "one changed digit". So the honest, decisive measurement is at the level the
comparator operates: the score it assigns to TRUE-DUP DOB pairs vs HARD-NEGATIVE
DOB pairs, and the SEPARATION MARGIN between the two populations.

  * TRUE DUPLICATES: same person, DOB typo -- day/month off by a little
    (<= ~1 month). A near-match under every metric; every scorer should keep
    these high.
  * HARD NEGATIVES: two DIFFERENT people, same name, DOB a year+ apart. Edit
    distance sees one changed digit -> ~0.90 (indistinguishable from a typo);
    `date_diff` sees 365+ days -> the weak-partial bands (<= 0.60).

A scorer is useful for this discriminator iff it leaves a POSITIVE margin
between the worst true-dup score and the best hard-negative score: any FS
threshold in that gap keeps the dups and drops the false merges. Edit distance
leaves a NEGATIVE margin (both populations pile at ~0.90 -- no threshold can
separate them); `date_diff` opens a real gap. Deterministic (seeded); no
external datasets or optional deps.

NOTE ON END-TO-END: the full zero-config FS pipeline on tiny synthetic data is a
poor lens here -- EM under-converges on a degenerate small sample and the
adaptive clustering threshold can absorb the field-score change, so all scorers
can post the same F1 while the underlying separation differs sharply. The
authoritative END-TO-END validation is `scripts/bench_er_headtohead` on real
NCVR / historical panels (run in CI, needs the gitignored datasets), where FS
training is well-conditioned. This harness proves the MECHANISM the panel then
confirms at scale.

Usage:
    python scripts/bench_date_comparators.py [--pairs 3000] [--seed 0]
"""
from __future__ import annotations

import argparse
import random

_SCORERS = ("levenshtein", "date", "date_diff")


def _dob(rng: random.Random) -> tuple[int, int, int]:
    return rng.randint(1940, 2005), rng.randint(1, 12), rng.randint(1, 28)


def _fmt(y: int, m: int, d: int) -> str:
    return f"{y:04d}-{m:02d}-{d:02d}"


def _typo_pair(rng: random.Random) -> tuple[str, str]:
    """Same person, DOB data-entry slip: day or month off by a little (stays
    within ~a month), so it is a NEAR match under day-distance too."""
    y, m, d = _dob(rng)
    if rng.random() < 0.5:
        d2 = max(1, min(28, d + rng.choice([-2, -1, 1, 2])))
        return _fmt(y, m, d), _fmt(y, m, d2)
    m2 = max(1, min(12, m + rng.choice([-1, 1])))
    return _fmt(y, m, d), _fmt(y, m2, d)


def _year_pair(rng: random.Random) -> tuple[str, str]:
    """Different person, same name: DOB a year (or a few) apart -- a large real
    gap that edit distance still reads as a one/two-digit near-match."""
    y, m, d = _dob(rng)
    y2 = y + rng.choice([-3, -2, -1, 1, 2, 3])
    return _fmt(y, m, d), _fmt(y2, m, d)


def _percentile(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank percentile on an already-sorted list (q in [0,1])."""
    if not sorted_vals:
        return 0.0
    idx = max(0, min(len(sorted_vals) - 1, round(q * (len(sorted_vals) - 1))))
    return sorted_vals[idx]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pairs", type=int, default=3000,
                    help="pairs per population (true-dup and hard-negative)")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    from goldenmatch.core.scorer import score_field

    rng = random.Random(args.seed)
    dup_pairs = [_typo_pair(rng) for _ in range(args.pairs)]
    neg_pairs = [_year_pair(rng) for _ in range(args.pairs)]

    print(f"{args.pairs} true-dup (DOB typo) + {args.pairs} hard-negative "
          f"(year-apart, same name) DOB pairs, seed {args.seed}\n")
    print("A scorer separates the populations iff  min(true-dup) > max(hard-neg).")
    print("Reported: true-dup 5th pct / mean, hard-neg mean / 95th pct, and the")
    print("SEPARATION MARGIN = (true-dup 5th pct) - (hard-neg 95th pct).\n")

    hdr = (f"{'scorer':<12} {'dup.p05':>8} {'dup.mean':>9} "
           f"{'neg.mean':>9} {'neg.p95':>8} {'margin':>8}")
    print(hdr)
    print("-" * len(hdr))

    margins: dict[str, float] = {}
    for scorer in _SCORERS:
        dup = sorted(score_field(a, b, scorer) for a, b in dup_pairs)
        neg = sorted(score_field(a, b, scorer) for a, b in neg_pairs)
        dup_p05 = _percentile(dup, 0.05)
        dup_mean = sum(dup) / len(dup)
        neg_mean = sum(neg) / len(neg)
        neg_p95 = _percentile(neg, 0.95)
        margin = dup_p05 - neg_p95
        margins[scorer] = margin
        print(f"{scorer:<12} {dup_p05:>8.3f} {dup_mean:>9.3f} "
              f"{neg_mean:>9.3f} {neg_p95:>8.3f} {margin:>+8.3f}")

    print()
    for scorer in _SCORERS:
        verdict = "SEPARATES" if margins[scorer] > 0 else "conflates"
        print(f"  {scorer:<12} margin {margins[scorer]:+.3f}  -> {verdict}")

    dd, best_ed = margins["date_diff"], max(margins["date"], margins["levenshtein"])
    print(f"\ndate_diff margin {dd:+.3f} vs best edit-distance margin {best_ed:+.3f} "
          f"(lift {dd - best_ed:+.3f})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
