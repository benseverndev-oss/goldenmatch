"""Step 2 — amortized neural posterior over the ER partition (framing #1).

Backs `docs/superpowers/specs/2026-06-07-amortized-bayesian-er-1plus3plus6-design.md`.
Follows the step-1 result (`RESULTS-3-reconstructability.md`), which cleared the
likelihood-viability gate and forced two design amendments that this prototype
implements:

  (a) LEARN the reconstructor, don't fix a similarity kernel. -> the record
      encoder is trained, and a masked-field reconstruction auxiliary
      (the #3 signal) shapes it so cluster-mates are mutually predictive.
  (b) LEARN the microclustering prior, don't use a size penalty. -> the
      "open a new cluster" logit is a learned function of context, trained on
      simulated data whose cluster-size distribution is microclustering-shaped
      (mostly singletons + small clusters). The net learns *when* to open a
      cluster instead of being told via a fixed size^2 term.

ARCHITECTURE (Neural Clustering Process family: Pakman et al., ICML 2020;
Deep Amortized Clustering, Lee et al. 2019 — adapted to ER + microclustering):

  records --[encoder]--> e_i
  process records in random order; maintain a running pooled sum per cluster.
  For record i, score joining each existing cluster k OR a NEW cluster, given
  the remaining-unassigned context U:
        logit(k) = score( phi(pool_k + e_i),  U,  e_i )
        logit(new) = score( phi(e_i),         U,  e_i )
        p = softmax over [existing..., new]
  Train by NLL of the GOLD assignment sequence (teacher-forced). One forward
  pass yields q(partition | records): no per-dataset MCMC. Amortized ACROSS
  datasets (the unclaimed cell vs. blink/d-blink MCMC and the 2025 VI paper).

WHAT WE MEASURE (the step-2 gate):
  * partition recovery: pairwise-F1 of greedy decode vs gold on held-out sims,
    against a connected-components-of-thresholded-distance baseline.
  * calibration (ECE): does the assignment confidence match its accuracy? — the
    whole point of a *posterior* (this is the "tells you when it's unsure" story).
  * learned prior: does the net's new-cluster rate track the true simulated
    cluster-size distribution, with NO size penalty supplied?

STATUS: runnable prototype on SIMULATED vector-records. TODOs for the real
program: swap the toy continuous-field simulator for a learned string/LM
encoder over real schemas; add posterior SAMPLING (not just greedy) for full
uncertainty; compare to d-blink on a small real set where MCMC is still feasible.

Run (needs torch):
    python scripts/research/amortized_partition_er.py --epochs 400
"""
from __future__ import annotations

import argparse
import random
from dataclasses import dataclass

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

    _HAVE_TORCH = True
except Exception:  # pragma: no cover
    _HAVE_TORCH = False


# --------------------------------------------------------------------------- #
# Simulator: latent entities -> corrupted multi-field records, with a
# microclustering-shaped cluster-size distribution (mostly singletons).
# --------------------------------------------------------------------------- #
@dataclass
class SimDataset:
    fields: "torch.Tensor"   # [N, F] record field vectors
    labels: list[int]        # gold entity id per record (len N)


def _sample_cluster_sizes(n_entities: int, rng: random.Random) -> list[int]:
    """Microclustering-shaped sizes: many 1s, some 2-3, rare larger. Sublinear
    growth of entity count in record count (Betancourt et al., NeurIPS 2016)."""
    sizes = []
    for _ in range(n_entities):
        r = rng.random()
        if r < 0.55:
            sizes.append(1)
        elif r < 0.85:
            sizes.append(2)
        elif r < 0.96:
            sizes.append(3)
        else:
            sizes.append(rng.randint(4, 6))
    return sizes


def simulate(n_entities: int, n_fields: int, noise: float,
             rng: random.Random) -> SimDataset:
    g = torch.Generator().manual_seed(rng.randrange(1 << 30))
    sizes = _sample_cluster_sizes(n_entities, rng)
    rows, labels = [], []
    for eid, sz in enumerate(sizes):
        center = torch.randn(n_fields, generator=g)
        for _ in range(sz):
            rec = center + noise * torch.randn(n_fields, generator=g)
            # occasionally corrupt a field hard (mimic missing/typo)
            if rng.random() < 0.15:
                rec[rng.randrange(n_fields)] = torch.randn(1, generator=g).item()
            rows.append(rec)
            labels.append(eid)
    fields = torch.stack(rows)
    perm = torch.randperm(len(labels), generator=g)
    return SimDataset(fields[perm], [labels[i] for i in perm.tolist()])


