"""Pretrained-encoder transfer test — does universal geometry fix step 4?

Step 4 (`RESULTS-real-schema-encoder.md`) found that a from-scratch trigram
string encoder trained on a simulator does NOT transfer zero-shot to real ER:
it memorised the simulator's vocabulary, so real co-referents weren't close in
embedding space (REAL Febrl3 F1 0.03 vs a char+CC baseline ~0.9). The diagnosis:
the BOTTLENECK is the encoder, not the partition head.

This script tests the fix the design note proposed: use a FROZEN PRETRAINED text
encoder (sentence-transformers all-MiniLM-L6-v2) as the record featurizer so the
embedding geometry is UNIVERSAL, and amortize ONLY the partition head on top.
MiniLM already separates co-referent strings out of the box
('john smith boston' vs 'jon smith boston' cos 0.91; vs 'mary jones denver' 0.39).

THE TEST (same as step 4, only the encoder changes): freeze MiniLM, train the
partition head on EMBEDDED simulated records, evaluate ZERO-SHOT on real
Febrl3/DBLP-ACM. If F1 now reaches/clears the char+CC baseline, the
pretrained-encoder hypothesis holds and the sim-to-real gap was indeed the
from-scratch encoder.

Reuses the step-1..4 pieces (simulator, loaders, baseline, metrics). The head is
re-implemented compactly here over PRECOMPUTED embeddings (frozen encoder => no
re-embedding during training; embed each dataset once and cache).

Run (needs torch + sentence-transformers; recordlinkage for Febrl3; datasets/):
    python scripts/research/pretrained_transfer_er.py --epochs 300
"""
from __future__ import annotations

import argparse
import random
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from amortized_partition_er import _HAVE_TORCH, pairwise_f1  # noqa: E402
from real_schema_encoder import (  # noqa: E402
    _load_real,
    char_threshold_cc,
    ece,
    simulate_strings,
)

if _HAVE_TORCH:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
_EMB_DIM = 384


# --------------------------------------------------------------------------- #
# Frozen pretrained encoder (cached embeddings).
# --------------------------------------------------------------------------- #
def get_encoder():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(_MODEL, device="cpu")


def embed(encoder, records: list[list[str]]):
    """Records -> [N, 384] normalized embeddings (fields joined to one string)."""
    strs = [" ".join(f for f in r if f) for r in records]
    v = encoder.encode(strs, normalize_embeddings=True, show_progress_bar=False,
                        batch_size=64)
    return torch.tensor(v, dtype=torch.float32)


# --------------------------------------------------------------------------- #
# Partition head over PRECOMPUTED embeddings (mirrors steps 2-4; only the
# encoder upstream changed). Frozen MiniLM -> learned projection -> head.
# --------------------------------------------------------------------------- #
if _HAVE_TORCH:

    def _mlp(i, h, o):
        return nn.Sequential(nn.Linear(i, h), nn.ReLU(), nn.Linear(h, o))

    class ProjHead(nn.Module):
        def __init__(self, in_dim: int = _EMB_DIM, d: int = 48):
            super().__init__()
            self.proj = nn.Linear(in_dim, d)
            self.ctx = _mlp(d, 96, d)
            self.score = _mlp(5 * d + 2, 128, 1)
            self.empty = nn.Parameter(torch.zeros(d))
            self.recon = _mlp(d, 96, in_dim)   # mate-context -> pretrained emb (#3)
            self.d = d

        def encode(self, epre):
            return F.normalize(self.proj(epre), dim=-1)

        def _logits(self, ei, means, counts, U):
            d = self.d
            ei_b = ei.unsqueeze(0)
            rows = []
            K = means.size(0) if means.numel() else 0
            if K:
                eb = ei_b.expand(K, d)
                feat = torch.cat([eb, means, eb - means, eb * means], -1)
                logsz = torch.log1p(counts.float()).unsqueeze(-1)
                rows.append(torch.cat([feat, logsz, torch.zeros(K, 1)], -1))
            m_new = self.empty.unsqueeze(0)
            feat_new = torch.cat([ei_b, m_new, ei_b - m_new, ei_b * m_new], -1)
            rows.append(torch.cat([feat_new, torch.zeros(1, 1), torch.ones(1, 1)], -1))
            feats = torch.cat(rows, 0)
            U_b = U.unsqueeze(0).expand(feats.size(0), d)
            return self.score(torch.cat([feats, U_b], -1)).squeeze(-1)

        def nll_and_aux(self, epre, labels):
            e = self.encode(epre)
            n = e.size(0)
            order = torch.randperm(n)
            sums: list[torch.Tensor] = []
            counts: list[int] = []
            g2i: dict[int, int] = {}
            total_after = e[order].flip(0).cumsum(0).flip(0)
            nll = e.new_zeros(())
            for step, ridx in enumerate(order.tolist()):
                ei = e[ridx]
                U = self.ctx(total_after[step] - ei)
                means = (torch.stack([s / c for s, c in zip(sums, counts)])
                         if sums else e.new_zeros(0, self.d))
                cnt = torch.tensor(counts) if counts else torch.zeros(0)
                logp = F.log_softmax(self._logits(ei, means, cnt, U), 0)
                y = labels[ridx]
                choice = g2i[y] if y in g2i else len(sums)
                nll = nll - logp[choice]
                if y in g2i:
                    sums[choice] = sums[choice] + ei
                    counts[choice] += 1
                else:
                    g2i[y] = len(sums)
                    sums.append(ei.clone())
                    counts.append(1)
            aux = e.new_zeros(())
            na = 0
            by: dict[int, list[int]] = {}
            for i, y in enumerate(labels):
                by.setdefault(y, []).append(i)
            for members in by.values():
                if len(members) < 2:
                    continue
                for i in members:
                    mates = [m for m in members if m != i]
                    aux = aux + F.mse_loss(self.recon(e[mates].mean(0)),
                                           epre[i].detach())
                    na += 1
            aux = aux / na if na else aux
            return nll / n, aux

        @torch.no_grad()
        def decode(self, epre, labels):
            e = self.encode(epre)
            n = e.size(0)
            total = e.sum(0)
            sums: list[torch.Tensor] = []
            counts: list[int] = []
            assign = [0] * n
            seen = e.new_zeros(self.d)
            conf, corr = [], []
            ec: dict[int, set[int]] = {}
            for ridx in range(n):
                ei = e[ridx]
                U = self.ctx(total - seen - ei)
                means = (torch.stack([s / c for s, c in zip(sums, counts)])
                         if sums else e.new_zeros(0, self.d))
                cnt = torch.tensor(counts) if counts else torch.zeros(0)
                p = torch.softmax(self._logits(ei, means, cnt, U), 0)
                choice = int(torch.argmax(p))
                conf.append(float(p[choice]))
                y = labels[ridx]
                if y not in ec:
                    corr.append(1.0 if choice == len(sums) else 0.0)
                else:
                    corr.append(1.0 if choice in ec[y] else 0.0)
                if choice < len(sums):
                    sums[choice] = sums[choice] + ei
                    counts[choice] += 1
                else:
                    sums.append(ei.clone())
                    counts.append(1)
                ec.setdefault(y, set()).add(choice)
                assign[ridx] = choice
                seen = seen + ei
            return assign, conf, corr


