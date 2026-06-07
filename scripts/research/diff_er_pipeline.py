"""Prototype #4 — jointly differentiable blocking + matching + clustering.

Backs `docs/superpowers/specs/2026-06-07-amortized-bayesian-er-1plus3plus6-design.md`
(framing #4 in the 2026-06-07 white-space scan).

THE GAP WE'RE PROBING
---------------------
Every "joint"/"end-to-end" ER system the scan found either (a) shares an encoder
but trains blocker and matcher with separate objectives (Sudowoodo, DIAL), or
(b) couples them via iterative pseudo-labels (Co-Learning, PVLDB'24; MutualER,
CIKM'24), or (c) chains independently-trained stages. NONE backprops a single
GLOBAL clustering loss through a differentiable candidate-selection step. Yet
all the primitives exist, just never assembled for ER:

  * differentiable top-k via optimal transport      (SOFT, NeurIPS 2020)
  * differentiable clustering layers                (Stewart et al., NeurIPS 2023)
  * relaxed global clustering metric as the loss     (Le & Titov, CoNLL 2017 — coref)

This file assembles a minimal version of exactly that:

    records --[encoder]--> embeddings
            --[SOFT blocker: differentiable top-k gate g_ij in [0,1]]-->
            --[matcher: pair MLP -> m_ij in [0,1]]-->
            --[soft same-cluster S_ij = g_ij * m_ij]-->
            --[relaxed pairwise-F1 loss vs gold + blocker budget penalty]

The point: because the blocker gate `g_ij` multiplies into `S_ij`, dropping a
true pair at blocking directly costs soft-RECALL in the global loss, so the
gradient pushes the blocker to RETAIN pairs the matcher rewards — the coupling
that the two-stage pipeline cannot express. A budget penalty creates the
opposing pressure (don't keep everything), so the blocker must learn *which*
pairs to keep. The recall-vs-budget tension is the whole experiment.

STATUS: architecture skeleton + synthetic-data sanity demo. Not tuned, not a
benchmark result. Real next steps (TODOs inline): swap the softmax-relaxed
top-k for true entropic-OT SOFT top-k; swap the pairwise-F1 surrogate for a
relaxed B^3 with a differentiable transitive-closure head; encode with the
goldenmatch LM bi-encoder instead of hashed char-trigrams.

Run (needs torch):
    python scripts/research/diff_er_pipeline.py --epochs 150
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
except Exception:  # pragma: no cover - torch optional in some envs
    _HAVE_TORCH = False


# --------------------------------------------------------------------------- #
# Synthetic ER data: latent entities -> corrupted records (so we have gold).
# --------------------------------------------------------------------------- #
@dataclass
class SynthER:
    records: list[str]
    entity_of: list[int]  # gold entity id per record

    def gold_pair_matrix(self):  # [N, N] in {0,1}, same-entity, no diagonal
        n = len(self.records)
        y = torch.zeros(n, n)
        for i in range(n):
            for j in range(n):
                if i != j and self.entity_of[i] == self.entity_of[j]:
                    y[i, j] = 1.0
        return y


def make_synth(n_entities: int, dups_max: int, seed: int) -> SynthER:
    rng = random.Random(seed)
    tokens = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot",
              "golf", "hotel", "india", "juliet", "kilo", "lima"]
    records: list[str] = []
    entity_of: list[int] = []
    for e in range(n_entities):
        base = f"{rng.choice(tokens)} {rng.choice(tokens)} {rng.randint(1000, 9999)}"
        for _ in range(rng.randint(1, dups_max)):
            s = list(base)
            # corrupt: a few char edits (typos)
            for _ in range(rng.randint(0, 3)):
                if not s:
                    break
                p = rng.randrange(len(s))
                op = rng.random()
                if op < 0.34:
                    s[p] = rng.choice("abcdefghijklmnopqrstuvwxyz0123456789 ")
                elif op < 0.67:
                    s.pop(p)
                else:
                    s.insert(p, rng.choice("abcdefghijklmnopqrstuvwxyz"))
            records.append("".join(s))
            entity_of.append(e)
    # shuffle so co-referent records are not adjacent
    idx = list(range(len(records)))
    rng.shuffle(idx)
    return SynthER([records[i] for i in idx], [entity_of[i] for i in idx])


def char_trigram_features(records: list[str], dim: int = 256):
    """Hashed char-trigram bag-of-features [N, dim] (cheap, dependency-free)."""
    x = torch.zeros(len(records), dim)
    for i, r in enumerate(records):
        s = f"  {r}  "
        for k in range(len(s) - 2):
            h = hash(s[k:k + 3]) % dim
            x[i, h] += 1.0
    return F.normalize(x, dim=1)


# --------------------------------------------------------------------------- #
# Model: encoder + SOFT blocker + matcher.
# --------------------------------------------------------------------------- #
if _HAVE_TORCH:

    class DiffER(nn.Module):
        def __init__(self, in_dim: int, emb_dim: int = 64, top_k: int = 4,
                     tau: float = 0.3):
            super().__init__()
            self.encoder = nn.Sequential(
                nn.Linear(in_dim, 128), nn.ReLU(), nn.Linear(128, emb_dim)
            )
            # pair matcher over [|a-b|, a*b]
            self.matcher = nn.Sequential(
                nn.Linear(2 * emb_dim, 64), nn.ReLU(), nn.Linear(64, 1)
            )
            self.top_k = top_k
            self.tau = tau  # blocker relaxation temperature

        def soft_block(self, z):
            """Differentiable top-k blocker gate g_ij in [0,1].

            v0 relaxation: per-row softmax over candidate affinities, scaled by
            the row's softmax mass on its top-k. This is a smooth stand-in for
            entropic-OT SOFT top-k (NeurIPS 2020) — gradients flow into `z`, so
            the encoder learns a blocking-friendly geometry.
            TODO: replace with true OT top-k for a sharper, budget-calibrated gate.
            """
            sim = z @ z.t()                      # [N, N] affinity
            n = sim.size(0)
            sim = sim - torch.eye(n, device=sim.device) * 1e9  # mask self
            # soft per-row selection weights
            w = torch.softmax(sim / self.tau, dim=1)
            # encourage ~top_k mass: gate = min(1, k * w) (smooth, keeps grad)
            g = torch.clamp(self.top_k * w, max=1.0)
            return g, sim

        def match(self, z):
            n = z.size(0)
            a = z.unsqueeze(1).expand(n, n, -1)
            b = z.unsqueeze(0).expand(n, n, -1)
            feat = torch.cat([(a - b).abs(), a * b], dim=-1)
            return torch.sigmoid(self.matcher(feat).squeeze(-1))  # [N, N]

        def forward(self, x):
            z = F.normalize(self.encoder(x), dim=1)
            g, sim = self.soft_block(z)
            m = self.match(z)
            s = g * m                            # soft same-cluster prob
            return s, g, m


# --------------------------------------------------------------------------- #
# Relaxed GLOBAL loss: soft pairwise-F1 + blocker budget penalty.
# --------------------------------------------------------------------------- #
def soft_f1_loss(s, y, eps: float = 1e-6):
    """1 - soft pairwise F1. `s`,`y` are [N,N]; we use the off-diagonal upper
    triangle so each pair counts once.

    soft_TP = sum_{y=1} s ; soft_FP = sum_{y=0} s ; soft_FN = sum_{y=1} (1-s)
    This is a differentiable relaxation of the global clustering metric — the
    Le & Titov (CoNLL 2017) trick, here on pairwise-F1.
    TODO: upgrade to relaxed B^3 over a differentiable transitive-closure head
    so the loss rewards whole-cluster coherence, not just independent pairs.
    """
    n = s.size(0)
    triu = torch.triu(torch.ones(n, n, device=s.device), diagonal=1).bool()
    sp, yp = s[triu], y[triu]
    tp = (sp * yp).sum()
    fp = (sp * (1 - yp)).sum()
    fn = ((1 - sp) * yp).sum()
    precision = tp / (tp + fp + eps)
    recall = tp / (tp + fn + eps)
    f1 = 2 * precision * recall / (precision + recall + eps)
    return 1.0 - f1, precision.item(), recall.item()


def blocker_recall(g, y, thresh: float = 0.5):
    """Fraction of TRUE pairs the blocker keeps (gate above thresh). This is the
    metric the two-stage pipeline silently caps; we watch it rise as training
    couples the blocker to the global loss."""
    n = g.size(0)
    triu = torch.triu(torch.ones(n, n, device=g.device), diagonal=1).bool()
    gp, yp = g[triu], y[triu]
    kept = ((gp > thresh) & (yp > 0.5)).sum().item()
    total = (yp > 0.5).sum().item()
    return kept / total if total else 0.0


# --------------------------------------------------------------------------- #
# Train.
# --------------------------------------------------------------------------- #
def train(epochs: int, seed: int, budget: float) -> int:
    if not _HAVE_TORCH:
        print("  torch not installed — prototype is architecture-only here. "
              "Install torch to run the synthetic training demo.")
        return 0

    torch.manual_seed(seed)
    data = make_synth(n_entities=20, dups_max=4, seed=seed)
    x = char_trigram_features(data.records)
    y = data.gold_pair_matrix()
    model = DiffER(in_dim=x.size(1))
    opt = torch.optim.Adam(model.parameters(), lr=5e-3)

    print(f"  records={len(data.records)} true_pairs={int(y.sum() / 2)} "
          f"budget_lambda={budget}\n")
    print(f"  {'epoch':>5} {'loss':>7} {'soft-P':>7} {'soft-R':>7} "
          f"{'blk-recall':>10} {'gate-mass':>9}")
    for ep in range(epochs):
        opt.zero_grad()
        s, g, m = model(x)
        f1_loss, p, r = soft_f1_loss(s, y)
        # budget penalty: average off-diagonal gate mass (pushes blocker sparse)
        n = g.size(0)
        gate_mass = g.sum() / (n * (n - 1))
        loss = f1_loss + budget * gate_mass
        loss.backward()
        opt.step()

        if ep % max(1, epochs // 10) == 0 or ep == epochs - 1:
            br = blocker_recall(g.detach(), y)
            print(f"  {ep:>5} {loss.item():>7.4f} {p:>7.4f} {r:>7.4f} "
                  f"{br:>10.3f} {gate_mass.item():>9.4f}")

    final_br = blocker_recall(model(x)[1].detach(), y)
    print(f"\n  final blocker recall = {final_br:.3f}")
    print("  (Sanity, not a benchmark: the demo passes if blocker recall climbs"
          " under the global loss\n   while gate mass stays bounded — i.e. the"
          " blocker learns to keep TRUE pairs, not all pairs.)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--epochs", type=int, default=150)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--budget", type=float, default=0.5,
                    help="blocker sparsity penalty weight (recall-vs-budget tension)")
    args = ap.parse_args()
    return train(args.epochs, args.seed, args.budget)


if __name__ == "__main__":
    raise SystemExit(main())
