"""Real-schema encoder — learned string encoder for the amortized ER posterior.

Highest-value follow-up to steps 1-3
(`docs/superpowers/specs/2026-06-07-amortized-bayesian-er-1plus3plus6-design.md`).
Replaces the toy continuous-field simulator of `amortized_partition_er.py` with:

  (a) a LEARNED, schema-agnostic STRING encoder — char-trigram EmbeddingBag over
      all fields of a record (typo-robust, can learn to down-weight common
      trigrams = the IDF lesson from step 1's RESULTS-3). No fixed kernel, no
      pretrained multi-GB LM. Schema-agnostic (bag over all fields) so ONE
      trained net applies to ANY schema -- Febrl3's 10 fields or DBLP-ACM's 4.
  (b) a masked reconstruction auxiliary in EMBEDDING space (the #3 signal):
      predict a record's own bag embedding from its cluster-mates' encodings.

THE TEST (the real amortization claim): train the head on a STRING SIMULATOR
(latent entities -> Febrl-style corruption), then evaluate ZERO-SHOT on real
Febrl3 and DBLP-ACM subsamples -- no per-dataset retraining. Reports pairwise-F1
+ calibration vs a char-similarity threshold+CC baseline.

This is a sim-to-real transfer probe; weak transfer is a valid, reportable
outcome. Honest caveats inline + in RESULTS.

Run (needs torch; recordlinkage for Febrl3; datasets/DBLP-ACM for DBLP):
    python scripts/research/real_schema_encoder.py --epochs 250
"""
from __future__ import annotations

import argparse
import random
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from amortized_partition_er import _HAVE_TORCH, _sample_cluster_sizes, pairwise_f1  # noqa: E402

if _HAVE_TORCH:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F

_B = 8192  # trigram hash buckets


# --------------------------------------------------------------------------- #
# String featurisation: record -> char-trigram bucket ids (over all fields).
# --------------------------------------------------------------------------- #
def _trigrams(s: str) -> list[str]:
    s = "  " + s.lower() + "  "
    return [s[i:i + 3] for i in range(len(s) - 2)]


def _record_ids(field_values: list[str]) -> list[int]:
    ids: list[int] = []
    for v in field_values:
        for t in _trigrams(v):
            # stable hash (Python's hash() on str is per-process randomised,
            # which made trigram bucketing — and thus results — non-reproducible).
            ids.append(zlib.crc32(t.encode("utf-8")) % _B)
    return ids or [0]


@dataclass
class SchemaDataset:
    input_ids: "torch.Tensor"   # 1D Long, all records' trigram buckets concatenated
    offsets: "torch.Tensor"     # 1D Long, start index per record
    labels: list[int]           # gold entity id per record
    n: int


def to_dataset(records: list[list[str]], labels: list[int]) -> "SchemaDataset":
    flat, offsets = [], []
    for fv in records:
        offsets.append(len(flat))
        flat.extend(_record_ids(fv))
    return SchemaDataset(torch.tensor(flat, dtype=torch.long),
                         torch.tensor(offsets, dtype=torch.long), labels, len(records))


# --------------------------------------------------------------------------- #
# String simulator: latent entities with string fields + Febrl-style corruption.
# --------------------------------------------------------------------------- #
_FIRST = ["james", "mary", "robert", "linda", "michael", "patricia", "john",
          "jennifer", "david", "elizabeth", "william", "susan", "richard",
          "jessica", "thomas", "sarah", "charles", "karen", "daniel", "nancy"]
_LAST = ["smith", "johnson", "williams", "brown", "jones", "garcia", "miller",
         "davis", "rodriguez", "martinez", "hernandez", "lopez", "wilson",
         "anderson", "thomas", "taylor", "moore", "jackson", "martin", "lee"]
_CITY = ["boston", "denver", "austin", "seattle", "miami", "chicago", "portland",
         "phoenix", "dallas", "atlanta"]


