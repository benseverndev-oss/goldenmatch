"""Step 3 — Bayesian optimal experimental design over the ER partition posterior.

Framing #6 in the 2026-06-07 white-space scan; design note
`docs/superpowers/specs/2026-06-07-amortized-bayesian-er-1plus3plus6-design.md`.
Builds directly on step 2 (`amortized_partition_er.py`): it draws a Monte-Carlo
posterior over partitions from the trained head and runs active labelling on top.

THE WHITE SPACE (from the scan): active-ER selects pairs by INDIVIDUAL-PAIR
uncertainty (ALIAS KDD'02 -> DIAL PVLDB'22). ITACC (ICDM'25) does info-gain over
the whole clustering with transitivity, but on a non-Bayesian Gibbs energy model,
generic correlation clustering, requiring K. The open cell is BAYESIAN optimal
design over a real ER partition POSTERIOR (the step-2 head), unknown K.

THE THEORY (noiseless same-entity oracle): for a binary query A_ij = "are i and j
co-referent?", since the partition Z determines the answer,
        EIG(i,j) = I(A_ij ; Z) = H(A_ij) - H(A_ij | Z) = H(A_ij) = H_b(p_ij),
where p_ij = posterior co-clustering probability and H_b is binary entropy. So
the Bayesian-optimal single query is the pair whose co-clustering is closest to
50/50 across posterior samples. TRANSITIVITY is automatic: p_ij is computed over
whole-partition samples, and conditioning the posterior on each answer (keep
samples consistent with every accumulated constraint) collapses many induced
pairs at once — so one label resolves more than one pair.

THE EXPERIMENT: identical posterior + identical conditioning for all strategies;
only the SELECTION rule differs, isolating its value.
  * eig    — argmax H_b(p_ij) over the CURRENT (conditioned) posterior. Re-scored
             every round => transitivity-resolved pairs (p->0/1) are skipped.
  * static — argmax H_b over the INITIAL posterior, fixed order. Classic
             per-pair uncertainty sampling that ignores transitivity in SELECTION.
  * random — random unlabelled pair.
Metric: consensus-partition F1 and total posterior uncertainty vs #labels.
Claim under test: eig reaches high F1 / low uncertainty with FEWER labels.

Run (needs torch):
    python scripts/research/active_partition_er.py --train-epochs 250
"""
from __future__ import annotations

import argparse
import math
import random
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from amortized_partition_er import (  # noqa: E402
    _HAVE_TORCH,
    pairwise_f1,
    simulate,
)

if _HAVE_TORCH:
    import torch
    from amortized_partition_er import AmortizedPartition


def _hb(p: float) -> float:
    """Binary entropy in bits."""
    if p <= 0.0 or p >= 1.0:
        return 0.0
    return -(p * math.log2(p) + (1 - p) * math.log2(1 - p))


def _co_prob(samples: list[list[int]], i: int, j: int) -> float:
    m = len(samples)
    if not m:
        return 0.5
    return sum(1 for s in samples if s[i] == s[j]) / m


def gold_same(labels: list[int], i: int, j: int) -> bool:
    return labels[i] == labels[j]


class _Constraints:
    """Conditions the partition posterior on noiseless same-entity answers via
    must-link/cannot-link TRANSITIVE CLOSURE (union-find) — the exact logical
    consequence of each answer. This is where transitivity lives: one label can
    determine many induced pairs (A~B and B~C => A~C; A~B and B!~C => A!~C), so
    those pairs become certain (p in {0,1}) and a re-scoring acquisition skips
    them. No resampling needed — the model pool is drawn once and only supplies
    the prior for pairs the constraints have NOT yet determined.

    TODO(real program): also apply the soft/correlational Bayesian update (a new
    must-link should shift belief on *similar* undetermined pairs), e.g. via
    importance-reweighting or SMC over the head — omitted here for tractability.
    """

    def __init__(self, n: int):
        self.parent = list(range(n))
        self.cl: list[tuple[int, int]] = []  # cannot-link (record indices)

    def find(self, a: int) -> int:
        while self.parent[a] != a:
            self.parent[a] = self.parent[self.parent[a]]
            a = self.parent[a]
        return a

    def add(self, i: int, j: int, same: bool) -> None:
        if same:
            self.parent[self.find(i)] = self.find(j)
        else:
            self.cl.append((i, j))

    def forced(self, i: int, j: int) -> float | None:
        """1.0 if must-linked (transitively), 0.0 if cannot-linked at the
        component level, else None (undetermined -> use the model prior)."""
        ri, rj = self.find(i), self.find(j)
        if ri == rj:
            return 1.0
        for x, y in self.cl:
            rx, ry = self.find(x), self.find(y)
            if {rx, ry} == {ri, rj}:
                return 0.0
        return None


