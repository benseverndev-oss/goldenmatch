"""Landscape-sculpting ER (v0) vs a discrete split/merge baseline.

Kill-criterion prototype for the topology/geometry angle
(see the 2026-06-07 prior-art scan). The scan found that the ITERATIVE add/split
ER loop is partly anticipated (pBlocking, Gruenheid incremental RL, Sayari), so
the only defensible novelty is the MECHANISM: expressing the loop as SCULPTING A
POTENTIAL/ATTRACTOR LANDSCAPE (carve basins, raise ridges) rather than as discrete
graph edits. Novelty of representation is necessary, not sufficient — this script
tests the empirical question head-on:

    Does the landscape mechanism beat a DISCRETE split/merge loop that optimises
    the SAME objective on the SAME graph? If it just reproduces the discrete
    loop's partition, the novelty is cosmetic and we drop it.

FAIR-FIGHT DESIGN — everything shared except the mechanism:
  * same affinity graph W (IDF-token-weighted Jaccard over all fields),
  * same correlation-clustering objective (every move accepted iff it lowers it),
  * same Fiedler 2-cut primitive for proposing splits,
  * same initial partition (connected components of W at a moderate threshold).
Only the MECHANISM differs:
  - LANDSCAPE: records are marbles; basins = attractors; assignment = clamped
    label-propagation over W (rolling downhill). A SPLIT raises a ridge
    (zeroes the cut edges in W) + adds two attractors, then RE-FLOWS GLOBALLY
    (every record may re-route across the modified terrain). A stranded record
    (low settle score) CARVES a new basin (new attractor).
  - DISCRETE (Gruenheid-style): operates directly on labels with merge / split /
    move-record moves; no terrain, no global re-flow.

Reuses the existing harness (real Febrl3 / DBLP-ACM subsamples, pairwise F1).
numpy + stdlib only.

Run:
    python scripts/research/landscape_er.py --dataset febrl3 --max-entities 80
    python scripts/research/landscape_er.py --dataset dblp-acm --datasets-dir datasets
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from amortized_partition_er import pairwise_f1  # noqa: E402
from real_schema_encoder import _load_real  # noqa: E402
import richer_simulator as RR  # noqa: E402


# --------------------------------------------------------------------------- #
# Synthetic MULTI-SOURCE-CONFLICT regime: distinctive entities + spurious
# BRIDGES (shared placeholder/boilerplate values used by a few unrelated
# entities -> high cross-entity affinity). NO single threshold separates these
# (bridge affinity rivals within-entity affinity), but graph STRUCTURE can cut
# them. This is the "shared-value collapse" (info@ email) the design flagged,
# and the scan predicted is where the ridge-raising mechanism should matter.
# --------------------------------------------------------------------------- #
def simulate_conflict(n_entities: int, rng, bridge_rate: float = 0.6):
    import random
    sizes = RR._sample_cluster_sizes(n_entities, rng)
    # placeholder pool: distinctive multi-token phrases, each shared by a few
    # unrelated entities (moderate frequency -> survives IDF -> a real bridge).
    n_ph = max(2, n_entities // 5)
    placeholders = [
        f"{rng.choice(RR.STREET)} {rng.choice(RR.CITY)} {rng.randint(100,999)}"
        for _ in range(n_ph)
    ]
    ent_ph: dict[int, str] = {}
    for e in range(n_entities):
        if rng.random() < 0.5:                      # ~half the entities bridged
            ent_ph[e] = rng.choice(placeholders)
    records, labels = [], []
    for e, sz in enumerate(sizes):
        truth = RR._truth(rng)
        for _ in range(sz):
            rec = RR._make_record(truth, rng)
            if e in ent_ph and rng.random() < bridge_rate:
                # overwrite SEVERAL fields with the shared placeholder so the
                # cross-entity bridge affinity exceeds theta (genuinely fools a
                # threshold); the bridge stays a SPARSE cut (only bridged records),
                # so graph structure can still separate the two entity-cliques.
                ph_tok = ent_ph[e].split()
                fields_to_hit = rng.sample(range(len(rec)), min(4, len(rec)))
                for fi, tok in zip(fields_to_hit, ph_tok + ph_tok):
                    rec[fi] = tok
            records.append(rec)
            labels.append(e)
    idx = list(range(len(records)))
    rng.shuffle(idx)
    return [records[i] for i in idx], [labels[i] for i in idx]


# --------------------------------------------------------------------------- #
# Affinity graph: IDF-token-weighted Jaccard over all fields (step-1 lesson).
# --------------------------------------------------------------------------- #
def _tokens(fields: list[str]) -> set[str]:
    out: set[str] = set()
    for v in fields:
        for t in "".join(c if c.isalnum() else " " for c in v.lower()).split():
            out.add(t)
    return out


def build_affinity(records: list[list[str]]) -> np.ndarray:
    """Dense IDF-token-weighted Jaccard affinity in [0,1]. Dense (not kNN) so the
    correlation-clustering objective sees every true pair (sparsifying would
    penalise gold for grouping true co-referents that missed the kNN cut)."""
    n = len(records)
    toks = [_tokens(r) for r in records]
    df: dict[str, int] = {}
    for s in toks:
        for t in s:
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log(1.0 + n / c) for t, c in df.items()}
    W = np.zeros((n, n), dtype=np.float64)
    for i in range(n):
        for j in range(i + 1, n):
            a, b = toks[i], toks[j]
            if not a and not b:
                continue
            inter = sum(idf[t] for t in (a & b))
            union = sum(idf[t] for t in (a | b))
            w = inter / union if union else 0.0
            W[i, j] = W[j, i] = w
    return W


# --------------------------------------------------------------------------- #
# Shared objective: CORRELATION CLUSTERING (the canonical ER clustering
# objective; "(Almost) All of ER" calls it the most natural setting). Lower =
# fewer disagreements. With signed weights S = affinity - theta:
#   within-cluster pair: pay max(0, -S) = max(0, theta - aff)   [grouped unlike]
#   across-cluster pair: pay max(0,  S) = max(0, aff - theta)   [split alike]
# theta is the precision/recall knob (data-driven by default). Penalises BOTH
# over-merge and under-merge symmetrically; its optimum sits near gold.
# --------------------------------------------------------------------------- #
def cost(labels: np.ndarray, W: np.ndarray, theta: float) -> float:
    labels = np.asarray(labels)
    S = W - theta
    same = labels[:, None] == labels[None, :]
    M = np.where(same, np.maximum(0.0, -S), np.maximum(0.0, S))
    return float(np.triu(M, 1).sum())


def default_theta(W: np.ndarray) -> float:
    """Unsupervised theta = mean + 2*std of nonzero affinities.

    The affinity distribution is junk-dominated (most nonzero pairs share one
    low-IDF token, mean ~0.05) with a thin high tail of true co-referents.
    mean+2std sits just above the junk and below the true-pair mass; verified to
    put connected-components F1 >= 0.98 on clean Febrl3/DBLP-ACM subsamples
    (median is too low -> over-merge; Otsu lands in the tail -> all-singletons).
    """
    nz = W[np.triu_indices_from(W, 1)]
    nz = nz[nz > 0]
    if nz.size < 4:
        return 0.2
    return float(nz.mean() + 2.0 * nz.std())


# --------------------------------------------------------------------------- #
# Shared Fiedler 2-cut primitive (proposes how to split a cluster).
# --------------------------------------------------------------------------- #
def fiedler_split(members: list[int], W: np.ndarray):
    if len(members) < 4:
        return None
    m = np.array(members)
    A = W[np.ix_(m, m)].copy()
    d = A.sum(1)
    if (d <= 0).any():
        return None
    Dinv = 1.0 / np.sqrt(d)
    L = np.eye(len(m)) - (Dinv[:, None] * A * Dinv[None, :])
    try:
        vals, vecs = np.linalg.eigh(L)
    except np.linalg.LinAlgError:
        return None
    fied = vecs[:, 1]                            # second-smallest eigenvector
    left = m[fied >= 0].tolist()
    right = m[fied < 0].tolist()
    if not left or not right:
        return None
    return left, right


def _connected_components(W: np.ndarray, thresh: float) -> np.ndarray:
    n = W.shape[0]
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    xs, ys = np.where(np.triu(W, 1) >= thresh)
    for a, b in zip(xs.tolist(), ys.tolist()):
        parent[find(a)] = find(b)
    roots = [find(i) for i in range(n)]
    remap = {r: k for k, r in enumerate(sorted(set(roots)))}
    return np.array([remap[r] for r in roots])


def _medoids(labels: np.ndarray, W: np.ndarray) -> list[int]:
    med = []
    for c in sorted(set(labels.tolist())):
        m = np.where(labels == c)[0]
        med.append(int(m[np.argmax(W[np.ix_(m, m)].sum(1))]))
    return med


# --------------------------------------------------------------------------- #
# LANDSCAPE mechanism: marbles roll to attractors via clamped label propagation;
# splits raise ridges (edit W) + add attractors + RE-FLOW globally; stranded
# marbles carve new basins.
# --------------------------------------------------------------------------- #
def _propagate(W: np.ndarray, attractors: list[int], alpha=0.85, iters=30):
    """Clamped label propagation: each attractor is a clamped seed; mass flows
    downhill through W. Returns (labels, settle_score) where settle = top score."""
    n = W.shape[0]
    K = len(attractors)
    rowsum = W.sum(1, keepdims=True)
    rowsum[rowsum == 0] = 1.0
    P = W / rowsum                               # row-stochastic transition
    S0 = np.zeros((n, K))
    for k, a in enumerate(attractors):
        S0[a, k] = 1.0
    S = S0.copy()
    seed_mask = np.zeros(n, dtype=bool)
    seed_mask[attractors] = True
    for _ in range(iters):
        S = alpha * (P @ S) + (1 - alpha) * S0
        S[seed_mask] = S0[seed_mask]             # clamp attractors
    lab = np.argmax(S, 1)
    settle = S[np.arange(n), lab]
    # margin = top1 - top2 (ambiguity / "on a ridge")
    part = np.partition(S, -2, axis=1)
    margin = part[:, -1] - part[:, -2]
    return lab, settle, margin


def landscape_loop(W0: np.ndarray, init_labels: np.ndarray,
                   max_iter: int = 60, theta: float = 0.3):
    W = W0.copy()
    attractors = _medoids(init_labels, W)
    labels, settle, margin = _propagate(W, attractors)
    best = cost(_relabel(labels), W0, theta=theta)
    moves = 0
    for _ in range(max_iter):
        improved = False
        cur = _relabel(labels)
        # ----- proposal A: SPLIT an impure basin (raise a ridge + reflow) -----
        for c in sorted(set(cur.tolist())):
            members = np.where(cur == c)[0].tolist()
            cut = fiedler_split(members, W)
            if cut is None:
                continue
            left, right = cut
            Wt = W.copy()
            li, ri = np.array(left), np.array(right)
            Wt[np.ix_(li, ri)] = 0.0            # raise the ridge
            Wt[np.ix_(ri, li)] = 0.0
            la = int(li[np.argmax(W[np.ix_(li, li)].sum(1))])
            ra = int(ri[np.argmax(W[np.ix_(ri, ri)].sum(1))])
            new_attr = [a for a in attractors if a not in members] + [la, ra]
            lab2, _, _ = _propagate(Wt, new_attr)
            c2 = cost(_relabel(lab2), W0, theta=theta)
            if c2 < best - 1e-9:
                W, attractors, labels = Wt, new_attr, lab2
                best, improved, moves = c2, True, moves + 1
                break
        if improved:
            labels, settle, margin = _propagate(W, attractors)
            continue
        # ----- proposal B: MERGE two basins (remove an attractor + reflow) -----
        #   the landscape analogue of lowering the ridge between two wells.
        if len(attractors) > 1:
            aff = W[np.ix_(attractors, attractors)]
            cand = []
            for x in range(len(attractors)):
                for y in range(x + 1, len(attractors)):
                    cand.append((aff[x, y], x, y))
            cand.sort(reverse=True)
            for _, x, y in cand[:20]:
                new_attr = [a for kk, a in enumerate(attractors) if kk != y]
                lab2, _, _ = _propagate(W, new_attr)
                c2 = cost(_relabel(lab2), W0, theta=theta)
                if c2 < best - 1e-9:
                    attractors, labels = new_attr, lab2
                    best, improved, moves = c2, True, moves + 1
                    break
        if improved:
            labels, settle, margin = _propagate(W, attractors)
            continue
        # ----- proposal C: CARVE a basin for the most-stranded marble -----
        order = np.argsort(settle + margin)      # worst-settled / most ambiguous
        for i in order[:8].tolist():
            if i in attractors:
                continue
            new_attr = attractors + [i]
            lab2, _, _ = _propagate(W, new_attr)
            c2 = cost(_relabel(lab2), W0, theta=theta)
            if c2 < best - 1e-9:
                attractors, labels = new_attr, lab2
                best, improved, moves = c2, True, moves + 1
                break
        if not improved:
            break
        labels, settle, margin = _propagate(W, attractors)
    return _relabel(labels), best, moves


# --------------------------------------------------------------------------- #
# DISCRETE baseline (Gruenheid-style): merge / split / move on labels directly.
# Same cost ledger, same Fiedler split, NO terrain, NO global re-flow.
# --------------------------------------------------------------------------- #
def discrete_loop(W: np.ndarray, init_labels: np.ndarray, max_iter: int = 200,
                  theta: float = 0.3):
    labels = _relabel(init_labels.copy())
    best = cost(labels, W, theta=theta)
    moves = 0
    for _ in range(max_iter):
        improved = False
        clusters = sorted(set(labels.tolist()))
        # ----- split -----
        for c in clusters:
            members = np.where(labels == c)[0].tolist()
            cut = fiedler_split(members, W)
            if cut is None:
                continue
            left, right = cut
            trial = labels.copy()
            newid = labels.max() + 1
            for j in right:
                trial[j] = newid
            t = cost(_relabel(trial), W, theta=theta)
            if t < best - 1e-9:
                labels, best, improved, moves = _relabel(trial), t, True, moves + 1
                break
        if improved:
            continue
        # ----- merge -----
        clusters = sorted(set(labels.tolist()))
        cmemb = {c: np.where(labels == c)[0] for c in clusters}
        # only try the most-affine cluster pairs
        pairs = []
        for ci in range(len(clusters)):
            for cj in range(ci + 1, len(clusters)):
                a, b = cmemb[clusters[ci]], cmemb[clusters[cj]]
                pairs.append((W[np.ix_(a, b)].mean(), clusters[ci], clusters[cj]))
        pairs.sort(reverse=True)
        for _, ci, cj in pairs[:20]:
            trial = labels.copy()
            trial[trial == cj] = ci
            t = cost(_relabel(trial), W, theta=theta)
            if t < best - 1e-9:
                labels, best, improved, moves = _relabel(trial), t, True, moves + 1
                break
        if improved:
            continue
        # ----- move a poorly-fit record to its most-affine cluster (or singleton) -----
        clusters = sorted(set(labels.tolist()))
        cmemb = {c: np.where(labels == c)[0] for c in clusters}
        for i in range(len(labels)):
            ci = labels[i]
            best_c, best_a = ci, -1.0
            for c in clusters:
                others = cmemb[c][cmemb[c] != i]
                a = W[i, others].mean() if len(others) else 0.0
                if a > best_a:
                    best_a, best_c = a, c
            if best_c != ci:
                trial = labels.copy()
                trial[i] = best_c
                t = cost(_relabel(trial), W, theta=theta)
                if t < best - 1e-9:
                    labels, best, improved, moves = _relabel(trial), t, True, moves + 1
                    break
        if not improved:
            break
    return labels, best, moves


def _relabel(labels) -> np.ndarray:
    labels = np.asarray(labels)
    remap = {c: k for k, c in enumerate(sorted(set(labels.tolist())))}
    return np.array([remap[c] for c in labels.tolist()])


# --------------------------------------------------------------------------- #
def _f1(labels, gold_labels) -> float:
    return pairwise_f1(np.asarray(labels).tolist(), list(gold_labels))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", choices=["febrl3", "dblp-acm", "synth-conflict"],
                    default="febrl3")
    ap.add_argument("--bridge-rate", type=float, default=0.6,
                    help="synth-conflict: prob a bridged entity's record uses the placeholder")
    ap.add_argument("--datasets-dir", type=Path, default=Path("datasets"))
    ap.add_argument("--max-entities", type=int, default=80)
    ap.add_argument("--init-thresh", type=float, default=0.5)
    ap.add_argument("--theta", type=float, default=None,
                    help="correlation-clustering threshold; default = median nonzero affinity")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if args.dataset == "synth-conflict":
        import random
        records, gold = simulate_conflict(args.max_entities, random.Random(args.seed),
                                          bridge_rate=args.bridge_rate)
    else:
        real = _load_real(args.dataset, args.datasets_dir, args.max_entities, args.seed)
        if real is None:
            print(f"  [{args.dataset}] unavailable — install recordlinkage / fetch DBLP-ACM.")
            return 0
        records, gold = real
    gold = np.array(gold)
    W = build_affinity(records)
    n = len(records)

    theta = args.theta if args.theta is not None else default_theta(W)
    init = _connected_components(W, args.init_thresh)
    init_f1 = _f1(init, gold)

    land_lab, land_cost, land_moves = landscape_loop(W, init, theta=theta)
    disc_lab, disc_cost, disc_moves = discrete_loop(W, init, theta=theta)
    gold_cost = cost(_relabel(gold), W, theta=theta)

    def k(lab):
        return len(set(np.asarray(lab).tolist()))

    print(f"\n  dataset={args.dataset}  N={n}  true_entities={k(gold)}\n")
    print(f"  {'method':<22} {'F1':>7} {'clusters':>9} {'cost(bits)':>11} {'moves':>6}")
    print(f"  {'-'*22} {'-'*7} {'-'*9} {'-'*11} {'-'*6}")
    cc_theta = _connected_components(W, theta)
    print(f"  {'CC @ theta (no struct)':<22} {_f1(cc_theta, gold):>7.3f} {k(cc_theta):>9} {cost(_relabel(cc_theta), W, theta=theta):>11.0f} {'-':>6}")
    print(f"  {'init (CC@thresh)':<22} {init_f1:>7.3f} {k(init):>9} {cost(_relabel(init), W, theta=theta):>11.0f} {'-':>6}")
    print(f"  {'discrete split/merge':<22} {_f1(disc_lab, gold):>7.3f} {k(disc_lab):>9} {disc_cost:>11.0f} {disc_moves:>6}")
    print(f"  {'LANDSCAPE sculpting':<22} {_f1(land_lab, gold):>7.3f} {k(land_lab):>9} {land_cost:>11.0f} {land_moves:>6}")
    print(f"  {'gold (reference)':<22} {1.0:>7.3f} {k(gold):>9} {gold_cost:>11.0f} {'-':>6}")

    f_land, f_disc = _f1(land_lab, gold), _f1(disc_lab, gold)
    print("\n  KILL-CRITERION:")
    if abs(f_land - f_disc) < 0.01:
        verdict = ("MECHANISM IS COSMETIC — landscape ties the discrete loop "
                   "(same objective, same partition). Drop it.")
    elif f_land > f_disc:
        verdict = (f"landscape BEATS discrete by {f_land - f_disc:+.3f} F1 — the "
                   "global re-flow / ridge mechanism earns its keep. Worth pursuing.")
    else:
        verdict = (f"landscape LOSES to discrete by {f_land - f_disc:+.3f} F1 — the "
                   "mechanism hurts. Drop it.")
    print(f"   {verdict}")
    print(f"   (both optimise the SAME cost ledger; lower bits = better-optimised, "
          f"not necessarily higher F1.)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
