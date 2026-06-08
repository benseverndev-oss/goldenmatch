# Richer-simulator results — closing step 5's decision-boundary gap

Runner: `scripts/research/pretrained_transfer_er.py --simulator {basic,rich}`
(rich = `richer_simulator.py`). Frozen MiniLM featurizer, head trained only on
simulated embeddings, zero-shot eval on real data. torch 2.12 CPU +
sentence-transformers 5.5. First run: 2026-06-07.

```bash
python scripts/research/pretrained_transfer_er.py --simulator rich \
    --epochs 350 --train-pool 160 --max-real-entities 60
```

## Controlled A/B (IDENTICAL real subsets: Febrl3 N=162, DBLP-ACM N=104)

The real-subset selector was made content-deterministic
(`real_schema_encoder._load_real`) so both arms see the same records.

| Eval set | basic sim | **rich sim** | F1 char+CC (best4) | clusters basic→rich / true | ECE (rich) |
|---|---:|---:|---:|---:|---:|
| held-out simulated | 0.741 | 0.696 | 1.000 | 14 / 12 | 0.15 |
| **REAL Febrl3** | 0.415 | **0.678** | 0.845 | 38 → **60** / 60 | 0.093 |
| **REAL DBLP-ACM** | 0.265 | **0.613** | 0.846 | 41 → **60** / 60 | 0.089 |

## Verdict: richer-simulator hypothesis CONFIRMED — over-merging fixed

1. **Over-merging is fully corrected.** The basic simulator's low between-entity
   diversity made the head learn a too-loose join boundary -> it under-segmented
   real data (38, 41 clusters for 60 true entities). The richer simulator's
   hundreds-of-token vocabularies + realistic multi-mode corruption tighten the
   boundary, and the head now recovers **exactly 60/60 clusters on BOTH** real
   datasets. The diagnosis from step 5 was right.

2. **Real-data F1 roughly doubles** on identical subsets: Febrl3 0.415 -> 0.678
   (+0.26), DBLP-ACM 0.265 -> 0.613 (+0.35) — pure zero-shot, no target labels.

3. **Stays calibrated zero-shot** (ECE 0.09) — the posterior remains trustworthy
   on never-seen real data.

4. **Residual gap to the tuned baseline ~0.17–0.23.** char+CC (best of 4
   thresholds, tuned on the eval set) is still ahead (0.85 vs 0.68/0.61). The
   remaining gap is no longer over-merging — it's recall on hard true-matches
   (heavy typos / abbreviations the frozen MiniLM doesn't place close enough).

5. **Harder sim, better transfer.** Held-out simulated F1 dips (0.741 -> 0.696)
   because the rich simulator is harder, yet real transfer jumps — the expected
   "train on realistic difficulty, generalise better" trade.

## Encoder-arc trajectory (real Febrl3 / DBLP-ACM zero-shot F1)

| Step | Encoder | Simulator | Febrl3 | DBLP-ACM | failure mode |
|---|---|---|---:|---:|---|
| 4 | from-scratch trigram | basic | 0.03 | 0.035 | over-fragment (148, 108 clusters) |
| 5 | frozen MiniLM | basic | 0.42 | 0.27 | over-merge (38, 41) |
| **6** | **frozen MiniLM** | **rich** | **0.68** | **0.61** | exact cluster count; recall gap |

Two targeted fixes — pretrained encoder (step 5) then richer simulator (step 6) —
took zero-shot real-ER F1 from ~0.03 to ~0.6–0.68 with correct cluster counts and
calibration, within ~0.2 F1 of a tuned classical baseline. The amortized
1+3+6 posterior is now a functional, calibrated zero-shot ER system on real data.

## Honest caveats

- Real evals SUBSAMPLED to 60 entities (Febrl3 N=162, DBLP N=104); the head
  trained on ~12-entity sims, so size shift remains a confound.
- char+CC baseline is charitably tuned (best-of-4 thresholds on the eval set);
  the amortized head is pure zero-shot, untuned.
- Single seed; held-out-sim F1 has run-to-run variance on the tiny eval set.
- Vocabularies are generic (no target-dataset values) — transfer is honest, not
  memorised. The remaining recall gap is the next lever (better encoder for hard
  typos, or light per-domain temperature calibration).