def _corrupt(s: str, rng: random.Random) -> str:
    out = list(s)
    for _ in range(rng.randint(0, 2)):
        if not out:
            break
        p = rng.randrange(len(out))
        r = rng.random()
        if r < 0.34:
            out[p] = rng.choice("abcdefghijklmnopqrstuvwxyz")
        elif r < 0.67:
            out.pop(p)
        else:
            out.insert(p, rng.choice("abcdefghijklmnopqrstuvwxyz"))
    s2 = "".join(out)
    if rng.random() < 0.15 and len(s2) > 2:       # abbreviate
        s2 = s2[:rng.randint(1, max(1, len(s2) - 1))]
    return s2


def simulate_strings(n_entities: int, rng: random.Random) -> tuple[list[list[str]], list[int]]:
    sizes = _sample_cluster_sizes(n_entities, rng)
    records, labels = [], []
    for eid, sz in enumerate(sizes):
        base = [rng.choice(_FIRST), rng.choice(_LAST), rng.choice(_CITY),
                str(rng.randint(1000, 9999))]
        for _ in range(sz):
            rec = [_corrupt(f, rng) if rng.random() < 0.8 else f for f in base]
            if rng.random() < 0.1:                # drop a field
                rec[rng.randrange(len(rec))] = ""
            records.append(rec)
            labels.append(eid)
    idx = list(range(len(records)))
    rng.shuffle(idx)
    return [records[i] for i in idx], [labels[i] for i in idx]


# --------------------------------------------------------------------------- #
# Model: learned string encoder + the proven step-2 partition head.
# --------------------------------------------------------------------------- #
if _HAVE_TORCH:

    def _mlp(i, h, o):
        return nn.Sequential(nn.Linear(i, h), nn.ReLU(), nn.Linear(h, o))

    class SchemaPartitionModel(nn.Module):
        def __init__(self, fdim: int = 32, d: int = 48):
            super().__init__()
            self.bag = nn.EmbeddingBag(_B, fdim, mode="mean")
            self.enc = _mlp(fdim, 96, d)
            self.ctx = _mlp(d, 96, d)
            self.score = _mlp(5 * d + 2, 128, 1)
            self.empty = nn.Parameter(torch.zeros(d))
            self.recon = _mlp(d, 96, fdim)       # mate-context -> own bag emb (#3)
            self.d = d

        def encode(self, ds: "SchemaDataset"):
            bag = self.bag(ds.input_ids, ds.offsets)   # [N, fdim]
            return self.enc(bag), bag                  # e [N,d], bag [N,fdim]

        def _logits(self, ei, means, counts, U):
            # mirrors amortized_partition_er._logits: cluster mean + interaction
            # features (e_i-m, e_i*m) + log size + is_new; new uses a learned
            # empty prototype. (Kept local to avoid coupling to that module's
            # private method.)
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

        def nll_and_aux(self, ds: "SchemaDataset"):
            e, bag = self.encode(ds)
            n = e.size(0)
            order = torch.randperm(n)
            sums: list[torch.Tensor] = []
            counts: list[int] = []
            gold_to_idx: dict[int, int] = {}
            total_after = e[order].flip(0).cumsum(0).flip(0)
            nll = e.new_zeros(())
            for step, ridx in enumerate(order.tolist()):
                ei = e[ridx]
                U = self.ctx(total_after[step] - ei)
                means = (torch.stack([s / c for s, c in zip(sums, counts)])
                         if sums else e.new_zeros(0, self.d))
                cnt = torch.tensor(counts) if counts else torch.zeros(0)
                logp = F.log_softmax(self._logits(ei, means, cnt, U), 0)
                y = ds.labels[ridx]
                choice = gold_to_idx[y] if y in gold_to_idx else len(sums)
                nll = nll - logp[choice]
                if y in gold_to_idx:
                    sums[choice] = sums[choice] + ei
                    counts[choice] += 1
                else:
                    gold_to_idx[y] = len(sums)
                    sums.append(ei.clone())
                    counts.append(1)
            # embedding-space reconstruction aux (#3)
            aux = e.new_zeros(())
            na = 0
            by: dict[int, list[int]] = {}
            for i, y in enumerate(ds.labels):
                by.setdefault(y, []).append(i)
            for members in by.values():
                if len(members) < 2:
                    continue
                for i in members:
                    mates = [m for m in members if m != i]
                    pred = self.recon(e[mates].mean(0))
                    aux = aux + F.mse_loss(pred, bag[i].detach())
                    na += 1
            aux = aux / na if na else aux
            return nll / n, aux

        @torch.no_grad()
        def decode(self, ds: "SchemaDataset"):
            e, _ = self.encode(ds)
            n = e.size(0)
            total = e.sum(0)
            sums: list[torch.Tensor] = []
            counts: list[int] = []
            assign = [0] * n
            seen = e.new_zeros(self.d)
            conf, corr = [], []
            entity_clusters: dict[int, set[int]] = {}
            for ridx in range(n):
                ei = e[ridx]
                U = self.ctx(total - seen - ei)
                means = (torch.stack([s / c for s, c in zip(sums, counts)])
                         if sums else e.new_zeros(0, self.d))
                cnt = torch.tensor(counts) if counts else torch.zeros(0)
                p = torch.softmax(self._logits(ei, means, cnt, U), 0)
                choice = int(torch.argmax(p))
                conf.append(float(p[choice]))
                y = ds.labels[ridx]
                if y not in entity_clusters:
                    corr.append(1.0 if choice == len(sums) else 0.0)
                else:
                    corr.append(1.0 if choice in entity_clusters[y] else 0.0)
                if choice < len(sums):
                    sums[choice] = sums[choice] + ei
                    counts[choice] += 1
                else:
                    sums.append(ei.clone())
                    counts.append(1)
                entity_clusters.setdefault(y, set()).add(choice)
                assign[ridx] = choice
                seen = seen + ei
            return assign, conf, corr