# --------------------------------------------------------------------------- #
# Model.
# --------------------------------------------------------------------------- #
if _HAVE_TORCH:

    def _mlp(i: int, h: int, o: int) -> nn.Module:
        return nn.Sequential(nn.Linear(i, h), nn.ReLU(), nn.Linear(h, o))

    class AmortizedPartition(nn.Module):
        def __init__(self, n_fields: int, d: int = 48):
            super().__init__()
            self.encoder = _mlp(n_fields, 96, d)
            self.ctx = _mlp(d, 96, d)               # remaining-unassigned context
            # score over [e_i, m_k, e_i-m_k, e_i*m_k, U] (5d) + [log1p(size), is_new]
            self.score = _mlp(5 * d + 2, 128, 1)
            self.empty = nn.Parameter(torch.zeros(d))  # learned empty-cluster prototype
            # masked-field reconstruction head (#3 signal): predict a field from
            # the gold cluster-mate context. Shapes the encoder.
            self.recon = _mlp(d + n_fields, 96, 1)
            self.d = d

        def encode(self, fields):
            return self.encoder(fields)  # [N, d]

        def _logits(self, ei, means, counts, U):
            """Logits over [existing clusters..., NEW] for assigning record ei.

            Uses cluster MEAN + explicit interaction features (difference and
            product with e_i) so proximity is a direct, easy-to-learn signal —
            sum-pooling made this scale-sensitive and the model collapsed to
            always-open-new. The NEW option uses a learned empty prototype, so
            'open a cluster' is comparable to 'join' and the net LEARNS the
            microclustering rate rather than being handed a size penalty.
            """
            K = means.size(0) if means is not None and means.numel() else 0
            d = self.d
            ei_b = ei.unsqueeze(0)
            rows = []
            if K:
                eb = ei_b.expand(K, d)
                feat = torch.cat([eb, means, eb - means, eb * means], -1)  # [K,4d]
                logsz = torch.log1p(counts.float()).unsqueeze(-1)          # [K,1]
                isnew = torch.zeros(K, 1)
                rows.append(torch.cat([feat, logsz, isnew], -1))           # [K,4d+2]
            m_new = self.empty.unsqueeze(0)
            feat_new = torch.cat([ei_b, m_new, ei_b - m_new, ei_b * m_new], -1)
            new_row = torch.cat([feat_new, torch.zeros(1, 1), torch.ones(1, 1)], -1)
            rows.append(new_row)                                           # [1,4d+2]
            feats = torch.cat(rows, 0)                                     # [K+1,4d+2]
            U_b = U.unsqueeze(0).expand(feats.size(0), d)
            return self.score(torch.cat([feats, U_b], -1)).squeeze(-1)     # [K+1]

        def nll_and_aux(self, sim: "SimDataset"):
            """Teacher-forced NLL of the gold partition + masked-field recon aux."""
            e = self.encode(sim.fields)                  # [N, d]
            n = e.size(0)
            order = torch.randperm(n)
            sums: list[torch.Tensor] = []                # running sum of e per cluster
            counts: list[int] = []
            gold_to_idx: dict[int, int] = {}
            total_after = e[order].flip(0).cumsum(0).flip(0)  # suffix sums
            nll = e.new_zeros(())
            for step, ridx in enumerate(order.tolist()):
                ei = e[ridx]
                U = self.ctx(total_after[step] - ei)     # remaining after current
                means = (torch.stack([s / c for s, c in zip(sums, counts)])
                         if sums else e.new_zeros(0, self.d))
                cnt = torch.tensor(counts) if counts else torch.zeros(0)
                logits = self._logits(ei, means, cnt, U)
                logp = F.log_softmax(logits, 0)
                y = sim.labels[ridx]
                choice = gold_to_idx[y] if y in gold_to_idx else len(sums)
                nll = nll - logp[choice]
                if y in gold_to_idx:
                    sums[choice] = sums[choice] + ei
                    counts[choice] += 1
                else:
                    gold_to_idx[y] = len(sums)
                    sums.append(ei.clone())
                    counts.append(1)

            # masked-field reconstruction aux over gold clusters
            aux = e.new_zeros(())
            naux = 0
            by_entity: dict[int, list[int]] = {}
            for i, y in enumerate(sim.labels):
                by_entity.setdefault(y, []).append(i)
            for members in by_entity.values():
                if len(members) < 2:
                    continue
                for i in members:
                    mates = [m for m in members if m != i]
                    ctx_e = e[mates].mean(0)             # cluster-mate context
                    f = random.randrange(sim.fields.size(1))
                    target = sim.fields[i, f]
                    onehot = torch.zeros(sim.fields.size(1))
                    onehot[f] = 1.0
                    pred = self.recon(torch.cat([ctx_e, onehot])).squeeze(-1)
                    aux = aux + (pred - target) ** 2
                    naux += 1
            aux = aux / naux if naux else aux
            return nll / n, aux

        @torch.no_grad()
        def decode(self, sim: "SimDataset"):
            """Greedy MAP-ish partition + per-step confidence (for calibration)."""
            e = self.encode(sim.fields)
            n = e.size(0)
            order = list(range(n))
            total = e.sum(0)
            sums: list[torch.Tensor] = []
            counts: list[int] = []
            assign = [0] * n
            seen_sum = e.new_zeros(self.d)
            confidences, corrects = [], []
            # which model-cluster indices each gold entity's members landed in
            entity_clusters: dict[int, set[int]] = {}
            for ridx in order:
                ei = e[ridx]
                U = self.ctx(total - seen_sum - ei)
                means = (torch.stack([s / c for s, c in zip(sums, counts)])
                         if sums else e.new_zeros(0, self.d))
                cnt = torch.tensor(counts) if counts else torch.zeros(0)
                logits = self._logits(ei, means, cnt, U)
                p = torch.softmax(logits, 0)
                choice = int(torch.argmax(p))
                confidences.append(float(p[choice]))
                # is the decision right? gold says NEW if this entity is unseen,
                # else JOIN any model-cluster already holding a co-referent.
                y = sim.labels[ridx]
                new_slot = len(sums)
                if y not in entity_clusters:
                    correct = 1.0 if choice == new_slot else 0.0
                else:
                    correct = 1.0 if choice in entity_clusters[y] else 0.0
                corrects.append(correct)
                # apply the model's choice and record it for this entity
                if choice < len(sums):
                    sums[choice] = sums[choice] + ei
                    counts[choice] += 1
                else:
                    sums.append(ei.clone())
                    counts.append(1)
                entity_clusters.setdefault(y, set()).add(choice)
                assign[ridx] = choice
                seen_sum = seen_sum + ei
            return assign, confidences, corrects

        @torch.no_grad()
        def sample_partition(self, sim: "SimDataset", temp: float = 1.0) -> list[int]:
            """Draw ONE partition sample from q(partition | X): sequential
            assignment, sampling each step from softmax(logits / temp) instead of
            argmax. A pool of these is the Monte-Carlo posterior used by step 3.
            Returns a cluster id per record (model cluster indices)."""
            e = self.encode(sim.fields)
            n = e.size(0)
            order = torch.randperm(n).tolist()
            total = e.sum(0)
            sums: list[torch.Tensor] = []
            counts: list[int] = []
            assign = [0] * n
            seen = e.new_zeros(self.d)
            for ridx in order:
                ei = e[ridx]
                U = self.ctx(total - seen - ei)
                means = (torch.stack([s / c for s, c in zip(sums, counts)])
                         if sums else e.new_zeros(0, self.d))
                cnt = torch.tensor(counts) if counts else torch.zeros(0)
                logits = self._logits(ei, means, cnt, U)
                p = torch.softmax(logits / temp, 0)
                choice = int(torch.multinomial(p, 1))
                if choice < len(sums):
                    sums[choice] = sums[choice] + ei
                    counts[choice] += 1
                else:
                    sums.append(ei.clone())
                    counts.append(1)
                assign[ridx] = choice
                seen = seen + ei
            return assign


