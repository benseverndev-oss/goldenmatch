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


def chao2_var(counts: dict, K: int) -> float:
    """Analytic variance of the Chao2 estimator (Chao 1987, incidence form)."""
    D = len(counts)
    Q1 = sum(1 for v in counts.values() if v == 1)
    Q2 = sum(1 for v in counts.values() if v == 2)
    c = (K - 1) / K if K > 1 else 1.0
    if Q2 == 0:
        return 0.0
    r = Q1 / Q2
    return Q2 * ((c / 2) * r ** 2 + c ** 2 * r ** 3 + (c ** 2 / 4) * r ** 4)


def log_ci(N_hat: float, D: int, var: float, z: float = 1.96):
    """Chao's log-transformed CI: keeps the lower bound >= D (can't estimate
    fewer than observed) and gives an asymmetric interval on N."""
    f0 = max(N_hat - D, 1e-9)
    if var <= 0:
        return N_hat, N_hat
    C = np.exp(z * np.sqrt(np.log(1.0 + var / f0 ** 2)))
    return D + f0 / C, D + f0 * C


def loglinear_independence(histories: list[tuple[int, ...]], K: int) -> float:
    """Poisson log-linear (main-effects/independence) multiple-systems estimate
    via IRLS. Generalises Lincoln-Petersen to K lists; the missing all-zero cell
    is exp(intercept). (Pairwise-interaction terms — which model matcher
    CORRELATION — need K>=4 cells to be estimable; flagged, not fit here.)"""
    from collections import Counter
    cells = Counter(histories)
    keys = list(cells.keys())
    # need >= 3 lists and more observed cells than params, else the missing cell
    # isn't identifiable (saturated -> garbage extrapolation).
    if K < 3 or len(keys) <= 1 + K:
        return float("nan")
    y = np.array([cells[k] for k in keys], dtype=float)
    X = np.array([[1.0] + list(k) for k in keys], dtype=float)  # intercept + K mains
    beta = np.zeros(X.shape[1])
    for _ in range(50):
        mu = np.exp(X @ beta)
        Wd = mu
        XtWX = X.T @ (X * Wd[:, None])
        z = X @ beta + (y - mu) / np.maximum(mu, 1e-9)
        try:
            beta_new = np.linalg.solve(XtWX, X.T @ (Wd * z))
        except np.linalg.LinAlgError:
            return float("nan")
        if np.max(np.abs(beta_new - beta)) < 1e-8:
            beta = beta_new
            break
        beta = beta_new
    missing = float(np.exp(beta[0]))   # all-zero history -> only intercept active
    return len(histories) + missing    # D observed + estimated hidden cell


