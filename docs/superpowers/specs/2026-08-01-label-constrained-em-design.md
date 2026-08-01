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
| + 600 real 1.5B band labels (88.5% acc) | 600 | 0.98895 | **+0.0000** |
| + all band pairs, **gold** labels (oracle ceiling) | 67,321 | 0.98930 | **+0.00035** |

The anchored model is **materially different** — 254,246 / 267,356 scored pairs
(95%) change score, `p_match` moves 0.148 → 0.172, `m` shifts on 5 fields — and
the review band moves (4260 → 4274 review pairs). **Yet the above-threshold
cluster pair-set is byte-identical** with the realistic labels, and moves only
208 pairs (ΔF1 +0.0004) even at the oracle ceiling.

**Conclusion:** on `historical_50k` the uncertain band is structurally
non-decisive for clustering — the decisive high-confidence pairs dominate and
near-boundary changes stay in the review band or get absorbed by transitive
closure. This is the *same wall* the per-pair band override hit (+0.0038). Two
independent mechanisms plus an oracle ceiling agree: **band labels do not move
cluster-level ER quality on this dataset**, regardless of label quality or
volume.

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