def _report(tag, model, records, labels, epre):
    assign, conf, corr = model.decode(epre, labels)
    f1 = pairwise_f1(assign, labels)
    base = max(pairwise_f1(char_threshold_cc(records, t), labels)
               for t in (0.55, 0.7, 0.82, 0.9))
    n_ent = len(set(labels))
    print(f"  {tag:<22} N={len(records):>4} ent={n_ent:>3} | "
          f"F1(amortized)={f1:.3f}  F1(char+CC best4)={base:.3f}  "
          f"ECE={ece(conf, corr):.3f}  clusters: pred={len(set(assign))} true={n_ent}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--epochs", type=int, default=300)
    ap.add_argument("--entities", type=int, default=12)
    ap.add_argument("--train-pool", type=int, default=160, help="cached sim datasets")
    ap.add_argument("--max-real-entities", type=int, default=60)
    ap.add_argument("--datasets-dir", type=Path, default=Path("datasets"))
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if not _HAVE_TORCH:
        print("  torch not installed — architecture-only here.")
        return 0
    try:
        import sentence_transformers  # noqa: F401
    except Exception:
        print("  sentence-transformers not installed — `pip install sentence-transformers`.")
        return 0

    torch.manual_seed(args.seed)
    rng = random.Random(args.seed)
    print(f"  loading frozen pretrained encoder ({_MODEL})...")
    encoder = get_encoder()

    # pre-generate + embed a pool of simulated datasets ONCE (encoder is frozen)
    print(f"  embedding {args.train_pool} simulated datasets (cached)...")
    pool = []
    for _ in range(args.train_pool):
        recs, labs = simulate_strings(args.entities, rng)
        pool.append((embed(encoder, recs), labs))

    model = ProjHead()
    opt = torch.optim.Adam(model.parameters(), lr=2e-3)
    print(f"  training head only ({args.epochs} ep x batch 8)...")
    for ep in range(args.epochs):
        opt.zero_grad()
        loss = None
        nll_acc = 0.0
        for _ in range(8):
            epre, labs = pool[rng.randrange(len(pool))]
            nll, aux = model.nll_and_aux(epre, labs)
            term = (nll + 0.5 * aux) / 8
            loss = term if loss is None else loss + term
            nll_acc += nll.item() / 8
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        opt.step()
        if ep % max(1, args.epochs // 8) == 0 or ep == args.epochs - 1:
            print(f"    epoch {ep:>4}  nll={nll_acc:.3f}")

    print("\n  --- ZERO-SHOT evaluation (head trained ONLY on simulated embeddings) ---")
    erng = random.Random(args.seed + 777)
    recs, labs = simulate_strings(args.entities, erng)
    _report("held-out simulated", model, recs, labs, embed(encoder, recs))
    real = _load_real("febrl3", args.datasets_dir, args.max_real_entities, args.seed)
    if real:
        _report("REAL Febrl3 (subset)", model, real[0], real[1], embed(encoder, real[0]))
    else:
        print("  REAL Febrl3            : recordlinkage not installed — skipped")
    real = _load_real("dblp-acm", args.datasets_dir, args.max_real_entities, args.seed)
    if real:
        _report("REAL DBLP-ACM (subset)", model, real[0], real[1], embed(encoder, real[0]))
    else:
        print("  REAL DBLP-ACM          : datasets/DBLP-ACM missing — skipped")

    print("\n  vs step 4 (from-scratch trigram encoder): REAL Febrl3 F1 0.03,"
          " DBLP-ACM 0.035.\n  If pretrained transfer clears those (and nears the"
          " char+CC baseline), the\n  step-4 bottleneck was the encoder, as"
          " hypothesised.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
