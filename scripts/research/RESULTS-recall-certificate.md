# Unsupervised recall certificate via capture-recapture — kill-criterion results

Runner: `scripts/research/recall_certificate.py`. numpy + the existing real-data
loaders; real Febrl3 / DBLP-ACM subsamples, 3 seeds. 2026-06-07.

> **Verdict: PASS on small high-precision subsamples, FAIL at full scale.**
> On 60–80-entity subsamples the capture-recapture recall estimate tracks true
> recall within ~0.03–0.09 MAE with no labels. But the full-scale runs (added per
> the follow-up) **break**: the matchers' precision craters at scale, false
> positives contaminate the population estimator, and the recall estimate is badly
> off (Febrl3 0.61 est vs 0.95 true). The conservative lower bound never gave
> false assurance (stayed ≤ true recall) but is uselessly loose. Making it work at
> scale needs **FP-aware (latent-class) capture-recapture** — real open work.

## Why this idea (derived from two failed arcs)

The amortized-Bayesian and landscape arcs both failed the same way: novel but
not competitive, because they competed on **accuracy** on **saturated**
benchmarks at the **clustering layer**. The lessons, turned into constraints,
point at an unsaturated axis above the clustering layer: **knowing your recall
without labels.** In production, precision is cheap to estimate (sample matches,
check them) but recall is not — you can't sample the true matches you *didn't*
find. Every ER deployment ships blind on recall. There is no incumbent baseline,
so the fair test can't be trivially lost the way the clustering ideas were.

## The method

Run K matchers; each "captures" a subset of the true matching pairs; the overlap
structure of their captures estimates, via **capture-recapture** (Chao2 incidence
estimator — the same dual-system math used for census undercount), how many true
pairs *none* of them caught → the hidden population → recall. The estimator never
sees gold; we compare its output to the true recall we can compute from gold.

## The trajectory (the tension is the finding)

Capture-recapture needs matchers that are **both independent AND individually
accurate** — and in ER those conflict. Three matcher designs:

| Matcher design | overlap | precision | N_hat | result |
|---|---|---|---|---|
| Shared global affinity gate (correlated) | 0.55–0.84 | ~1.0 | ≈ D | degenerate: recall_hat ≈ 1.0 always (correlation bias) |
| Single-field (fully decorrelated) | low | **0.02–0.05** | huge | FP population explodes → recall_hat → 0 |
| **Disjoint field groups** (precise + decorrelated) | ~0.45 | 0.85–0.97 | ≈ N_true | **tracks true recall** |

Strong matchers all use the same evidence (correlated); weak matchers use partial
evidence (imprecise). The disjoint-field-group design threads the needle: each
matcher uses several fields (precise) but a *different* set (decorrelated), so a
pair corrupted in one group's fields is caught by another group.

## Results (disjoint field groups, K=2–3, 3 seeds)

| Dataset | seed | found (D) | N_true | N_hat | recall_hat_pc | true_recall | prec |
|---|---|---:|---:|---:|---:|---:|---:|
| Febrl3 | 0 | 253 | 308 | 306 | 0.796 | 0.792 | 0.96 |
| Febrl3 | 1 | 246 | 298 | 278 | 0.841 | 0.785 | 0.95 |
| Febrl3 | 2 | 241 | 275 | 286 | 0.814 | 0.847 | 0.97 |
| DBLP-ACM | 0 | 73 | 62 | 74 | 0.836 | 1.000 | 0.85 |
| DBLP-ACM | 1 | 67 | 65 | 67 | 0.967 | 1.000 | 0.97 |
| DBLP-ACM | 2 | 68 | 63 | 69 | 0.919 | 1.000 | 0.93 |

Febrl3: MAE 0.031, bias +0.009 (≈unbiased). DBLP-ACM: MAE 0.092, bias −0.092
(conservative). Both clear the <0.10-MAE kill-criterion.

## Honest caveats

- **Small subsamples** (N≈180–240, 60–80 entities), 3 seeds. Promising, not
  conclusive; needs full-scale + more seeds + CIs.
