# Real-schema encoder results — sim-to-real transfer probe

Runner: `scripts/research/real_schema_encoder.py`. torch 2.12 CPU; recordlinkage
for Febrl3; `datasets/DBLP-ACM/` for DBLP. Stable (crc32) trigram hashing so runs
reproduce. First run: 2026-06-07. Reproduce:

```bash
python scripts/research/real_schema_encoder.py --epochs 300 --max-real-entities 60
```

A learned, schema-agnostic STRING encoder (char-trigram EmbeddingBag over all
fields) feeds the step-2/3 partition head. Trained ONLY on a string simulator
(latent entities + Febrl-style corruption), then evaluated **zero-shot** on real
data — the real "amortize across datasets, no per-dataset retraining" claim.

## Results

| Eval set | N | entities | F1 amortized | F1 char+CC (best-of-4) | clusters pred / true | ECE |
|---|---:|---:|---:|---:|---:|---:|
| held-out simulated | 21 | 12 | **0.880** | 0.966 | 14 / 12 | 0.19 |
| **REAL Febrl3** (subset) | 151 | 60 | **0.030** | 0.890 | **148 / 60** | 0.27 |
| **REAL DBLP-ACM** (subset) | 111 | 60 | **0.035** | 0.922 | 108 / 60 | 0.40 |

## Verdict: in-distribution YES, zero-shot transfer NO

1. **The architecture works in-distribution.** On held-out *simulated* records the
   learned string encoder + amortized head reach **F1 0.880** with the right
   cluster count (14 vs 12 true) — so the string encoder learns discriminative,
   typo-robust embeddings and the head clusters them. (It needed ~300 epochs;
   at 30 it had collapsed to all-singletons, F1 0.)

2. **Zero-shot transfer to real schemas FAILS.** On real Febrl3 and DBLP-ACM the
   amortized head scores ~0.03 while the plain char-similarity threshold+CC
   baseline gets ~0.89–0.92. The cluster counts diagnose it: on Febrl3 the head
   opens **148 clusters for 60 true entities** — it does not recognise real
   typo-variants as co-referent, so it over-fragments to near-singletons.

3. **The bottleneck is the encoder/simulator, not the partition head.** The head
   was validated in steps 2–3 and works here in-distribution. What fails is the
   *embedding geometry on real strings*: the learned trigram-bag embeddings are
   tuned to the simulator's narrow vocabulary (20 first names, 20 surnames, 10
   cities, 4-digit ids). Real records hash into trigram buckets the model never
   meaningfully trained, so co-referent real records land far apart. The encoder
   memorised simulator vocabulary instead of learning a general string-similarity
   metric.

## What this sharpens about the program

Pure "train on a toy simulator → zero-shot to any real schema" is **not**
achievable with a lightweight learned-from-scratch trigram encoder. To get the
amortization-across-datasets payoff the program needs ONE of:

- **A pretrained general text encoder** as the record featurizer (char-level or
  sentence-transformer), so the embedding geometry is universal and only the
  partition head is amortized. (Highest-value; trades the no-heavy-deps stance.)
- **A far richer simulator** — real name/address/title dictionaries + realistic
  corruption — so the trigram distribution the encoder sees matches real data.
- **Light per-domain adaptation** (a few unsupervised epochs on the target
  corpus), conceding *pure* zero-shot in exchange for cheap transfer.

The honest headline: the partition head + learned prior + EIG design (steps 1–3)
hold up, but **the real-schema encoder is where the sim-to-real gap bites**, and
closing it — most likely via a pretrained encoder — is the gating problem for
taking 1+3+6 from a simulated proof-of-concept to real ER.

## Caveats

- Real evals are SUBSAMPLED (≤60 entities) to sizes near the simulator; the head
  trained on ~12-entity sims, and large size shifts add their own gap.
- char+CC baseline is charitably tuned (best of 4 thresholds).
- Single seed; in-distribution F1 has run-to-run variance on the tiny held-out
  set (the crc32 fix removed the worst non-determinism, from process-randomised
  `hash()`).