# --------------------------------------------------------------------------- #
# Baseline + calibration.
# --------------------------------------------------------------------------- #
def char_threshold_cc(records: list[list[str]], thresh: float) -> list[int]:
    """Connected components of records within char-similarity `thresh` on the
    whitespace-joined record string. The classic threshold+transitive-closure
    baseline, given best-of-several thresholds (charitable)."""
    try:
        from rapidfuzz.distance import JaroWinkler as JW
        sim = lambda a, b: JW.normalized_similarity(a, b)
    except Exception:
        from difflib import SequenceMatcher
        sim = lambda a, b: SequenceMatcher(None, a, b).ratio()
    strs = [" ".join(r) for r in records]
    n = len(strs)
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a
    for i in range(n):
        for j in range(i + 1, n):
            if sim(strs[i], strs[j]) >= thresh:
                parent[find(i)] = find(j)
    return [find(i) for i in range(n)]


def ece(conf: list[float], corr: list[float], bins: int = 10) -> float:
    if not conf:
        return 0.0
    tot, e = len(conf), 0.0
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        idx = [k for k, c in enumerate(conf) if (lo < c <= hi) or (b == 0 and c <= hi)]
        if not idx:
            continue
        e += (len(idx) / tot) * abs(sum(conf[k] for k in idx) / len(idx)
                                    - sum(corr[k] for k in idx) / len(idx))
    return e