# --------------------------------------------------------------------------- #
# Metrics.
# --------------------------------------------------------------------------- #
def pairwise_f1(assign: list[int], labels: list[int]) -> float:
    def pairs(lab):
        s = set()
        idx: dict[int, list[int]] = {}
        for i, v in enumerate(lab):
            idx.setdefault(v, []).append(i)
        for members in idx.values():
            for a in range(len(members)):
                for b in range(a + 1, len(members)):
                    s.add((members[a], members[b]))
        return s
    pred, gold = pairs(assign), pairs(labels)
    tp = len(pred & gold)
    fp = len(pred - gold)
    fn = len(gold - pred)
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    return (2 * p * r / (p + r)) if (p + r) else 0.0


def cc_threshold_baseline(sim: "SimDataset", thresh: float) -> list[int]:
    """Connected components of records within L2 `thresh` — the classic
    threshold+transitive-closure ER baseline."""
    x = sim.fields
    n = x.size(0)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    d = torch.cdist(x, x)
    for i in range(n):
        for j in range(i + 1, n):
            if d[i, j] < thresh:
                parent[find(i)] = find(j)
    return [find(i) for i in range(n)]


def ece(confidences: list[float], corrects: list[float], bins: int = 10) -> float:
    """Expected calibration error of the per-step assignment decision."""
    if not confidences:
        return 0.0
    tot = len(confidences)
    e = 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [k for k, c in enumerate(confidences) if (lo < c <= hi) or (b == 0 and c <= hi)]
        if not idx:
            continue
        conf = sum(confidences[k] for k in idx) / len(idx)
        acc = sum(corrects[k] for k in idx) / len(idx)
        e += (len(idx) / tot) * abs(conf - acc)
    return e


