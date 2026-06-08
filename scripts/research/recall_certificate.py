"""Unsupervised recall certificate for ER via capture-recapture.

Kill-criterion prototype for the third research direction (derived from the two
failed arcs: don't compete on accuracy on solved benchmarks; attack an
unsaturated axis above the clustering layer). The unsolved operational problem:
in production you can estimate PRECISION cheaply (sample your matches, check
them) but RECALL is unknowable without labels — you can't sample the true matches
you DIDN'T find. So every ER deployment ships blind on recall.

THE IDEA (capture-recapture / dual-system estimation, as used for census
undercount): run K decorrelated matchers; each "captures" a subset of the true
matching pairs; the OVERLAP structure of their captures estimates how many true
pairs NONE of them caught -> the hidden population -> recall, with no ground
truth. Matches found by many independent matchers are easy; the rate at which
independent matchers MISS DIFFERENT pairs is a measurable signal about the
invisible tail.

THE TEST: estimate recall via capture-recapture (Chao2 incidence estimator) WITH
NO LABELS, then compare to the TRUE recall computed from gold (which the estimator
never sees). Kill if the estimate doesn't track true recall; keep if it does —
because then we have a recall gauge that works where it's actually needed
(unlabeled production data), a regime with no incumbent baseline.

Honest known biases (the research, if pursued): correlation bias (matchers that
miss the SAME pairs -> underestimate the hidden population -> OPTIMISTIC recall)
and heterogeneity bias (pairs hard for EVERY matcher are invisible -> optimistic).
We report the gap and an independence diagnostic so the bias is visible.

Reuses the existing harness (real Febrl3 / DBLP-ACM subsamples). numpy + stdlib.

Run:
    python scripts/research/recall_certificate.py --dataset febrl3 --max-entities 80
    python scripts/research/recall_certificate.py --dataset dblp-acm --datasets-dir datasets
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from landscape_er import build_affinity, default_theta  # noqa: E402
from real_schema_encoder import _load_real  # noqa: E402


# --------------------------------------------------------------------------- #
# Gold + decorrelated field-based matchers.
# --------------------------------------------------------------------------- #
def gold_pairs(labels: np.ndarray) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    idx: dict[int, list[int]] = {}
    for i, c in enumerate(labels.tolist()):
        idx.setdefault(c, []).append(i)
    for members in idx.values():
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                out.add((members[a], members[b]))
    return out


def _field_tokens(records: list[list[str]], f: int) -> list[set[str]]:
    out = []
    for r in records:
        v = r[f] if f < len(r) else ""
        out.append(set("".join(c if c.isalnum() else " " for c in v.lower()).split()))
    return out


def matcher_pairs(records, field: int, tau: float) -> set[tuple[int, int]]:
    """One DECORRELATED matcher: declare (i,j) a match iff their FIELD-k values
    agree (token-Jaccard on field k >= tau). Crucially each matcher uses ONLY its
    own field's signal (not a shared global affinity), so a pair corrupted in
    field k is missed by matcher k but caught by matchers on clean fields ->
    genuinely decorrelated captures, the prerequisite capture-recapture needs.
    High tau keeps per-matcher precision high so D ~ true-found."""
    toks = _field_tokens(records, field)
    inv: dict[str, list[int]] = {}
    for i, s in enumerate(toks):
        for t in s:
            inv.setdefault(t, []).append(i)
    pairs: set[tuple[int, int]] = set()
    seen: set[tuple[int, int]] = set()
    for members in inv.values():
        if len(members) > 200:
            continue
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                i, j = (members[a], members[b]) if members[a] < members[b] else (members[b], members[a])
                if (i, j) in seen:
                    continue
                seen.add((i, j))
                ti, tj = toks[i], toks[j]
                if not ti or not tj:
                    continue
                jac = len(ti & tj) / len(ti | tj)
                if jac >= tau:
                    pairs.add((i, j))
    return pairs


# --------------------------------------------------------------------------- #
# Capture-recapture estimators (no labels).
# --------------------------------------------------------------------------- #
def chao2(capture_counts: dict[tuple[int, int], int], K: int) -> float:
    """Chao2 incidence estimator of the total population from K samples.
    capture_counts: pair -> number of matchers (samples) that captured it.
    N_hat = D + ((K-1)/K) * Q1^2 / (2 Q2)  (bias-corrected when Q2 == 0)."""
    D = len(capture_counts)
    if D == 0:
        return 0.0
    Q1 = sum(1 for v in capture_counts.values() if v == 1)
    Q2 = sum(1 for v in capture_counts.values() if v == 2)
    f = (K - 1) / K if K > 1 else 1.0
    if Q2 > 0:
        return D + f * (Q1 ** 2) / (2 * Q2)
    return D + f * Q1 * (Q1 - 1) / 2.0


def lincoln_petersen(n1: int, n2: int, n12: int) -> float:
    """Chapman bias-corrected 2-system estimator."""
    if n12 == 0:
        return float("inf")
    return (n1 + 1) * (n2 + 1) / (n12 + 1) - 1


# --------------------------------------------------------------------------- #
def group_matcher(records, group: list[int]) -> set[tuple[int, int]]:
    """A matcher over a DISJOINT field group: precise enough (uses several
    fields) yet decorrelated from matchers on other groups (uses different
    evidence). Pairs with group-restricted IDF-Jaccard >= calibrated theta."""
    sub = [[r[f] for f in group] for r in records]
    Wg = build_affinity(sub)
    thg = default_theta(Wg)
    n = len(records)
    pairs: set[tuple[int, int]] = set()
    xs, ys = np.where(np.triu(Wg, 1) >= thg)
    for i, j in zip(xs.tolist(), ys.tolist()):
        pairs.add((i, j))
    return pairs


def run(records, gold_labels, n_groups: int = 3):
    n_fields = len(records[0])
    gp = gold_pairs(np.asarray(gold_labels))

    # split fields into n_groups DISJOINT groups -> decorrelated-but-precise matchers
    groups = [list(range(g, n_fields, n_groups)) for g in range(n_groups)]
    groups = [g for g in groups if g]
    pred_sets = [group_matcher(records, g) for g in groups]
    pred_sets = [ps for ps in pred_sets if ps]
    K = len(pred_sets)
    union: set[tuple[int, int]] = set().union(*pred_sets) if pred_sets else set()

    # capture counts over the UNION of predicted pairs
    counts: dict[tuple[int, int], int] = {}
    for ps in pred_sets:
        for p in ps:
            counts[p] = counts.get(p, 0) + 1

    # ----- the estimate (NO gold) -----
    N_hat = chao2(counts, K)
    D = len(union)
    recall_hat = D / N_hat if N_hat > 0 else 0.0

    # ----- the truth (gold; the estimator never sees this) -----
    tp = len(union & gp)
    true_recall = tp / len(gp) if gp else 0.0
    true_precision = tp / D if D else 0.0
    # what the estimate SHOULD target: precision-corrected found-true / N_true
    # recall_hat uses D (incl. false positives) as "found"; report both.
    recall_hat_pc = (true_precision * D) / N_hat if N_hat > 0 else 0.0

    # independence diagnostic: mean Jaccard overlap between matcher capture sets
    # of TRUE pairs (high overlap => correlated => optimistic bias)
    overlaps = []
    for a in range(len(pred_sets)):
        for b in range(a + 1, len(pred_sets)):
            ta, tb = pred_sets[a] & gp, pred_sets[b] & gp
            if ta or tb:
                overlaps.append(len(ta & tb) / len(ta | tb))
    mean_overlap = float(np.mean(overlaps)) if overlaps else 0.0

    return dict(K=K, D=D, N_true=len(gp), N_hat=N_hat,
                recall_hat=recall_hat, recall_hat_pc=recall_hat_pc,
                true_recall=true_recall, true_precision=true_precision,
                mean_overlap=mean_overlap)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", choices=["febrl3", "dblp-acm"], default="febrl3")
    ap.add_argument("--datasets-dir", type=Path, default=Path("datasets"))
    ap.add_argument("--max-entities", type=int, default=80)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--groups", type=int, default=3,
                    help="number of disjoint field groups (decorrelated matchers)")
    args = ap.parse_args()

    rows = []
    for seed in range(args.seeds):
        real = _load_real(args.dataset, args.datasets_dir, args.max_entities, seed)
        if real is None:
            print(f"  [{args.dataset}] unavailable — install recordlinkage / fetch DBLP-ACM.")
            return 0
        records, gold = real
        rows.append(run(records, gold, n_groups=args.groups))

    print(f"\n  dataset={args.dataset}  (K decorrelated field-matchers, "
          f"capture-recapture recall estimate vs truth)\n")
    print(f"  {'seed':>4} {'K':>3} {'found':>6} {'N_true':>7} {'N_hat':>7} "
          f"{'recall_hat':>10} {'recall_hat_pc':>13} {'true_recall':>11} {'prec':>6} {'overlap':>8}")
    for s, r in enumerate(rows):
        print(f"  {s:>4} {r['K']:>3} {r['D']:>6} {r['N_true']:>7} {r['N_hat']:>7.0f} "
              f"{r['recall_hat']:>10.3f} {r['recall_hat_pc']:>13.3f} "
              f"{r['true_recall']:>11.3f} {r['true_precision']:>6.2f} {r['mean_overlap']:>8.2f}")

    # tracking: does recall_hat_pc (precision-corrected) track true_recall?
    rh = np.array([r["recall_hat_pc"] for r in rows])
    tr = np.array([r["true_recall"] for r in rows])
    mae = float(np.mean(np.abs(rh - tr)))
    print(f"\n  mean |recall_hat_pc - true_recall| = {mae:.3f}")
    bias = float(np.mean(rh - tr))
    print(f"  mean signed bias (est - true)        = {bias:+.3f}  "
          f"({'optimistic' if bias > 0 else 'conservative'})")
    print("\n  KILL-CRITERION:")
    if mae < 0.10:
        print(f"   PASS — capture-recapture tracks true recall within {mae:.3f} MAE "
              "with no labels. An unsupervised recall gauge looks viable.")
    else:
        print(f"   FAIL — estimate is off by {mae:.3f} MAE; the independence/"
              "heterogeneity bias dominates (see overlap column). Not yet usable.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
