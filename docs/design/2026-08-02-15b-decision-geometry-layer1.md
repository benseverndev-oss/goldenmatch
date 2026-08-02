# The 1.5B ER-matcher's decision geometry — Layer 1, brick 1

**Status:** correlational result landed; causal validation deferred to the GPU path.
**Scope:** mechanistic interpretability of the local ER-matcher (fine-tuned
Qwen2.5-1.5B-Instruct, the pinned `er-1p5b` GGUF). Not a product/quality change.

## Why this exists (the two-layer program)

Traditional XAI carries a **linguistic bias**: it forces a model's high-dimensional
geometry back onto human language (token attributions, "the model looked at the
name"), which is a lossy projection of what the network actually computes. The
alternative framing this work adopts:

- A network operates on **structural geometry** in its latent space.
- **Primitives** are fundamental directions (basis vectors) in that space.
- **Superposition** lets the model pack many features into nearly-orthogonal
  directions of a high-dim space.
- A **concept** is a *precise linear combination of primitives* — a direction, not
  a word.

So the program is split into two layers, built in order:

- **Layer 1 — the math.** Map the model's structural primitives with *linear-algebra
  soundness only*. Human readability is explicitly **irrelevant** here; the only
  question is whether the geometry holds up. Lock this first.
- **Layer 2 — the translation.** *Only after Layer 1 is locked*, abstract the proven
  structures into human-readable heuristics/flags.

This document is the first Layer-1 result: **does the model's "these two records are
the same entity" decision have the geometry the framework predicts — a
low-dimensional linear structure — at the point where it decides?**

## Method

`scripts/er_matcher/interp/decision_geometry.py`. Correlational, final-layer.

1. **Probe set.** Balanced true-matches + **hard negatives** from historical_50k.
   Hard = *different person, same surname soundex* (a blocking look-alike — exactly
   the regime where the model has to work; random negatives differ on every field
   and inflate every metric, so they are a contrast baseline only).
2. **Decision representation.** For each pair, render the **exact** prompt the model
   scores (`build_chat` → Qwen chat template up to `<|im_start|>assistant`) and take
   the **last-token final-layer hidden state** — the vector the model reads to emit
   `{"match": …}`. (llama.cpp `embedding=True`; dim 1536.)
3. **Three geometric probes.**
   - **Linear separability** — 5-fold logistic-probe accuracy. Is the decision
     linearly *decodable* at all?
   - **Single direction** — diff-of-means axis **fit on train, AUC on a held-out
     test fold**. Is there *one* "match" primitive direction, and does it generalize?
   - **Effective dimensionality** — probe accuracy from the top-k PCA components.
     How *few* basis directions carry the concept?

## Result

historical_50k, 160 match + 160 hard negatives, seed 0, from the committed
`decision_geometry.py` (the numbers the `Reproduce` command below regenerates):

| Probe | **Hard negatives** | Random negatives (contrast) |
|---|---|---|
| 5-fold logistic-probe accuracy | **0.991** | 0.96 |
| Single diff-of-means axis (held-out AUC) | **0.967 ± 0.022** | 0.96 |
| top-1 PC probe acc | **0.922** | 0.76 |
| top-2 PCs | 0.938 | 0.91 |
| top-4 PCs | 0.956 | 0.95 |
| top-8 PCs | **0.988** | 0.96 |
| top-16 / 32 PCs | 0.984 / 0.991 | 0.94 / 0.94 |

The hard-negative column is the load-bearing one: the structure does **not** weaken
when the negatives are look-alikes (shared surname soundex) rather than arbitrary
records — if anything it is sharper, because the fine-tune has learned a crisp
decision boundary exactly in the hard regime. (An earlier one-hard-negative-per-key
sampling gave 0.959 / 0.955 ± 0.017 — same conclusion; the committed script drains
multiple distinct look-alike pairs per surname key, a fuller and slightly more
separable set.)

**What Layer 1 has established, empirically, on our model:**

1. The "same-entity" decision is a **linear structure** in the residual stream —
   ~0.99 linear-probe accuracy *even against hard look-alikes*. It is a geometric
   fact, not a linguistic artifact.
2. It is **low-dimensional** — ~4–8 basis directions recover the full 1536-dim probe
   (plateau by rank 8). Concepts = linear combinations of a *few* primitives, as the
   framework predicts.
3. There is a **single dominant "match" direction** that generalizes **out of
   sample to hard negatives** at 0.967 ± 0.022 AUC. This vector is the first concrete
   **candidate primitive** — discovered by the math, its human label deferred to
   Layer 2.

The confound that would have made this fake — negatives being trivially separable —
is ruled out: the structure is at least as strong on hard look-alikes as on random
negatives.

## Honest boundary (what this does NOT yet show)

- **Correlational, not causal.** A linear probe proves the direction is *decodable*,
  not that the model *uses* it to decide. Proving use needs **causal
  steering/ablation** (add/subtract the direction, measure the verdict flip).
- **Final layer only.** llama.cpp exposes the last-layer hidden state, not the
  per-layer residual stream — so we see the decision *after it has crystallized*, not
  *where across depth* the primitive forms.
- **One diff-of-means axis is a discriminative axis, not a proven basis.** PCA gives
  directions of variance, not interpretable primitives. Turning "there is a low-dim
  linear structure" into "here are the N primitive features and their meanings" needs
  **dictionary learning (a sparse autoencoder) on the residual stream**.

These three gaps all require the **fp16 model with forward hooks on GPU** — the Modal
path where training already runs (`scripts/er_matcher/modal_train.py`,
`gpu_tiers.py`) — not this llama.cpp box.

## To lock Layer 1 (the GPU/Modal follow-on)

The GPU pipeline is `scripts/er_matcher/interp/modal_interp.py` (runs against the
merged fp16 fine-tune the GGUF was quantized from, `/out/model_1p5b/merged`):

1. **Residual-stream capture + per-layer geometry** — DONE (stage 1). ✅
2. **Dictionary learning** — SAE on layer-L residuals (`train_sae`). In progress.
3. **Causal validation (the lock)** — steer/ablate along the match direction + top
   SAE features (`causal_validate`). Next.

### Stage 1 result — where the primitive forms across depth

28-layer sweep (200 hard-negative + 200 match pairs, decision-token residual). The
embedding layer (L0) is degenerate — the decision token is always `\n`, so its
last-token vector is identical across pairs — and is skipped.

| Layer | sep_acc | dir_auc (held-out) | top-1 PC | top-8 PC |
|---|---|---|---|---|
| 1 | 1.000 | 0.993 | 0.485 | 0.993 |
| 13 | 1.000 | **1.000** | 0.605 | 0.988 |
| 14 | 1.000 | 0.999 | 0.550 | 0.988 |
| 20 | 0.985 | 0.989 | 0.960 | 0.965 |
| 28 | 0.982 | 0.992 | 0.960 | 0.968 |

The arc is the mechanistic story:

- **Formed early, distributed.** By **layer 1** the match direction is already a
  near-perfect linear separator (dir_auc 0.993, sep 1.0) — but **top-1 PC ≈ 0.49
  (chance)** while top-8 ≈ 0.99. So the primitive exists as a **low-rank (~8D)
  distributed code**, *not* aligned to the dominant axis of variance (early-layer
  variance is dominated by surface/token features).
- **Sharpened mid-stack.** dir_auc **peaks at 1.000 (L13)** and sits ≥0.999 across
  L11–14 — the crispest linear decision, decision formed but not yet collapsed.
- **Concentrated late (the model "commits").** From L15→28, **top-1 PC jumps to
  ~0.96**: the match direction *becomes* the dominant axis of the decision-token
  residual — the residual stream reorganizes so the single top direction ≈ the
  verdict. sep dips slightly (~0.98) as more of the variance *is* the decision.

**SAE + causal target: L14** — near-peak linear crispness (dir_auc 0.999), mid-stack,
decision formed but still a rich distributed code (top-1 ≈ 0.55, not yet collapsed) —
the richest layer for dictionary learning, and upstream of the final readout for
steering. (`layer_probes.json` on the `er-matcher-out` volume has all 28 layers.)

### Remaining to lock

- **Dictionary learning.** SAE decomposes the layer-14 residual into an overcomplete
  monosemantic basis (the primitives under superposition); rank features by
  decision-token match correlation.
- **Causal validation (the lock).** Steer/ablate along the match direction and each
  top SAE feature; a feature is a *real* primitive only if moving along it moves the
  verdict as predicted. This is the linear-algebra-soundness bar Layer 1 demands.

**Only after causal validation holds does Layer 2 begin** — auto-labeling the proven
features into human-readable match rationales/flags. Building the abstraction before
the math is locked is precisely the linguistic-bias trap this program rejects.

## Reproduce

```bash
GOLDENMATCH_LOCAL_LLM_PATH=<pinned er-1p5b.gguf> \
  python -m scripts.er_matcher.interp.decision_geometry \
    --data scripts/autoconfig_quality/vendored/historical_50k.parquet \
    --per-class 160 --negatives hard --seed 0
```

Pure helpers (pair mining + the three probe estimators) are unit-tested model-free
in `scripts/er_matcher/test_decision_geometry.py`. The model path (CPU, ~1.4s/pair)
is exercised manually against the pinned GGUF.
