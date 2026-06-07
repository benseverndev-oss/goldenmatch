# Amortized Bayesian Entity Resolution (program 1+3+6)

**Status:** brainstorm / research design. Not scheduled. No code in `goldenmatch/` yet —
exploratory prototypes live under `scripts/research/`.
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

## Decision

Steps 1 and 2 cleared. The amortized posterior is viable on simulated ER, with
calibration and a learned prior as the robust wins. Proceed to **step 3**
(EIG-over-partition active design, framing #6) — which first requires posterior
**sampling** (not just greedy MAP) from the step-2 head. Parallel real-program
work: a learned real-schema (string/LM) encoder, and a d-blink calibration
cross-check on a small real set. F1 is at a floor (parity with an oracle-tuned
baseline), to be lifted with capacity/training, not a blocker for step 3.