- **`recall_hat_pc` uses the true precision** as a stand-in for the cheaply-
  estimable precision (in production you'd sample+label ~50 pairs). With
  high-precision matchers the label-free `recall_hat = D/N_hat` already tracks
  (Febrl3 0.83–0.88 vs true 0.79–0.85); the correction matters when precision < 1
  (DBLP). This is a fair stand-in, but it is a stand-in.
- **The accuracy–independence tension is real and dataset-dependent.** The
  disjoint-group sweet spot worked here, but a schema without a good
  decorrelated-but-precise field split may not afford one. Robustness across
  schemas is unproven.
- **Correlation / heterogeneity bias is present, just not fatal** (DBLP overlap
  ~0.90 → mild conservative bias). On data with homogeneous corruption (pairs
  hard for *every* group) it would bite and the estimate would turn optimistic —
  the known failure mode of dual-system estimation.

## Full-scale runs + CI + precision-sampling + conservative bound (the follow-up)

Added: a blocked matcher (scales to full N), analytic Chao2 variance + log-transform
CIs, a Poisson log-linear estimator (K≥3), **real precision-sampling** (label a small
sample with an oracle; Wilson CI) replacing the gold stand-in, and a **conservative
recall lower bound** = precision_lo · D / N_hi.

Run on the FULL datasets (no subsampling):

| Full dataset | N | K | precision (sampled / true) | Chao2 N_hat [95% CI] | N_true | recall point [95% CI] | cons. bound | TRUE recall |
|---|---:|---:|---|---|---:|---|---:|---:|
| Febrl3 | 5000 | 4 | 0.72 / 0.77 | 9569 [9429, 9722] | 6538 | 0.61 [0.52, 0.68] | ≥0.52 | **0.95** |
| DBLP-ACM | 4910 | 3 | 0.08 / 0.07 | 117880 [114k, 122k] | 2224 | 0.02 [0.01, 0.04] | ≥0.01 | **1.00** |

**It breaks at scale.** Root causes:

1. **False-positive contamination (dominant).** At full scale the field-group
   matchers' precision collapses (0.72, 0.08), so the union `D` is mostly FPs
   (8051 found vs 6538 true on Febrl3). The FP-singletons look like "rare species"
   to Chao2 → it inflates the population (N_hat 9569 ≫ N_true 6538) → recall is
   *under*-estimated (0.61 vs 0.95). Capture-recapture must run on the TRUE-pair
   population, which can't be isolated without labels. The subsample PASS held only
   because subsamples were high-precision (≈0.95+), masking this.
2. **Schema width.** DBLP has 4 fields; K=3 forces tiny single-field groups
   (venue alone, year alone) → precision 0.08 → total breakdown. The method needs
   enough fields to form K disjoint *precise* groups.
3. **The conservative bound stayed safe but useless.** It never overstated recall
   (≤ true in both), but ≥0.52-when-actually-0.95 is not actionable.

Precision-sampling and the CIs themselves worked correctly (sampled precision
tracked true; CIs are sound w.r.t. sampling variance). The failure is upstream:
the estimator's input (a low-precision union) violates its assumptions.

**Corrected verdict:** the basic Chao2-on-the-raw-union recall certificate is
**not viable at scale**. The honest next step is FP-aware estimation (treat each
captured pair as latent true/false; model true-pair capture probabilities and the
spurious-FP process jointly) — a real research step, not a tweak. Until then, the
subsample result should be read as "the idea is sound only when matchers are
high-precision," which is itself the hard ER problem.

## Next levers (if pursued)

1. **Full-scale + multi-seed CIs**; report the estimate *as* an interval, not a
   point (capture-recapture has standard variance formulas).
2. **Real precision sampling** instead of the gold-precision stand-in.
3. **≥3 groups + log-linear / Chao models** that are robust to heterogeneity and
   give a *conservative lower bound* on recall (the safety-relevant direction).
4. **Decorrelation by modality** (different blocking axes / encoders), not just
   field groups, to push overlap down and reduce correlation bias.
5. Test on a regime with homogeneous-hard pairs to characterise where the
   optimistic bias becomes dangerous.
