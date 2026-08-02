# The 1.5B ER-matcher's decision geometry — Layer 1 (locked)

**Status:** LOCKED. Correlational + per-layer geometry + dictionary learning + causal
validation all done. Steering the match direction across depth drives the verdict 0→1
monotonically — the direction is the causal substrate of the decision.
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

## Locking Layer 1 (the GPU/Modal work)

The GPU pipeline is `scripts/er_matcher/interp/modal_interp.py` (runs against the
merged fp16 fine-tune the GGUF was quantized from, `/out/model_1p5b/merged`):

1. **Residual-stream capture + per-layer geometry** — DONE (stage 1). ✅
2. **Dictionary learning** — SAE on layer-14 residuals (`train_sae`). DONE. ✅
3. **Causal validation (the lock)** — multi-layer steer/ablate (`causal_validate`).
   DONE — the direction is causal. ✅

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

### Stage 2 result — dictionary learning (SAE) on layer 14

Sparse autoencoder on **848k** layer-14 token activations (expansion 8 → 12,288
features; activations normalized to mean-norm √d; L1 = 0.06). Converged to a genuinely
sparse code: **L0 ≈ 212** active features per token (1.7% of the dictionary), recon 2.2.

The decision-token feature analysis matches the geometry: **no single SAE feature
dominates** — the top features correlate with the match label at only **|0.55–0.60|**
(several negative = "non-match evidence" features, one positive). Consistent with the
stage-1 finding that the concept is a *distributed* ~8D code, not one monosemantic
neuron; the diff-of-means direction is the aggregate of many partial-signal features.
(`sae_layer14.pt` + `sae_features_layer14.json` on the volume.)

### Stage 3 result — the causal lock

A single-layer intervention barely moved the verdict (steering the layer-14 decision
token: swing 0.35→0.42; ablation 0.385→0.361) — because the direction is *redundantly*
encoded across depth (present from layer 1), so downstream layers re-derive it. The
correct test for a redundant code is **multi-layer intervention**: steer/ablate the
per-layer diff-of-means direction at the decision token across layers **8–20** at once,
in natural gap-units (c=1 = the class-mean difference at each layer).

| Intervention (layers 8–20) | mean P(match) |
|---|---|
| steer c = −4 | **0.000** |
| baseline (c = 0) | 0.385 |
| steer c = +4 | **1.000** |
| ablate the direction (all layers) | 0.226 |

**Steering the match direction drives the verdict fully and monotonically from
certain-non-match (0.000) to certain-match (1.000).** This is the lock: the direction
is not epiphenomenal — it *is* the causal substrate of the same-entity decision.
Ablating it across the window pushes the model toward non-match (0.385 → 0.226). The
single-site→multi-site contrast is itself the mechanism: no one layer is a bottleneck,
but the direction across depth controls the decision.

Individual SAE features, steerable only single-layer (they are trained at one layer),
move P(match) by ≤~0.02 — the same depth-redundancy that defeats single-site
diff-of-means steering, so they are correlational markers rather than single-site
causal levers. (`causal_multilayer_8_20.json` on the volume.)

### Layer 1 is locked — what it means

The same-entity decision is a **low-dimensional (~8D) linear structure** that **forms
at layer 1**, **sharpens to a perfect linear separator by L13**, **concentrates onto
the dominant residual axis late**, and is **causally controllable**: steering it flips
the model's verdict 0→1 monotonically. The math is sound; the primitive is real.

**Layer 2 can now begin** — auto-labeling the proven structure into human-readable
match rationales/flags (e.g. interpreting the negative "non-match evidence" SAE
features, or decomposing the diff-of-means direction into per-field contributions).
Building that abstraction *before* this lock would have been the linguistic-bias trap
this program rejects; now it rests on a causally-validated basis.

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

The GPU pipeline (Modal, against the fp16 `/out/model_1p5b/merged`):

```bash
modal run scripts/er_matcher/interp/modal_interp.py::probe_layers   # stage 1
modal run scripts/er_matcher/interp/modal_interp.py::sae --layer 14 --l1 0.06 --expansion 8
modal run scripts/er_matcher/interp/modal_interp.py::causal --layer 14 --lo 8 --hi 20
```
