# The 1.5B ER-matcher's decision geometry — Layer 1 locked + Layer 2 translation

**Status:** Layer 1 LOCKED (correlational + per-layer geometry + dictionary learning +
causal validation — steering the match direction across depth drives the verdict 0→1
monotonically, so the direction is the causal substrate of the decision). Layer 2
DONE — the proven direction is translated into human field signals (first_name +
birth_place dominate; surname/dob ignored; R² = 0.51), cross-validated by the SAE basis.
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

**Layer 2 now rests on a causally-validated basis** (below). Building that abstraction
*before* this lock would have been the linguistic-bias trap this program rejects.

## Layer 2 — the translation (human-readable, built on the locked basis)

Layer 2 does only what the framework reserves for a locked Layer 1: it *abstracts the
proven structure* into human-readable signals, **without inventing a new linguistic
story** — it decomposes the exact direction Layer 1 proved causal. Two independent
translations (`field_attribution.py`, run at layer 14 by
`modal_interp.py::layer2_abstraction`, 400 match + 400 hard-negative pairs):

### (a) Decompose the causal direction into human field signals

Regress each pair's *projection onto the proven match direction* against
human-readable per-field **agreement** signals (jaro-winkler similarity of each
field's two values). Standardized coefficients:

| Field | coef | reads as |
|---|---|---|
| **first_name** | **+0.42** | the dominant discriminator |
| **birth_place** | +0.30 | secondary discriminator |
| occupation | +0.15 | |
| postcode | +0.08 | |
| **surname** | **+0.04** | ~ignored |
| **dob** | **+0.01** | ~ignored |

**R² = 0.51** — an honest faithfulness number *for this target*: human field-agreement
explains ~half the **projection onto the causal direction**. **But that is the wrong
target for a per-decision explainer** — the projection is a lossy 1D shadow of the ~8D
decision. The right target is the model's **actual P(match) output**, measured by the
committed `modal_interp.py::faithfulness_eval` stage (cluster-disjoint split, fp16
model, teacher-forced readout) — see the faithfulness section of
`2026-08-03-15b-interp-handoff.md` for the table, the caveats, and the **unreproduced
earlier 0.87/0.97 figures**. Headline: on the honest split the *frozen shipped
weights* score **0.25–0.32 against look-alike (hard) negatives and 0.50 against random
negatives**, and refitting the weights on the same 6-feature basis buys almost nothing
(+0.01–0.05) — the binding constraint is the feature basis and the linear link, not the
frozen weights. A **record-disjoint** split (records disjoint but the same *entity* on
both sides) inflates the linear rows by ~+0.22, which is why the split is stated
explicitly everywhere here.

Two readable findings fall straight out of the geometry:
- **surname ≈ 0 (predicted).** The probe's hard negatives *share* surname-soundex by
  construction, so surname agreement is uninformative among them — and the model's
  match direction correctly ignores it, keying on **first_name** to break look-alikes.
- **dob ≈ 0 (not predicted).** dob does *not* discriminate — consistent with dob being
  corrupted/unreliable in historical_50k, so the model **learned to down-weight it**.

### (b) Label the SAE basis by field

For each top Layer-1 SAE feature, the field-agreement signal its activation tracks
most. The basis is field-aligned, and **it converges with (a)**:
- **"non-match evidence" features** (negative match-corr) mostly track **first_name**
  disagreement (feats 501/1055/6347/1546), plus dob/surname/occupation disagreement.
- **"match evidence" features** (positive match-corr) track **birth_place** and
  **first_name** agreement — feat 10062 tracks birth_place at r = +0.59, the cleanest
  single field-aligned primitive.

Both translations — the direction decomposition and the independently-derived SAE
labels — converge on the same human story: **first_name + birth_place agreement are
the primitives composing the model's same-entity decision against surname look-alikes.**
That convergence is the evidence the abstraction is faithful, not a just-so story —
and it is only meaningful *because* the underlying direction was causally locked first.
(`layer2_abstraction_L14.json` on the volume.)

### Shipped: a per-decision explainer

The Layer-2 profile is productized in the shipped package as a per-decision
explainer (`goldenmatch/core/er_matcher/explainer.py` +
`LocalLlamaAdapter.score_and_explain`). Given a record pair and the model's verdict,
it emits a field-grounded rationale using the model's OWN learned field-importance —
pure/model-free (jaro-winkler + the weight table), schema-agnostic, and honest about
the R²=0.51 faithfulness bound. Example (hard-negative): *"NO MATCH (confidence 0.82).
Supporting: surname (exact). Opposing: first_name ('John' vs 'Michael', 0.46),
birth_place (0.45)…"* — surname agrees but the model rejects on the discriminating
fields, exactly the learned behavior. It flags the low-confidence case where the
field story disagrees with the verdict, and falls back to a neutral profile (no
faithfulness claim) on schemas outside the person profile.

## Stripping the model — which parameters don't influence the outcome

Layer 1 said the ER decision is *formed by ~L13 and only committed thereafter*. If
that's real, the late layers are dead weight for entity resolution. Direct test
(`modal_interp.py::layer_early_exit`): read the verdict out of the layer-K residual
(final norm + lm_head applied to `hidden_states[K]` — i.e. **delete every layer > K**
and pass the residual straight to the readout, the "logit lens"); sweep K; compare to
the full-model verdict and the gold labels. 400 match + 400 hard-neg, historical_50k.

| Exit at layer K | verdict-agree vs full | F1 vs gold |
|---|---|---|
| full (28) | 1.000 | 0.886 |
| 27 | 0.985 | 0.870 |
| 24 | 0.973 | 0.919 |
| **21** | **0.970** | **0.892** |
| 20 | 0.954 | 0.839 |
| 17 | 0.955 | 0.907 |
| 16 | 0.870 | 0.929 |
| ≤15 | ~0.4–0.6 | degenerate (0 / 0.667) |

Two readings, and the distinction is the whole point:
- **Strict verdict-reproduction → strip 0.** Exact agreement with the full model isn't
  reached until L28 — the last layers keep flipping a few *borderline* pairs. If the
  bar is "reproduce every borderline flip," no layer is removable.
- **ER-outcome (F1) → strip the last 7 (~25% of the block params).** F1 saturates by
  **L21** (0.892 ≥ full 0.886) and stays there through L28 with 97% verdict agreement.
  **Layers 22–28 add no ER correctness — they only shuffle borderline verdicts.** This
  is the honest answer to "strip parameters not influencing the outcome": for the ER
  outcome, ~a quarter of the transformer depth is dead weight.

**This is a LOWER bound.** The logit lens reuses the *untrained* final RMSNorm+head on
mid-layer residuals (a scale/basis mismatch — which is why L≤15 read degenerate even
though the linear *probe* decodes the decision there from L1). A **truncate-and-adapt**
run (cut at K, train a fresh readout) confirms and extends it — below.
(`layer_early_exit.json` on the volume.)

### Truncate-and-adapt: with a trained readout, in-distribution ER needs ~8 layers

Truncate at K (keep layers 0..K-1) and train a **fresh linear readout** on the layer-K
decision-token residual (frozen backbone; record-disjoint train/test by cluster parity
so the head can't memorize). This replaces the untrained logit-lens head with a fair
adapted one. 1200 train / 926 test pairs, historical_50k:

| Truncate at L | F1 (trained readout) |
|---|---|
| 8 | 0.986 |
| 12 | 0.986 |
| 16 | 0.993 |
| 21 | 0.992 |
| 28 (full backbone) | 0.992 |

**k\* = L8 → strip 20/28 layers (~71% of block params) with in-distribution ER F1
preserved.** The ER decision is *linearly present in the residual by layer 8*; the
logit lens needed L21 only because it reused the untrained final head. The ~46-layer
gap between the two numbers (L21 vs L8) is exactly the work the late layers do that a
fresh in-distribution head makes unnecessary.

**Two caveats that bound the claim — this is where it would be easy to overclaim:**
1. **The readout is SUPERVISED on in-distribution gold**, so this measures *information
   presence* (is the label linearly decodable at layer K?), not the model's zero-shot
   verdict. That's why F1 ≈ 0.99 here far exceeds the model's own generative F1 (0.886)
   — the head is fit to *this* distribution. It's the stage-1 linear probe (0.99 @ L14)
   extended down to L8.
2. **In-distribution only.** Head trained and tested on historical_50k person records.
   This does NOT test the 1.5B's actual value proposition — zero-shot *cross-domain*
   generalization (held-out walmart 0.795). The late layers may be doing the
   cross-domain abstraction that is invisible on a single in-distribution task.

So the defensible conclusions:
- **Fixed-domain deployment** (you have labels for the entity type you resolve): ship a
  truncated ~8–16-layer backbone + a trained linear match head — a genuinely
  smaller/faster ER scorer at in-distribution F1 ≥ the full model. Real and shippable.
- **Open question (settled below):** does a truncated backbone keep *zero-shot
  cross-domain* F1? (`truncate_adapt.json` on the volume.)

### Truncate + LoRA-SFT, cross-domain — generalization lives in the late layers

The generative test that settles it: take the BASE Qwen2.5-1.5B, keep only layers
0..k-1, LoRA-SFT it on the same corpus + recipe (with a **trainable readout** —
`lm_head`+`norm` in `modules_to_save`, so the generative output can realign to the
truncated residual), then eval in-distribution **and** zero-shot held-out **walmart**.
k=28 is the control (same recipe), so the F1 delta vs a truncated k isolates
truncation. (`modal_train.py::train_truncated_eval`.)

| k | in-distribution F1 | walmart (zero-shot, cross-domain) F1 |
|---|---|---|
| 28 (control) | 0.9996 | 0.488 |
| 16 | **0.9962** | **0.179** |
| 12 | 0.780 | 0.175 |

The result is sharp and resolves the whole strip investigation:

- **In-distribution, truncation is nearly free** — k=16 matches the full model (0.996 vs
  0.9996). (A *frozen*-readout run collapsed even in-distribution to 0.52; that was a
  readout-adaptation artifact — the trainable `lm_head` recovers it, confirming the
  information is present early, exactly as the linear probe said.)
- **Cross-domain, truncation is catastrophic** — walmart F1 collapses **0.488 → 0.179**
  at k=16 *while in-distribution is fully preserved*. The late ~12 layers (17–28) are
  **inert for in-distribution F1 but load-bearing for zero-shot transfer**.

**So: generalization lives in the late layers.** This reconciles every earlier number.
The logit-lens (25%), the linear probe (71% @ L8), and the in-distribution truncation
(k=16) were all *in-distribution* measurements — and in-distribution the decision is
computed early, so most of the depth *looks* strippable. But the same late layers that
add no in-distribution F1 are doing the cross-domain abstraction that is the whole point
of a zero-shot matcher. "Parameters that don't influence *this* outcome" are not
"parameters that don't influence outcomes" — they carry out-of-distribution generality.

Practical consequences:
- **Fixed-domain deployment** (you have labels for the entity type you resolve): a
  truncated ~16-layer model (−43% depth) matches the full model on your distribution —
  a real smaller/faster ER scorer, *if* you never leave that distribution.
- **General zero-shot matcher** (the 1.5B's actual value): keep the depth. Stripping the
  late layers trades away exactly the transfer ability that justifies the model.

Caveat on absolutes: the k=28 control scores walmart 0.488, below the shipped model's
0.795, because this sweep trained on the 2,844-row synthetic-only corpus in the
checkout, not the shipped model's ~17,690-row multi-source corpus. The internal *delta*
(0.488 → 0.179 as depth drops, in-distribution held) is the clean signal; a
multi-source-corpus rerun would raise the absolutes but is expected to show the same
collapse pattern. (`eval_trunc{28,16,12}_v2.json` on the volume.)

## Manual control — can we tweak the model via the direction? (measured: yes, but…)

Since the match direction is causally locked, we CAN steer the model's verdict at
inference (Grade 1) or bake the shift into weights (Grade 2) — no retraining. The
natural product is a **precision/recall leniency dial**: steer +c to merge more,
−c to be stricter. `modal_interp.py::leniency_dial` measures whether steering the
model's *internal decision axis* beats a plain **threshold** on its output.
Per-layer directions from a record-disjoint TRAIN split; on TEST (1000 pairs):

| control | P | R | F1 |
|---|---|---|---|
| steer c ≤ −0.5 | 0.00 | 0.00 | 0.00 (reject-all) |
| **steer c = 0** (unsteered) | 0.997 | 0.786 | **0.879** |
| steer c ≥ +0.5 | 0.746 | 1.00 | 0.855 (accept-all) |
| **best threshold sweep** | — | — | **0.943** |

**Steering is a validated but BLUNT control — a threshold is strictly better.** At the
gap-unit scale that moves the decision, multi-layer steering saturates: it flips from
reject-all to accept-all between c=0 and c=±0.5, with almost no usable middle, and it
never beats thresholding at matched recall (0/13 points). The cleanest statement: the
best "steer" point is c=0 (no steering) at F1 0.879, and from there the only thing that
*improves* the operating point is moving the threshold (→0.943), not steering (→0.855).

Why: a threshold operates on the model's own *calibrated confidence ranking* — the
optimal way to pick an operating point from a scorer. Steering is a coarser, uncalibrated
mid-network shove that can reorder borderline pairs against that ranking, so its frontier
is dominated. **So manual tweaking is real, but a P/R dial is the wrong job for it — use
a threshold.** Steering/weight-editing earns its keep only where a threshold *can't* reach:
changing behavior when the output probability isn't exposed, or baking a fixed shift into
a binary-only deployment. (`leniency_dial.json` on the volume.)

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
modal run scripts/er_matcher/interp/modal_interp.py::layer2 --layer 14   # Layer 2
modal run scripts/er_matcher/interp/modal_interp.py::strip                # strip probe
```

Layer-2 pure helpers (`field_attribution.py`) are unit-tested model-free in
`scripts/er_matcher/test_field_attribution.py`.
