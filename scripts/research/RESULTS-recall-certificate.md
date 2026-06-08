# Unsupervised recall certificate via capture-recapture — kill-criterion results

Runner: `scripts/research/recall_certificate.py`. numpy + the existing real-data
loaders; real Febrl3 / DBLP-ACM subsamples, 3 seeds. 2026-06-07.

> **Verdict (final): the unsupervised recall POINT estimate works at full scale
> on wide AND narrow schemas; a trustworthy SAFETY lower bound is NOT obtainable
> from the capture data alone.**
> - FP-aware estimator (ignore the FP-contaminated singleton cell, fit the
>   true-pair capture model from k≥2) + multi-modal decorrelation gives a
>   full-scale, label-free recall POINT estimate within ~0.001–0.04 of true on
>   both Febrl3 (0.999 vs 1.000) and DBLP-ACM (0.962 vs 1.000).
> - But the heterogeneity-robust **lower bound FAILS**: in every config where true
>   recall < 1.0 the "conservative" bound came out *above* true recall (e.g.
>   0.969 vs 0.953). Even the lowest reliable cell (f2) is drawn from easier
>   (captured-≥2) pairs, so the invisible hard tail can't be bounded from observed
>   cells. A safe certificate needs an external assumption / small labeled audit.

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

## FP-aware (latent-class) estimator — the rescue

The full-scale failure was FP contamination of Chao2. Fix (the latent-class idea
made concrete): with DECORRELATED matchers, false positives are almost all
SINGLETONS (a spurious token match in one field group rarely coincides in
another), so the multi-capture cells f_k (k≥2) are ~FP-free. Fit the true-pair
capture model from ONLY those cells and ignore the contaminated f_1. Under a
homogeneous binomial capture model, `log f_k - log C(K,k) = const + k·logit(p)`,
so regressing on k (k≥2) gives p, and recall of the union = 1 − (1−p)^K.

Full-scale results (FP-aware vs naive):

| Full dataset | K | naive recall (err) | **FP-aware recall (err)** | cons. bound | TRUE recall |
|---|---:|---|---|---:|---:|
| Febrl3 | 4 | 0.642 (0.311) | **0.966 (0.013)** | 0.963 (unsafe by 0.01) | 0.953 |
| Febrl3 | 5 | 0.761 (0.107) | **0.831 (0.037)** | 0.687 (safe) | 0.868 |
| DBLP-ACM | 3 | 0.017 (0.98) | 0.009 (0.99) | 0.000 (safe, useless) | 1.000 |

**What worked:** ignoring f_1 collapsed the full-scale point error from 0.31 →
0.01 on Febrl3. The point recall estimate is now accurate at full scale with no
labels — the dominant (FP-contamination) failure is fixed.

**What's still open:**
1. **The conservative bound is not reliably safe.** The homogeneous-p model is
   mildly *optimistic* under capture heterogeneity (easy pairs over-represented in
   the fitted cells), and with few capture cells (low K) the slope CI is
   overconfident — at K=4 the "lower bound" (0.963) sat *above* true recall
   (0.953). K=5 (more cells → wider CI) restored safety. A genuinely safe bound
   needs a heterogeneity-robust model (mixture-of-p / non-parametric lower bound),
   not the homogeneous fit. This is the remaining research.
2. **Narrow schemas fail.** DBLP-ACM (4 fields) can't form ≥3 decorrelated-precise
   groups; the matchers degenerate (p≈0). Needs a richer feature space (more
   fields, or multi-modal decorrelation: blocking-axis / encoder diversity).
3. Still assumes matcher **independence** within each class; correlated true-pair
   captures would bias p.

**Net:** the recall *point* estimate is now viable at full scale on a wide-schema
dataset — a real step from "broken." The recall *certificate* (a trustworthy lower
bound) is not done: heterogeneity-robust bounding is the open problem.

## Heterogeneity-robust bound + multi-modal decorrelation (the follow-up)

Two more upgrades were attempted; one worked, one hit a fundamental wall.

### Multi-modal decorrelation (Part 2) — WORKED

Decorrelation no longer comes only from disjoint field groups (which needs many
fields) but from **modality × field-group**: each matcher uses token-Jaccard OR
char-trigram-Jaccard over its field group. Different modalities catch different
errors (trigrams are typo/transposition-robust), so narrow schemas yield K≥3
decorrelated matchers.

| Full dataset | matchers | FP-aware recall point | TRUE | err |
|---|---|---:|---:|---:|
| DBLP-ACM (4 fields) | 2 groups × {token,trigram} | **0.962** | 1.000 | 0.038 |
| Febrl3 | 3 groups × {token,trigram} | **0.999** | 1.000 | 0.001 |

DBLP-ACM, which totally broke before (0.009, err 0.99, single-field groups
precision 0.08), now gives an accurate point estimate. Multi-modal decorrelation
fixes the narrow-schema failure for the POINT estimate.

### Heterogeneity-robust SAFE lower bound (Part 1) — FAILED

Goal: a recall lower bound that stays ≤ true recall under capture heterogeneity.
Attempt: under heterogeneity the cell curve `log f_k − log C(K,k)` is convex
(higher cells richer in easy pairs), so the low-end slope (f2→f3) reflects the
harder pairs → a lower, pessimistic p → `recall = 1−(1−p)^K` should under-state.

Validation on configs where true recall < 1.0 (the only cases that test it):

| config | conservative bound | TRUE recall | safe? |
|---|---:|---:|---|
| Febrl3 g4 token | 0.969 | 0.953 | **NO** |
| Febrl3 g5 token | 0.919 | 0.868 | **NO** |
| Febrl3 g6 token | 0.936 | 0.932 | **NO** |

The bound is optimistic (unsafe) in every non-trivial case. (Earlier "safe"
verdicts were artifacts of true recall = 1.0, where any bound ≤ 1 is trivially
safe.) **Why it's fundamental:** even f2 — the lowest FP-free cell — is drawn
from pairs captured ≥ 2 times, i.e. the *easier* pairs. The genuinely hard pairs
(captured 0–1 times) are invisible or FP-contaminated, and **no function of the
observed higher-order cells can recover the invisible-to-every-matcher tail**, so
no observed-cell estimator can safely lower-bound recall. The convexity trick
narrows the optimism but cannot remove it.

**Conclusion:** a provably-safe, label-free recall lower bound is **impossible** —
it requires an external assumption (e.g. "no true pair has per-matcher capture
prob < p_min") or a small labeled audit to bound the hard-tail mass. The
unsupervised method delivers an accurate POINT estimate but not a trustworthy
CERTIFICATE.

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
