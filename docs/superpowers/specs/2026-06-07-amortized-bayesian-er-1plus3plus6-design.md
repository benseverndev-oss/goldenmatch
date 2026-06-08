# Amortized Bayesian Entity Resolution (program 1+3+6)

**Status:** CLOSED — exploratory. Mechanisms validated; **NOT accuracy-competitive** on
saturated benchmarks. Not scheduled, no code in `goldenmatch/`. Prototypes + results live
under `scripts/research/`. See the **Final verdict** at the bottom before reviving this.
**Date:** 2026-06-07
**Provenance:** deep-research white-space scan of six novel ER framings (2026-06-07). This
note merges three of them — (1) amortized Bayesian partition inference, (3) self-supervised
mutual reconstructability, (6) optimal experimental design over the partition posterior —
into one coherent thesis. (#2 MDL is half-occupied; #4 differentiable blocking is a separate
prototype; #5 region embeddings is a component, not a headline.)

---

## Thesis

The three framings are not independent — they are three faces of one engine:

- **(1)** wants a *trained neural posterior over the entity partition* — one forward pass
  emits calibrated `p(partition | records)`, no per-dataset MCMC.
- **(3)** supplies the **likelihood** that posterior needs: instead of the hand-coded
  distortion model in `blink`/`d-blink`, *learn* it as "can a record's masked field be
  reconstructed from its putative cluster-mates?" Reconstructability **is** the
  co-reference signal.
- **(6)** acts on that posterior: choose the human/LLM label that maximally collapses
  **partition** entropy (not pair entropy), letting transitivity propagate.

The posterior *is* the confidence story GoldenMatch already sells ("tells you when it's
unsure", ADR-0001 `ControllerNotConfidentError`) — made principled rather than heuristic.

## Why now / where the moat is (from the 2026-06-07 scan)

| Piece | Closest prior art | Open cell we occupy |
|---|---|---|
| (1) | Bayesian ER mature but MCMC-bound: `blink` (Steorts, *Bayesian Analysis* 2015), `d-blink` (Marchant et al., *JCGS* 2021, ~hundreds of thousands of records). **Beraha–Favaro (arXiv:2507.18101, Jul 2025) just hit ~1000× via *variational* inference.** Amortized-clustering nets (NCP, ICML 2020; DAC, 2019; APDC, 2024) do partitions but for spike-sorting/communities, never ER. | **Amortization *across datasets*** — a trained net, single forward pass, under a **microclustering prior** (Betancourt et al., NeurIPS 2016). Neither the VI paper (still per-dataset) nor the amortized-clustering nets (no microclustering prior, not ER) occupy this. The moat is *shrinking* — frame the novelty as amortization, not "scale Bayesian ER". |
| (3) | Tabular masked-AE reconstruct *within-row* (Picket, *VLDBJ* 2021; TabTransformer). Bayesian distortion models reconstruct latent fields but via a hand-specified likelihood. IDEC uses reconstruction only as an embedding *regularizer*. Data Washing Machine (*JDIQ* 2025) uses *entropy* as an intrinsic cluster-quality signal. | **Cross-co-referent** masked-field reconstruction used **as the clustering criterion**, label-free. Nobody reconstructs from *other records believed co-referent* and lets that loss *decide* clusters. Cleanest pure-novelty piece. |
| (6) | Active-ER selects by **per-pair** uncertainty (ALIAS, KDD 2002 → DIAL, *PVLDB* 2022). ITACC (Aronsson & Chehreghani, ICDM 2025) does info-gain over the *whole clustering* with transitivity — but on a non-Bayesian Gibbs energy model, generic correlation clustering, **requires K**. | **Bayesian** optimal experimental design (expected partition-posterior-entropy reduction) over a **real ER posterior** with an attribute/distortion model and **unknown K** — exactly what (1) supplies to ITACC's acquisition machinery. |

## Architecture

```
records ──▶ per-record encoder (set-transformer / LM bi-encoder)   [#5 region embeddings optional here]
              │
              ▼
   amortized partition head  ──────────────────────────────▶  q(partition | X)   [#1]
   (NCP/DAC-style sequential assignment, microclustering prior)      │
              ▲                                                       │
              │ likelihood = reconstructability                       ▼
   mask field f of record r ; predict from current               EIG acquisition  [#6]
   cluster-mates ; loss = recon error                            argmax over candidate
              [#3 — this is the training signal AND the              labels of expected
               intrinsic cluster-quality score]                      Δ H(partition)
```

Training is **simulation-based**: forward-simulate a population (latent entities → a learned
or parametric corruption process → records), so ground-truth partitions are free and the
amortized head meta-learns across simulated datasets. The reconstruction likelihood (3) is
what ties the simulator's corruption model to the inference net.

## Validation plan (kill criteria, in order)

1. **#3 in isolation first** (smallest, riskiest assumption). Hypothesis: *mutual
   reconstructability ranks clusterings monotonically with F1.* Test on DBLP-ACM + Febrl3
   via the committed harness (`scripts/research/recon_er_experiment.py`, reusing
   `dqbench_adapters`). **Kill if** reconstructability does not separate the gold partition
   from perturbed (over-/under-merged) partitions. No deep nets needed for the first cut —
   a frozen embedder + held-out-field cosine reconstruction suffices.
2. **(1) amortized head** on simulated data; check calibration (does the posterior's
   stated confidence match empirical accuracy?) and that it tracks `d-blink` on a small
   real set where MCMC is still feasible.
3. **(6)** only after (1) gives a usable posterior: compare label efficiency of
   EIG-over-partition vs. per-pair uncertainty (the DIAL/ALIAS baseline) at equal label
   budget.

## Risks / framing landmines to pre-empt

- **(1) is dual to existing Bayesian ER** — reviewers will say "this is just amortized
  blink." Defense: amortization *across datasets* + microclustering prior is the unclaimed
  cell; the VI paper is the warning that "scale it" alone is no longer novel.
- **(3) vs. generative distortion models** — must frame as *discriminative, learned,
  cross-record* reconstruction (not a relabeling of Steorts' distortion likelihood, not
  IDEC's regularizer, not Picket's within-row mask).
- **(6) vs. ITACC** — the contribution is the *Bayesian posterior + attribute model +
  unknown K*, not the acquisition function (ITACC has that).
- **Compute** — amortized-clustering nets are finicky to train; budget for the simulator
  being the hard part (garbage corruption model → garbage posterior).

## Step 1 result (2026-06-07 — real Febrl3 + DBLP-ACM)

Run via `scripts/research/recon_er_experiment.py`; full tables in
`scripts/research/RESULTS-3-reconstructability.md`. **Step-1 gate PASSES.**

- **Viable, strongly on PII**: Febrl3 reconstructability ranks clusterings by F1
  at Spearman **+0.944**, gold on top, monotone across over/under/mixed.
- **Kernel is data-dependent**: bibliographic DBLP-ACM FAILS with char
  Jaro-Winkler (+0.591) because titles/venues share vocabulary; an IDF-weighted
  token kernel (discount common tokens) lifts it to +0.647 and clears the gate.
  => *learn* the field reconstructor in step 2 rather than fixing a kernel.
- **Over-merge precision-blindness persists** on bibliographic data regardless of
  kernel, and the crude `size^2` prior does not rescue it (clusters are tiny).
  => the precision half must be a **learned microclustering prior (#1)**, not a
  size penalty. The experiment cleanly localises which half each piece owns.

## Step 2 result (2026-06-07 — amortized partition head on simulated data)

Run via `scripts/research/amortized_partition_er.py` (NCP/DAC-style head +
masked-field recon aux + learned empty-cluster prototype); full tables in
`scripts/research/RESULTS-2-amortized-partition.md`. **Step-2 gate PASSES on
what matters**, across 4 seeds:

- **Calibration is robust**: ECE 0.014–0.030 every seed — assignment confidence
  matches accuracy. This is the posterior property the program is built on, and
  the threshold+CC baseline cannot provide it.
- **The microclustering prior is LEARNED** to within ~0.01–0.02 of the true
  new-cluster rate with **no size penalty** — validates amendment (b).
- **Partition F1 is at PARITY** with a baseline charitably tuned (best-of-4 L2
  thresholds on the eval set) — matched threshold-free in one forward pass. The
  marginal cases were undertraining: seed 1 went 0.652→0.701 (over baseline)
  from 300→600 epochs, prior tightening in lockstep.
- Architecture lesson: sum-pooling collapsed the model to "always open new"
  (F1≈0.03); scoring on cluster **mean** + interaction features + a learned
  empty prototype unlocked learning.

## Step 3 result (2026-06-07 — EIG active design over the partition posterior)

Run via `scripts/research/active_partition_er.py`; full tables in
`scripts/research/RESULTS-6-active-design.md`. **Step-3 gate PASSES.** Added
posterior sampling to the step-2 head, then compared pair-selection strategies
(identical posterior + identical must/cannot-link transitive-closure
conditioning; only selection differs):

- **EIG = H_b(p_ij) is exact** for a noiseless oracle: `I(A_ij; Z) = H_b(p_ij)`.
- **Robust win — uncertainty collapse**: partition-EIG drives posterior pairwise
  uncertainty down ~2.7x faster than per-pair static (1.18 vs 3.18 bits at 24
  labels), ~14x faster than random.
- **F1**: eig ≥ static at every budget (small edge averaged; widens in
  sparse regimes — a smoke run hit eig's target in 16 labels vs static's 24).
- **Transitivity does the work**: eig re-scores after each label, so the
  must/cannot-link closure lets it skip logically-resolved induced pairs while
  static keeps querying them.

## Decision — full 1+3+6 loop demonstrated on simulated ER

All three steps cleared on simulated data: reconstruction-as-likelihood (#3,
step 1), amortized calibrated partition posterior with a learned microclustering
prior (#1, step 2), and EIG-over-partition active design (#6, step 3). The
program hangs together end-to-end.

## Step 4 result (2026-06-07 — real-schema encoder, sim-to-real transfer probe)

Run via `scripts/research/real_schema_encoder.py`; full tables in
`scripts/research/RESULTS-real-schema-encoder.md`. **Negative transfer result —
the honest gating finding.** A learned, schema-agnostic char-trigram string
encoder feeds the step-2/3 head, trained ONLY on a string simulator:

- **In-distribution YES**: held-out simulated F1 0.880, right cluster count.
- **Zero-shot to real schemas NO**: REAL Febrl3 F1 0.030 (head opens 148 clusters
  for 60 true entities — over-fragments), REAL DBLP-ACM F1 0.035, while the plain
  char+CC baseline gets ~0.89–0.92.
- **Bottleneck is the encoder/simulator, not the head**: the from-scratch trigram
  embeddings memorise the simulator's narrow vocabulary; real records hash into
  buckets the model never trained, so real co-referents aren't close. (Also fixed
  a reproducibility bug — process-randomised `hash()` → crc32.)

## Step 5 result (2026-06-07 — frozen pretrained encoder fixes most of the gap)

Run via `scripts/research/pretrained_transfer_er.py`; full tables in
`scripts/research/RESULTS-pretrained-transfer.md`. **Step-4 hypothesis CONFIRMED.**
Swapping the from-scratch trigram encoder for frozen MiniLM (head trained only on
simulated embeddings, zero-shot eval on real):

- REAL Febrl3 F1 **0.03 -> 0.42** (~14x), DBLP-ACM **0.035 -> 0.21** (~6x).
- **Well-calibrated zero-shot on real data** (ECE 0.08-0.09) — the posterior
  property, holding on never-seen data.
- Still below the tuned char+CC baseline (0.42 vs 0.95); failure mode FLIPPED from
  over-fragmentation (step 4) to OVER-MERGING (33 clusters vs 60 true). The residual
  gap is a sim-vs-real decision-BOUNDARY mismatch, not an encoder problem.

## Step 6 result (2026-06-07 — richer simulator closes the boundary gap)

Run via `pretrained_transfer_er.py --simulator rich` (`richer_simulator.py`);
full tables in `scripts/research/RESULTS-rich-simulator.md`. **Hypothesis
CONFIRMED**, controlled A/B on identical real subsets:

- **Over-merging fixed**: cluster count goes from 38/41 (basic sim) to **exactly
  60/60 true** on both real Febrl3 and DBLP-ACM with the richer simulator.
- Real F1 ~doubles: Febrl3 0.42->0.68, DBLP-ACM 0.27->0.61, zero-shot, calibrated
  (ECE 0.09). Residual ~0.17-0.23 below the tuned char+CC baseline is now a
  RECALL gap on hard typos, not over-merging.

## Decision — zero-shot ER on real data is functional; arc summary

The encoder arc resolved the sim-to-real gap with two targeted fixes — pretrained
encoder (step 5) + richer simulator (step 6) — taking real zero-shot F1 from ~0.03
to ~0.6-0.68 with **correct cluster counts and calibration**. The 1+3+6 program is
now a working, calibrated, zero-shot ER system on real data, within ~0.2 F1 of a
tuned classical baseline.

Remaining work (diminishing returns; none blocking the thesis):
1. **Close the recall gap** to the baseline: a stronger/ER-tuned text encoder for
   hard typos, or light per-domain temperature calibration (concede pure zero-shot).
2. **d-blink calibration cross-check** on a small real set where MCMC is feasible.
3. **Soft Bayesian conditioning** + noisy-oracle handling in step 3; scale evals
   beyond 60-entity subsamples; multi-seed CIs.
4. **Lift the head** (Set-Transformer context, matched train/eval sizes) — held-out
   sim still ~0.70, so the head has slack independent of the encoder.

---

## Final verdict (2026-06-07) — exploratory; mechanisms validated, NOT competitive

Read this before reviving the program.

**What it proved.** All six framings have genuine, verified white space (the
2026-06-07 scan), and each mechanism held up under test: reconstructability ranks
clusterings by F1 (+0.94 on real Febrl3); the amortized head emits a calibrated
posterior with a *learned* microclustering prior; partition-EIG beats per-pair
active selection; and a pretrained encoder + richer simulator took **zero-shot**
real-ER F1 from 0.03 to ~0.6–0.68 with correct cluster counts and calibration.

**What it did NOT prove — the honest bottom line.** As an ER *system* it is not
good:

| | real Febrl3 F1 | real DBLP-ACM F1 |
|---|---:|---:|
| This work (amortized, zero-shot) | ~0.68 | ~0.61 |
| Trivial char-sim + connected-components (tuned) | ~0.85 | ~0.85 |
| **GoldenMatch's existing zero-config controller** | **0.944** | **0.964** |

It loses to a ~15-line baseline and is far behind the production system it was
exploring alternatives to.

**Why the ceiling is structural.** The residual gap is recall on hard typos, and
the only fix is a much stronger encoder — but fine-tuning the encoder on matching
pairs *is* supervised entity matching, already solved (Ditto ≈ 98% on DBLP-ACM).
Amortization/zero-shot is exactly what costs the accuracy: you get amortization or
SOTA F1, not both cheaply. Also, these benchmarks are near-saturated (incumbents at
0.94–0.96), so there was never accuracy headroom for novelty here — the test
favoured the incumbent from the start.

**Where (if anywhere) this is worth reviving.** Not as an accuracy play. Only if
reframed around its actual novel value — **calibrated uncertainty** and
**label-efficiency** (the EIG result) — and evaluated on **hard regimes** (messy
product data, cross-org/PPRL) where 0.6 may be respectable and calibration matters,
rather than on saturated structured benchmarks. Even then, GoldenMatch already has
active sampling and a confidence-gating controller (ADR-0001), so the marginal value
is modest.

**Disposition:** kept as a documented research arc (prototypes + per-step RESULTS
under `scripts/research/`, public writeup in the docs Research section). Do not
treat as a product proposal. Do not re-run expecting a win on Febrl3/DBLP-ACM.