# --------------------------------------------------------------------------- #
# Train + evaluate.
# --------------------------------------------------------------------------- #
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--epochs", type=int, default=400)
    ap.add_argument("--n-fields", type=int, default=6)
    ap.add_argument("--entities", type=int, default=14, help="entities per sim dataset")
    ap.add_argument("--noise", type=float, default=0.35)
    ap.add_argument("--aux-weight", type=float, default=0.5)
    ap.add_argument("--batch", type=int, default=8, help="sims per optimizer step")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if not _HAVE_TORCH:
        print("  torch not installed — architecture-only here. "
              "`pip install torch` to run the simulated training.")
        return 0

    torch.manual_seed(args.seed)
    rng = random.Random(args.seed)
    model = AmortizedPartition(args.n_fields)
    opt = torch.optim.Adam(model.parameters(), lr=2e-3)

    print(f"  amortized partition head | fields={args.n_fields} "
          f"entities/sim~{args.entities} noise={args.noise} aux={args.aux_weight}\n")
    print(f"  {'epoch':>5} {'nll':>7} {'aux':>7}  (batch={args.batch} sims/step)")
    for ep in range(args.epochs):
        opt.zero_grad()
        nll_acc = aux_acc = 0.0
        loss = None
        for _ in range(args.batch):
            sim = simulate(args.entities, args.n_fields, args.noise, rng)
            nll, aux = model.nll_and_aux(sim)
            term = (nll + args.aux_weight * aux) / args.batch
            loss = term if loss is None else loss + term
            nll_acc += nll.item() / args.batch
            aux_acc += aux.item() / args.batch
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        opt.step()
        if ep % max(1, args.epochs // 10) == 0 or ep == args.epochs - 1:
            print(f"  {ep:>5} {nll_acc:>7.3f} {aux_acc:>7.3f}")

    # ---- evaluate on fresh held-out simulated datasets ----
    eval_rng = random.Random(args.seed + 9999)
    n_eval = 60
    f1_model, f1_base, eces = [], [], []
    all_conf, all_corr = [], []
    true_newrate, pred_newrate = [], []
    for _ in range(n_eval):
        sim = simulate(args.entities, args.n_fields, args.noise, eval_rng)
        assign, conf, corr = model.decode(sim)
        f1_model.append(pairwise_f1(assign, sim.labels))
        # baseline: sweep a couple thresholds, take best (charitable to baseline)
        f1_base.append(max(pairwise_f1(cc_threshold_baseline(sim, t), sim.labels)
                           for t in (0.5, 0.8, 1.1, 1.4)))
        eces.append(ece(conf, corr))
        all_conf += conf
        all_corr += corr
        # prior check: fraction of records that open a new cluster
        true_newrate.append(len(set(sim.labels)) / len(sim.labels))
        pred_newrate.append((max(assign) + 1) / len(assign))

    def mean(v):
        return sum(v) / len(v)

    print(f"\n  --- held-out evaluation ({n_eval} simulated datasets) ---")
    print(f"  pairwise-F1  amortized head : {mean(f1_model):.4f}")
    print(f"  pairwise-F1  threshold+CC    : {mean(f1_base):.4f}  (best-of-4 thresholds)")
    print(f"  calibration ECE (assignment) : {ece(all_conf, all_corr):.4f}  "
          f"(0 = perfectly calibrated)")
    print(f"  learned prior: new-cluster rate pred={mean(pred_newrate):.3f} "
          f"vs true={mean(true_newrate):.3f}  (no size penalty supplied)")

    beats_base = mean(f1_model) > mean(f1_base)
    calibrated = ece(all_conf, all_corr) < 0.15
    prior_ok = abs(mean(pred_newrate) - mean(true_newrate)) < 0.10
    print("\n  STEP-2 GATE:")
    print(f"   - amortized head beats threshold+CC baseline : "
          f"{'YES' if beats_base else 'NO'}")
    print(f"   - posterior is calibrated (ECE < 0.15)        : "
          f"{'YES' if calibrated else 'NO'}")
    print(f"   - learned prior tracks true size dist (no penalty): "
          f"{'YES' if prior_ok else 'NO'}")
    ok = beats_base and calibrated and prior_ok
    print(f"\n  RESULT: {'PASS' if ok else 'PARTIAL'} — "
          f"{'amortized posterior is viable; next: posterior sampling + a real-schema encoder + d-blink comparison' if ok else 'see which sub-gate missed; tune aux-weight / capacity / noise'}.\n")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