def wilson_ci(k: int, n: int, z: float = 1.96):
    """Wilson score interval for a binomial proportion (precision sampling)."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    d = 1 + z ** 2 / n
    centre = (p + z ** 2 / (2 * n)) / d
    half = (z * np.sqrt(p * (1 - p) / n + z ** 2 / (4 * n ** 2))) / d
    return max(0.0, centre - half), min(1.0, centre + half)


def sample_precision(union: list, gold: set, n_sample: int, rng):
    """REAL precision sampling: label a small uniform sample of predicted pairs
    with an oracle (here gold, used ONLY on the sample — the legitimately-cheap
    production operation). Returns (p_hat, p_lo, p_hi) with a Wilson CI."""
    if not union:
        return 0.0, 0.0, 0.0
    idx = rng.sample(range(len(union)), min(n_sample, len(union)))
    k = sum(1 for t in (union[i] for i in idx) if t in gold)
    n = len(idx)
    lo, hi = wilson_ci(k, n)
    return k / n, lo, hi


# --------------------------------------------------------------------------- #
def group_matcher(records, group: list[int], max_post: int = 150) -> set[tuple[int, int]]:
    """A matcher over a DISJOINT field group, BLOCKED so it scales to full
    datasets: candidate pairs share a non-ubiquitous token in the group's fields;
    keep those with group-restricted IDF-Jaccard >= calibrated theta. Precise
    enough (several fields) yet decorrelated from other groups (different fields).
    """
    toks = []
    for r in records:
        s: set[str] = set()
        for f in group:
            v = r[f] if f < len(r) else ""
            for t in "".join(c if c.isalnum() else " " for c in v.lower()).split():
                s.add(t)
        toks.append(s)
    n = len(records)
    df: dict[str, int] = {}
    for s in toks:
        for t in s:
            df[t] = df.get(t, 0) + 1
    idf = {t: __import__("math").log(1.0 + n / c) for t, c in df.items()}
    # inverted index; skip ubiquitous tokens (huge blocks) for tractability
    inv: dict[str, list[int]] = {}
    for i, s in enumerate(toks):
        for t in s:
            inv.setdefault(t, []).append(i)
    cand: set[tuple[int, int]] = set()
    for t, members in inv.items():
        if len(members) > max_post:
            continue
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                i, j = members[a], members[b]
                cand.add((i, j) if i < j else (j, i))
    # affinity on candidates; calibrate theta = mean+2std of candidate affinities
    affs = {}
    for (i, j) in cand:
        a, b = toks[i], toks[j]
        if not a and not b:
            continue
        inter = sum(idf[t] for t in (a & b))
        union = sum(idf[t] for t in (a | b))
        affs[(i, j)] = inter / union if union else 0.0
    vals = np.array(list(affs.values())) if affs else np.array([0.0])
    theta = float(vals.mean() + 2.0 * vals.std()) if vals.size > 3 else 0.3
    return {p for p, w in affs.items() if w >= theta}


def run(records, gold_labels, n_groups: int = 3, n_precision: int = 80, seed: int = 0):
    import random
    rng = random.Random(seed)
    n_fields = len(records[0])
    gp = gold_pairs(np.asarray(gold_labels))

    # split fields into n_groups DISJOINT groups -> decorrelated-but-precise matchers
    groups = [list(range(g, n_fields, n_groups)) for g in range(n_groups)]
    groups = [g for g in groups if g]
    pred_sets = [group_matcher(records, g) for g in groups]
    pred_sets = [ps for ps in pred_sets if ps]
    K = len(pred_sets)
    union_set: set[tuple[int, int]] = set().union(*pred_sets) if pred_sets else set()
    union = sorted(union_set)
    D = len(union)

    # capture counts + capture histories over the union
    counts: dict[tuple[int, int], int] = {p: 0 for p in union}
    hist: dict[tuple[int, int], list[int]] = {p: [0] * K for p in union}
    for gi, ps in enumerate(pred_sets):
        for p in ps:
            counts[p] += 1
            hist[p][gi] = 1
    histories = [tuple(hist[p]) for p in union]

    # ----- population estimates (NO gold) -----
    N_chao = chao2(counts, K)
    N_var = chao2_var(counts, K)
    N_lo, N_hi = log_ci(N_chao, D, N_var)
    N_ll = loglinear_independence(histories, K)

    # ----- REAL precision sampling (oracle on a small sample only) -----
    p_hat, p_lo, p_hi = sample_precision(union, gp, n_precision, rng)

    # ----- recall point + 95% CI + CONSERVATIVE lower bound -----
    def rec(found, N):
        return found / N if N > 0 else 0.0
    recall_point = rec(p_hat * D, N_chao)
    recall_hi = rec(p_hi * D, N_lo)               # optimistic end
    recall_lo = rec(p_lo * D, N_hi)               # conservative end (safety bound)

    # ----- truth (gold; never seen by the estimator) -----
    tp = len(union_set & gp)
    true_recall = tp / len(gp) if gp else 0.0
    true_precision = tp / D if D else 0.0

    overlaps = []
    for a in range(len(pred_sets)):
        for b in range(a + 1, len(pred_sets)):
            ta, tb = pred_sets[a] & gp, pred_sets[b] & gp
            if ta or tb:
                overlaps.append(len(ta & tb) / len(ta | tb))
    mean_overlap = float(np.mean(overlaps)) if overlaps else 0.0

    return dict(K=K, D=D, N_true=len(gp), N_chao=N_chao, N_lo=N_lo, N_hi=N_hi,
                N_ll=N_ll, p_hat=p_hat, p_lo=p_lo, p_hi=p_hi,
                recall_point=recall_point, recall_lo=recall_lo, recall_hi=recall_hi,
                true_recall=true_recall, true_precision=true_precision,
                mean_overlap=mean_overlap)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", choices=["febrl3", "dblp-acm"], default="febrl3")
    ap.add_argument("--datasets-dir", type=Path, default=Path("datasets"))
    ap.add_argument("--max-entities", type=int, default=80)
    ap.add_argument("--full", action="store_true", help="use the FULL dataset")
    ap.add_argument("--groups", type=int, default=3,
                    help="number of disjoint field groups (>=3 enables log-linear)")
    ap.add_argument("--precision-sample", type=int, default=80,
                    help="# pairs labelled by the oracle to estimate precision")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    max_ent = 10 ** 9 if args.full else args.max_entities
    real = _load_real(args.dataset, args.datasets_dir, max_ent, args.seed)
    if real is None:
        print(f"  [{args.dataset}] unavailable — install recordlinkage / fetch DBLP-ACM.")
        return 0
    records, gold = real
    r = run(records, gold, n_groups=args.groups,
            n_precision=args.precision_sample, seed=args.seed)

    scope = "FULL" if args.full else f"{args.max_entities}-entity subsample"
    print(f"\n  dataset={args.dataset} ({scope})  N={len(records)}  "
          f"K={r['K']} disjoint field-group matchers  precision-sample={args.precision_sample}\n")
    print(f"  population (true pairs):  N_true={r['N_true']}   found(D)={r['D']}")
    print(f"    Chao2  N_hat = {r['N_chao']:.0f}   95% CI [{r['N_lo']:.0f}, {r['N_hi']:.0f}]")
    if r['N_ll'] == r['N_ll']:    # not nan
        print(f"    log-linear (indep) N_hat = {r['N_ll']:.0f}")
    else:
        print(f"    log-linear (indep) N_hat = n/a (needs K>=3 non-empty groups)")
    print(f"  precision (sampled, n={args.precision_sample}): "
          f"p_hat={r['p_hat']:.3f}  Wilson CI [{r['p_lo']:.3f}, {r['p_hi']:.3f}]  "
          f"(true={r['true_precision']:.3f})")
    print(f"  matcher overlap (indep diagnostic): {r['mean_overlap']:.2f}\n")
    print(f"  RECALL estimate (no labels):  point={r['recall_point']:.3f}   "
          f"95% CI [{r['recall_lo']:.3f}, {r['recall_hi']:.3f}]")
    print(f"  >>> CONSERVATIVE lower bound (safety): recall >= {r['recall_lo']:.3f}")
    print(f"  TRUE recall (gold, never seen):       {r['true_recall']:.3f}")

    covered = r['recall_lo'] <= r['true_recall'] <= r['recall_hi']
    safe = r['recall_lo'] <= r['true_recall'] + 1e-9
    print("\n  VERDICT:")
    print(f"   - 95% CI covers true recall:            {'YES' if covered else 'NO'}")
    print(f"   - conservative bound is a true LOWER bound: {'YES' if safe else 'NO (optimistic — unsafe)'}")
    print("   (Conservative bound is sound w.r.t. sampling variance; heterogeneity\n"
          "    — pairs hard for EVERY group — can still make it optimistic. See overlap.)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
