# Label-constrained (semi-supervised) EM anchors

**Date:** 2026-08-01
**Status:** shipped (infrastructure, default-off) — measured cluster-neutral on historical_50k; kept as a principled capability, not a claimed quality win.

## What

An optional `label_pairs` input to Fellegi-Sunter EM training
(`core/probabilistic.py::train_em`, threaded through `load_or_train_em`). Given
a mapping of canonical `(min_row_id, max_row_id)` pairs → `0/1` labels, the
labeled pairs are **injected into the EM training sample** and their E-step
responsibility is **clamped to the label** every iteration (match → 1.0,
non-match → 0.0). The M-step then re-estimates `m` (and the match prior
`p_match`) from the full blocked population *with those pairs pinned as ground
truth*. `u` is unchanged — it is estimated from random pairs and held fixed
during EM, exactly as in the unsupervised path.

This is **label-constrained (semi-supervised) EM**, distinct from the two
mechanisms it supersedes for using labels:

- **NOT a score override.** The labels never replace a pair's final FS score.
  They improve the trained *model*, which is then re-scored across the whole
  population. (An earlier per-pair band override was measured cluster-marginal
  on historical_50k, +0.0038; see below.)
- **NOT `estimate_m_from_labels`** (the existing supervised analog, Splink's
  `estimate_m_from_label_column`). That *replaces* `m` with the labeled
  positives' comparison-level frequencies — biased when the labels are drawn
  from a narrow region (band-only positives → pessimistic `m`). Label-constrained
  EM keeps `m`/`u` representative of the full population (the anchors are a small
  fraction) and lets the labels only steer the decision boundary.

## Why (the intended flow)

The motivating loop is the local-LLM ER matcher: run FS once → identify the
uncertain band `[0.55, 0.65]` → label those pairs with the pinned LLM → **rerun
FS with the labels fed into EM training**. The labels improve the pinned model
we ship, consistent with the "we train centrally, users consume zero-config"
product model.

## Seam

- `train_em(..., label_pairs=...)` — the mechanism.
- `load_or_train_em(..., label_pairs=...)` — the pipeline call site; explicit
  kwarg wins, else it reads a training-time `ContextVar`.
- `set_fs_label_anchors({mk_name_or_"*": {pair: label}})` / `reset_fs_label_anchors(token)`
  — the ContextVar seam a two-pass label-then-rerun driver uses, so anchors reach
  EM without polluting the user-facing config. Default None → unsupervised EM,
  byte-identical to the prior behavior (129 existing FS tests unchanged).

## Measurement (the honest result)

De-risked on `historical_50k` (50,578 rows) through the **real `dedupe_df`
pipeline**, measured at **cluster level** (pairwise F1 over gold clusters — the
gate that matters, since transitive closure absorbs near-boundary flips):

| run | anchors | cluster pair-F1 | ΔF1 |
|---|---|---|---|
| baseline (unsupervised EM) | — | 0.98895 | — |
| + 600 real 1.5B band labels [0.55,0.65], 88.5% acc | 600 | 0.98895 | **+0.0000** |
| + all [0.55,0.65] band pairs, **gold** (oracle) | 67,321 | 0.98930 | **+0.00035** |
| + **decision-straddling** [0.45,0.55], **gold** (oracle) | 154,631 | 0.98960 | **+0.0006** |
| + wide [0.40,0.60], **gold** (oracle) | 198,760 | 0.98960 | **+0.0006** |

**Band position matters, and it still doesn't help.** The clustering cut on this
config is ≈0.50 (emitted/clustered pairs start at 0.507; the review band is
[0.35, 0.49]), so the originally-labeled [0.55,0.65] window sits *above* the cut
(accept zone). Re-testing the oracle ceiling on the pairs that actually straddle
the boundary ([0.45,0.55]) lifts cluster-F1 by only **+0.0006** — pure recall
(0.9789 → 0.9802), precision flat — and a wider band does no better. The residual
recall is **blocking-bounded** (pairs never generated as candidates can't be
rescored), and FS's linear calibration co-moves the link threshold *with* the
model, so retraining largely preserves the threshold crossings.

The anchored model is **materially different** — 254,246 / 267,356 scored pairs
(95%) change score, `p_match` moves 0.148 → 0.172, `m` shifts on 5 fields — and
the review band moves (4260 → 4274 review pairs). **Yet the above-threshold
cluster pair-set is byte-identical** with the realistic labels, and moves only
208 pairs (ΔF1 +0.0004) even at the oracle ceiling.

**Conclusion:** on `historical_50k` the uncertain band is structurally
non-decisive for clustering. **Five independent measurements agree** — per-pair
override on [0.55,0.65] (+0.0038), label-constrained EM with realistic labels
(+0.0000), oracle on [0.55,0.65] (+0.00035), oracle on the decision-straddling
[0.45,0.55] (+0.0006), oracle on [0.40,0.60] (+0.0006): **band labels do not move
cluster-level ER quality on this dataset, regardless of band position, label
quality, or volume.** The ceiling is set by blocking-bounded recall + threshold
co-movement + transitive closure, not by the labels — so no labeling strategy
(LLM or otherwise) recovers it here.

## Disposition

Ship the seam as **infrastructure, default-off**, matching how the repo treats
FS capabilities that are principled but not a demonstrated DQbench win (the
`tf_adjustment` precedent). It is the correct, principled way to feed *any*
labels — LLM band labels, steward corrections, ground truth — into FS training,
and it will pay where the labeled region *is* decisive (lower operating points,
sparse-signal / heavily-corrupted data where the boundary sits in the labeled
band, two-table linkage). It does **not** pay as a band-label ER boost on a
dataset FS already resolves at a high operating point.

The local-LLM matcher's measured value therefore lives in the shipped 1.5B
matcher itself and the distilled band student (perf), **not** in a band-label
EM boost on already-strong datasets.