def run_strategy(model, sim, strategy: str, budget: int, pool: int,
                 base: list[list[int]], prior: dict[tuple[int, int], float],
                 static_order: list[tuple[int, int]], rng: random.Random,
                 checkpoints: list[int]) -> dict[int, tuple[float, float]]:
    """Active labelling loop for one selection strategy.

    Returns {n_labels: (consensus_F1, total_uncertainty)}. All strategies share
    the SAME posterior pool + SAME (must/cannot-link) conditioning; only the
    pair-SELECTION rule differs, isolating its value.
    """
    n = sim.fields.size(0)
    labels = sim.labels
    all_pairs = list(prior.keys())
    con = _Constraints(n)
    labelled: set[tuple[int, int]] = set()
    out: dict[int, tuple[float, float]] = {}

    def p_ij(i: int, j: int) -> float:
        f = con.forced(i, j)
        return f if f is not None else prior[(i, j)]

    def total_uncertainty() -> float:
        return sum(_hb(p_ij(i, j)) for (i, j) in all_pairs if (i, j) not in labelled)

    def consensus_f1() -> float:
        # must-link closure, then add model-prior edges (p>=0.5) that aren't
        # cannot-linked; connected components -> partition.
        uf = _Constraints(n)
        uf.parent = list(con.parent)
        uf.cl = list(con.cl)
        for (i, j) in all_pairs:
            if uf.forced(i, j) is None and prior[(i, j)] >= 0.5:
                uf.parent[uf.find(i)] = uf.find(j)
        assign = [uf.find(i) for i in range(n)]
        return pairwise_f1(assign, labels)

    def record(k: int):
        out[k] = (consensus_f1(), total_uncertainty())

    if 0 in checkpoints:
        record(0)

    static_ptr = 0
    for step in range(1, budget + 1):
        if strategy == "random":
            cand = [pr for pr in all_pairs if pr not in labelled]
            pick = rng.choice(cand)
        elif strategy == "static":
            while static_order[static_ptr] in labelled:
                static_ptr += 1
            pick = static_order[static_ptr]
            static_ptr += 1
        else:  # eig — argmax H_b(p_ij) over the CURRENT (conditioned) posterior
            best, pick = -1.0, None
            for pr in all_pairs:
                if pr in labelled:
                    continue
                u = _hb(p_ij(*pr))
                if u > best:
                    best, pick = u, pr
            if pick is None:
                pick = next(pr for pr in all_pairs if pr not in labelled)

        i, j = pick
        con.add(i, j, gold_same(labels, i, j))
        labelled.add(pick)
        if step in checkpoints:
            record(step)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--train-epochs", type=int, default=250)
    ap.add_argument("--entities", type=int, default=12)
    ap.add_argument("--n-fields", type=int, default=6)
    ap.add_argument("--noise", type=float, default=0.35)
    ap.add_argument("--pool", type=int, default=160, help="posterior samples")
    ap.add_argument("--budget", type=int, default=24, help="label budget")
    ap.add_argument("--eval-datasets", type=int, default=12)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if not _HAVE_TORCH:
        print("  torch not installed — architecture-only here. `pip install torch`.")
        return 0

    torch.manual_seed(args.seed)
    rng = random.Random(args.seed)

    # ---- train the step-2 head (same recipe) ----
    model = AmortizedPartition(args.n_fields)
    opt = torch.optim.Adam(model.parameters(), lr=2e-3)
    print(f"  training amortized head ({args.train_epochs} epochs x batch 8)...")
    for ep in range(args.train_epochs):
        opt.zero_grad()
        loss = None
        for _ in range(8):
            sim = simulate(args.entities, args.n_fields, args.noise, rng)
            nll, aux = model.nll_and_aux(sim)
            term = (nll + 0.5 * aux) / 8
            loss = term if loss is None else loss + term
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        opt.step()

    # ---- active-learning comparison on held-out datasets ----
    checkpoints = sorted({0, 4, 8, 12, 16, 20, args.budget})
    strategies = ["eig", "static", "random"]
    agg = {s: {k: [0.0, 0.0] for k in checkpoints} for s in strategies}

    eval_rng = random.Random(args.seed + 12345)
    for _ in range(args.eval_datasets):
        sim = simulate(args.entities, args.n_fields, args.noise, eval_rng)
        n = sim.fields.size(0)
        # ONE shared posterior pool + prior + static ranking for all strategies.
        base = [model.sample_partition(sim, temp=1.0) for _ in range(args.pool)]
        all_pairs = [(i, j) for i in range(n) for j in range(i + 1, n)]
        prior = {pr: _co_prob(base, *pr) for pr in all_pairs}
        static_order = sorted(all_pairs, key=lambda pr: -_hb(prior[pr]))
        for s in strategies:
            res = run_strategy(model, sim, s, args.budget, args.pool, base, prior,
                               static_order, random.Random(eval_rng.randrange(1 << 30)),
                               checkpoints)
            for k, (f1, unc) in res.items():
                agg[s][k][0] += f1
                agg[s][k][1] += unc

    nd = args.eval_datasets
    print(f"\n  records/sim ~ {sim.fields.size(0)} | posterior pool={args.pool} "
          f"| {nd} eval datasets | budget={args.budget}\n")
    print("  consensus pairwise-F1 vs #labels")
    print(f"  {'#labels':>8} " + " ".join(f"{s:>9}" for s in strategies))
    for k in checkpoints:
        row = " ".join(f"{agg[s][k][0]/nd:>9.3f}" for s in strategies)
        print(f"  {k:>8} {row}")

    print("\n  total posterior uncertainty (sum H_b over unlabelled pairs) vs #labels")
    print(f"  {'#labels':>8} " + " ".join(f"{s:>9}" for s in strategies))
    for k in checkpoints:
        row = " ".join(f"{agg[s][k][1]/nd:>9.2f}" for s in strategies)
        print(f"  {k:>8} {row}")

    # label efficiency: labels for eig vs static to reach a target F1
    def labels_to(target: float, s: str) -> int | None:
        for k in checkpoints:
            if agg[s][k][0] / nd >= target:
                return k
        return None

    target = 0.90 * max(agg["eig"][checkpoints[-1]][0] / nd, 1e-9)
    le_eig, le_static = labels_to(target, "eig"), labels_to(target, "static")
    print(f"\n  labels to reach 90% of eig's final F1 (={target:.3f}): "
          f"eig={le_eig}  static={le_static}  random={labels_to(target, 'random')}")

    eig_final = agg["eig"][checkpoints[-1]][0] / nd
    stat_final = agg["static"][checkpoints[-1]][0] / nd
    eig_unc = agg["eig"][checkpoints[-1]][1] / nd
    stat_unc = agg["static"][checkpoints[-1]][1] / nd
    wins_f1 = eig_final >= stat_final
    wins_unc = eig_unc <= stat_unc
    wins_eff = (le_eig is not None and (le_static is None or le_eig <= le_static))
    print("\n  STEP-3 GATE:")
    print(f"   - partition-EIG >= per-pair on final F1     : {'YES' if wins_f1 else 'NO'}")
    print(f"   - partition-EIG collapses uncertainty faster : {'YES' if wins_unc else 'NO'}")
    print(f"   - partition-EIG reaches target with <= labels: {'YES' if wins_eff else 'NO'}")
    ok = sum([wins_f1, wins_unc, wins_eff]) >= 2
    print(f"\n  RESULT: {'PASS' if ok else 'PARTIAL'} — partition-aware EIG "
          f"{'beats per-pair uncertainty at equal budget' if ok else 'did not clearly beat per-pair; tune pool/budget/training'}.\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