# --------------------------------------------------------------------------- #
# Real-data loaders (subsampled) via recon_er_experiment's adapters.
# --------------------------------------------------------------------------- #
def _load_real(name: str, datasets_dir: Path, max_entities: int, seed: int):
    import recon_er_experiment as R
    loaded = (R.load_febrl3() if name == "febrl3"
              else R.load_dblp_acm(datasets_dir))
    if loaded is None:
        return None
    gold = R.gold_clustering(loaded.all_ids, loaded.gt_pairs)  # cid -> [ids]
    # Deterministic subset across processes: sort clusters by CONTENT signature
    # first (cluster ids depend on hash-randomised set iteration in the GT->CC
    # step, so list(gold) order is not reproducible), THEN seeded-shuffle.
    def _sig(cid):
        return tuple(sorted(
            " ".join(str(loaded.records[r].get(f, "")) for f in loaded.fields)
            for r in gold[cid]))
    cids = sorted(gold, key=_sig)
    rng = random.Random(seed)
    rng.shuffle(cids)
    chosen = cids[:max_entities]
    records, labels = [], []
    for lab, cid in enumerate(chosen):
        for rid in gold[cid]:
            rec = loaded.records[rid]
            records.append([str(rec.get(f, "")) for f in loaded.fields])
            labels.append(lab)
    return records, labels


# --------------------------------------------------------------------------- #
def _report(tag: str, model, records, labels) -> None:
    ds = to_dataset(records, labels)
    assign, conf, corr = model.decode(ds)
    f1 = pairwise_f1(assign, labels)
    base = max(pairwise_f1(char_threshold_cc(records, t), labels)
               for t in (0.55, 0.7, 0.82, 0.9))
    e = ece(conf, corr)
    n_ent = len(set(labels))
    pred_clusters = len(set(assign))
    print(f"  {tag:<22} N={len(records):>4} ent={n_ent:>3} | "
          f"F1(amortized)={f1:.3f}  F1(char+CC best4)={base:.3f}  ECE={e:.3f}  "
          f"clusters: pred={pred_clusters} true={n_ent}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--epochs", type=int, default=250)
    ap.add_argument("--entities", type=int, default=12)
    ap.add_argument("--max-real-entities", type=int, default=60)
    ap.add_argument("--datasets-dir", type=Path, default=Path("datasets"))
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    if not _HAVE_TORCH:
        print("  torch not installed — architecture-only here. `pip install torch`.")
        return 0

    torch.manual_seed(args.seed)
    rng = random.Random(args.seed)
    model = SchemaPartitionModel()
    opt = torch.optim.Adam(model.parameters(), lr=2e-3)

    print(f"  training learned string encoder + head ({args.epochs} ep x batch 8)...")
    for ep in range(args.epochs):
        opt.zero_grad()
        loss = None
        nll_acc = 0.0
        for _ in range(8):
            recs, labs = simulate_strings(args.entities, rng)
            nll, aux = model.nll_and_aux(to_dataset(recs, labs))
            term = (nll + 0.5 * aux) / 8
            loss = term if loss is None else loss + term
            nll_acc += nll.item() / 8
        loss.backward()
        torch.nn.utils.clip_grad_norm_(model.parameters(), 5.0)
        opt.step()
        if ep % max(1, args.epochs // 8) == 0 or ep == args.epochs - 1:
            print(f"    epoch {ep:>4}  nll={nll_acc:.3f}")

    print("\n  --- zero-shot evaluation (trained ONLY on the string simulator) ---")
    # held-out simulated (sanity)
    erng = random.Random(args.seed + 777)
    sim_recs, sim_labs = simulate_strings(args.entities, erng)
    _report("held-out simulated", model, sim_recs, sim_labs)
    # real Febrl3
    real = _load_real("febrl3", args.datasets_dir, args.max_real_entities, args.seed)
    if real:
        _report("REAL Febrl3 (subset)", model, *real)
    else:
        print("  REAL Febrl3            : recordlinkage not installed — skipped")
    # real DBLP-ACM
    real = _load_real("dblp-acm", args.datasets_dir, args.max_real_entities, args.seed)
    if real:
        _report("REAL DBLP-ACM (subset)", model, *real)
    else:
        print("  REAL DBLP-ACM          : datasets/DBLP-ACM missing — skipped")

    print("\n  (Sim-to-real transfer probe. F1 at/above the char+CC baseline on a"
          " real subset\n   means the simulator-trained encoder transfers; below"
          " means the sim-to-real\n   gap dominates and the simulator/encoder need"
          " work — both are valid findings.)\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
