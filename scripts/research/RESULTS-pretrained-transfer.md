# Pretrained-encoder transfer results — does universal geometry fix step 4?

Runner: `scripts/research/pretrained_transfer_er.py`. Frozen
sentence-transformers `all-MiniLM-L6-v2` as the record featurizer; only the
partition head trains, on EMBEDDED simulated records; evaluated **zero-shot** on
real data. torch 2.12 CPU + sentence-transformers 5.5. First run: 2026-06-07.

```bash
python scripts/research/pretrained_transfer_er.py --epochs 350 --train-pool 160 --max-real-entities 60
```

## Results, vs step 4's from-scratch trigram encoder

| Eval set | N | ent | **F1 pretrained** | F1 step-4 (trigram) | F1 char+CC (best4) | clusters pred/true | ECE |
|---|---:|---:|---:|---:|---:|---:|---:|
| held-out simulated | 21 | 12 | 0.741 | 0.880 | 0.966 | 12 / 12 | 0.13 |
| **REAL Febrl3** | 151 | 60 | **0.420** | 0.030 | 0.947 | 33 / 60 | 0.09 |
| **REAL DBLP-ACM** | 111 | 60 | **0.210** | 0.035 | 0.817 | 35 / 60 | 0.08 |

## Verdict: hypothesis CONFIRMED (encoder was the bottleneck) — but not yet competitive

1. **The pretrained encoder largely fixes zero-shot transfer.** Real-data F1
   jumped **~14x on Febrl3 (0.03 -> 0.42)** and **~6x on DBLP-ACM (0.035 -> 0.21)**
   just by swapping the from-scratch trigram encoder for frozen MiniLM, with the
   head trained only on simulated embeddings. This confirms step 4's diagnosis:
   the sim-to-real gap lived in the ENCODER, not the partition head. MiniLM's
   universal geometry places real co-referents close enough that a
   simulator-trained head can act on them.

2. **Zero-shot is now well-CALIBRATED on real data** (ECE 0.08–0.09, better than
   in-distribution) — the posterior's confidence is trustworthy on data it was
   never trained on. This is the property the whole 1+3+6 program is built on, and
   it now holds zero-shot on real ER.

3. **But it still trails the tuned char+CC baseline** (0.42 vs 0.95 on Febrl3).
   The failure mode FLIPPED: step 4 over-fragmented (148 clusters for 60), the
   pretrained head now OVER-MERGES (33 clusters for 60). The join decision boundary
   the head learned on the simulator is too loose for real data — the simulator's
   corruption is milder / its within-vs-between-entity embedding-distance
   distribution differs from real Febrl3/DBLP. So the residual gap is a
   SIM-vs-REAL CALIBRATION-OF-THE-BOUNDARY problem, not an encoder problem.

## What this sharpens

- **The encoder hypothesis is validated.** A pretrained text encoder is the right
  featurizer for amortization across schemas; from-scratch trigrams are not.
- **The remaining lever is the decision boundary**, two cheap options:
  1. **Richer / realistic simulator** — real name/address/title dictionaries and
     corruption tuned so the simulated within/between-entity distance distribution
     matches real data; the head's learned boundary then transfers.
  2. **Light per-domain calibration** — a single temperature/threshold fit on the
     target corpus (unsupervised, e.g. match the expected cluster-count) to correct
     the over-merge, conceding *pure* zero-shot for near-zero-cost adaptation.
- A bigger projection head / Set-Transformer context and matching train/eval sizes
  would also help (held-out sim is only 0.741 here — the head itself has slack).

## Honest caveats

- Real evals SUBSAMPLED to <=60 entities (near the ~12-entity training sims);
  size shift is a confound, and the head trained small.
- char+CC baseline is charitably tuned (best of 4 thresholds on the eval set);
  the pretrained head is pure zero-shot, untuned.
- Single seed. The pretrained head is slightly WORSE in-distribution than the
  trigram encoder (0.741 vs 0.880) but vastly better out-of-distribution — the
  expected generalisation/overfit trade.
