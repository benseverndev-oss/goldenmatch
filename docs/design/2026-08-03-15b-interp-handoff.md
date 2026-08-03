# Handoff — mechanistic interpretability of the 1.5B ER-matcher

**Status:** active research thread on PR **#2369** (draft, green, mergeable). This
doc orients a new owner; the blow-by-blow with all numbers lives in
`docs/design/2026-08-02-15b-decision-geometry-layer1.md`. Read that for detail; read
this to know where things stand and what to do next.

## The thesis (why this matters)

Production ER has largely not adopted AI for the *core match decision* for three
reasons: **cost**, **explainability/auditability**, and **privacy** (can't send PII
to an API). This work pursues a coherent answer to all three: a **local, cheap,
mechanistically-explainable** ER scorer. Classical methods already hit ~97% F1 on
clean structured PII, so the AI wedge is the *hard* cases (messy text, product/
cross-source, low overlap) — which is exactly where a local explainable scorer helps.

**Honest positioning (do not overclaim):** the local 1.5B is *competitive-local*, not
SOTA. The only clean held-out product number is **walmart_amazon zero-shot F1 0.795**
(beats DeepMatcher 0.669; below per-dataset-tuned Ditto 0.868). Every "too good"
number this thread produced turned out to be a confound (training-set contamination,
gold-column leakage, cross-process nondeterminism, in-distribution-only strip). The
standing discipline: **when a number looks too good, hunt the confound first.**

## What's done and shipped

- **Layer 1 — locked (causal).** The "same-entity" decision is a low-dimensional (~4–8D)
  **linear direction** in the residual stream, formed by ~layer 13, and **causally
  validated**: multi-layer steering of that direction drives P(match) 0→1 monotonically
  (ablation collapses it). Pipeline: `scripts/er_matcher/interp/modal_interp.py` stages
  `probe_layers` → `sae` → `causal`.
- **Layer 2 — the human translation.** The proven direction decomposes into human
  field-importance (**first_name 0.42, birth_place 0.30**, occupation/postcode small,
  **surname ~0.04 and dob ~0.01 = near-ignored**), cross-validated by an independently
  trained SAE basis. `modal_interp.py::layer2_abstraction` + the pure/tested
  `scripts/er_matcher/interp/field_attribution.py`. **R² = 0.51 against the internal
  projection**; for faithfulness against the model's actual verdict — the number that
  matters for the explainer — see the faithfulness section below.
- **Faithfulness measurement — committed.** `modal_interp.py::faithfulness_eval`
  measures the shipped weights against the model's real P(match) on a cluster-disjoint
  split. Replaces the lost `scratchpad/faithfulness.py`; see the results section below.
- **Shipped product — the per-decision explainer.** `goldenmatch/core/er_matcher/
  explainer.py` + `LocalLlamaAdapter.score_and_explain`. Pure/model-free (jaro-winkler
  + the learned weights), schema-agnostic, honest about the R²=0.51 bound. 10 unit
  tests. Additive; `score_pair` unchanged.
- **Live demo (artifact).** Real records + the actual model's verdicts, each explained
  by learned field importance. Built from `scratchpad/xai_demo.py`.

## Key findings (with the caveats that keep them honest)

1. **Stripping / compression — "it depends what you optimize."** In-distribution the
   decision is computed early, so most depth *looks* strippable (logit-lens 25%,
   linear-probe 71%, generative truncate to ~16 layers keeps in-dist F1 0.996). **But
   cross-domain it collapses** (truncate+LoRA-SFT: walmart 0.488→0.179 at k=16 while
   in-dist stays perfect). **Generalization lives in the late layers.** So: a smaller
   *fixed-domain* model is real (−43% depth); a smaller *general* model needs
   distillation, not truncation. Caveat: the strip control trained on the 2,844-row
   synthetic-only corpus in the checkout (walmart absolutes low, 0.488); the internal
   delta is the clean signal — a multi-source-corpus rerun would lift absolutes.
2. **Manual tweaking (leniency dial) — steering works but a threshold is better.**
   Steering the causal axis as a precision/recall knob is bang-bang (reject-all ↔
   accept-all) and never beats a plain threshold at matched recall (best steer F1
   0.879 vs best threshold 0.943). Manual tweaking is real, but a P/R dial is the wrong
   job for it. Steering/weight-editing earn their keep only where a threshold can't
   reach (no exposed probability, binary-only deployment).
3. **Perf — the readout is correct but modest on CPU (a correction).** The
   teacher-forced logit readout (feed `{"match":` prefix, read `true`/`false` logits)
   gives **identical verdicts** to generation and a **clean continuous P(match)** —
   worth it for score quality. But the CPU speedup is only ~**1.5×** (generation
   5.3s/pair vs readout 3.5s on this box) because **prefill dominates**; the "10–15×"
   was a decode-heavy/GPU figure. Real CPU levers: **prefix-cache the identical system
   rubric** (~150 of ~200 tokens/call) and **GPU offload**. Requires `logits_all=True`
   for the logprobs API (verified working; see `scratchpad/readout2.py`).

## Where everything lives

- **PR #2369** (branch `claude/parity-cascade-queue-merge-x6twuo`): the explainer +
  `field_attribution.py` + all `modal_interp.py` stages + `modal_train.py::
  train_truncated_eval` + design note + this handoff.
- **Modal** app `goldenmatch-er-matcher-interp` (+ `goldenmatch-er-matcher-train`),
  volume **`er-matcher-out`**. fp16 model at `/out/model_1p5b/merged`. Artifacts on the
  volume: `layer_probes.json`, `sae_layer14.pt`, `causal_multilayer_8_20.json`,
  `layer2_abstraction_L14.json`, `layer_early_exit.json`, `truncate_adapt.json`,
  `eval_trunc{28,16,12}_v2.json`, `leniency_dial.json`,
  `faithfulness_{cluster,record}_{hard,random}_seed{0,1}.json`. Modal token is set locally.
- **Pinned GGUF** for on-box work: `scratchpad/er-1p5b.gguf` (Q4, the same weights as
  `/out/model_1p5b/merged`). llama.cpp only exposes the final layer — GPU/Modal needed
  for residual-stream + hooks.
- **Data:** `scripts/autoconfig_quality/vendored/historical_50k.parquet` (person, the
  probe set); training corpus `data/er_matcher/*.jsonl` (2,844 synthetic-only — NOT the
  shipped model's ~17,690-row multi-source corpus; matters for fair strip comparisons).

## Faithfulness hardening — result (0.51 measured the wrong target; 0.87 did not survive)

The 0.51 in `explainer.py` is the R² of explaining the internal diff-of-means
**projection** — a lossy 1D shadow of the ~8D decision, and the wrong target for a
per-decision explainer. The right target is the model's **actual P(match)**.

That is now measured by a **committed, reproducible** stage —
`modal_interp.py::faithfulness_eval` (pure helpers + 25 unit tests in
`field_attribution.py` / `test_field_attribution.py`) — on a **cluster-disjoint** split
(no entity shared train↔test), the fp16 `/out/model_1p5b/merged`, teacher-forced
`{"match":` → softmax(true,false) readout. Artifacts:
`interp/faithfulness_{cluster,record}_{hard,random}_seed{0,1}.json` (`split="record"`
reproduces the weaker split for comparison).

| basis | **cluster** / hard s0 | cluster / hard s1 | cluster / random | **record** / hard | record / random |
|---|---|---|---|---|---|
| **`fixed`** — SHIPPED weights, frozen (intercept+scale only) | **0.251** | **0.317** | 0.495 | 0.485 | 0.577 |
| `simple` — same 6 features, weights refit | 0.300 | 0.329 | 0.501 | 0.525 | 0.579 |
| `richer` — 36 features (exact/missing/conflict/edit/len) | 0.667 | 0.767 | 0.747 | 0.771 | 0.775 |
| `gbm` — gradient boosting on the richer features | 0.750 | 0.810 | 0.837 | 0.870 | 0.842 |

**The three findings that matter:**

1. **Freezing the shipped weights costs almost nothing** (+0.01–0.05 vs refitting the
   same basis, in every cell). The causally-derived weights are ~as good as a fresh fit
   — the explainer's central claim holds up. The binding constraint is the **feature
   basis and the linear link**, not the frozen weights: richer features roughly double
   R² in the honest column.
2. **A record-disjoint split leaks, and the leak is worth ~+0.22 on the linear rows.**
   Holding model / features / seed / negatives fixed and changing *only* the split,
   `simple` goes 0.300 → 0.525 and `fixed` 0.251 → 0.485. In `historical_50k` a cluster
   is one entity with several corrupted records, so a record-disjoint split still puts
   **the same entity on both sides**; the fit learns that entity's agreement→P(match)
   mapping in train and is then scored on it in test. **Use `split="cluster"` for any
   number you intend to publish.**
3. **Even so, the earlier 0.871 / 0.967 / 0.984 do NOT reproduce.** Stacking *both*
   known weakenings (record split **and** random negatives — the most favorable
   configuration tested) still gives simple **0.579**, richer **0.775**, gbm **0.842**.
   The split leak explains a large share of the gap but not all of it. The residual is
   most likely the target model and small-n: the original scored **400 pairs total**
   at ~2.4 s/pair on CPU, i.e. the **Q4 GGUF via llama.cpp** (~200 train / 200 test),
   where a 36-feature linear fit and a GBM reporting held-out 0.967 / 0.984 is a
   small-n red flag on its own. `scratchpad/faithfulness.py` no longer exists on any
   box (it lived in the session `Issues.md` teleports to), so this cannot be closed
   directly. **Treat 0.87 / 0.97 / 0.98 as unreproduced and do not cite them.**

   *Fair caveat in the other direction:* scoring the **Q4 GGUF** is arguably the more
   product-relevant target, since the shipped local scorer is the quantized model. That
   is a defensible difference of target, not simply an error — but quantization alone is
   an implausible explanation for moving a linear R² from 0.58 to 0.87, and it remains
   untested here.

**Consequence for the shipped explainer:** do **not** raise
`PERSON_IMPORTANCE_FAITHFULNESS_R2` to 0.87. Across every configuration tested the
shipped weights land in **0.25–0.58**, and on the honest split in the discriminative
look-alike regime — precisely where the explainer runs, since a review queue *is*
look-alikes — it is **0.25–0.32**. The published **0.51 sits mid-range and is
defensible as-is**; if anything it is optimistic for the hard regime rather than
pessimistic. `explainer.py` left unchanged pending the decision below.

**Caveats on these numbers too:** structured person data is the easy case; P(match) is
near-bimodal (82% of hard-negative test pairs sit outside [0.1, 0.9]), and a *linear*
link to a bimodal target is a poor functional form — a logit link would likely raise all
four rows and is the fairer metric to add next. Messy/product domains untested.

**Follow-up:** (a) re-measure with a logit link before publishing any headline number;
(b) decide whether to add a richer/GBM "high-faithfulness" mode (≈0.67–0.75 on the
honest split) alongside the simple/legible one, and whether to report the hard-negative
number in the product; (c) re-measure on a messy/product domain — that is the number a
skeptic will ask for; (d) optionally score the **Q4 GGUF** as the target to close the
last of the gap to the old number, since Q4 is what actually ships.

## Next steps (highest leverage first)

1. **Faithfulness, two modes.** Ship (a) the simple/legible explainer (done) and (b) a
   richer GBM/SHAP mode for a higher, honest R² (measured ≈0.75–0.84 — decide against
   the legibility cost). First re-run `faithfulness_eval` with a **logit link**; the
   current linear-link numbers understate every row against a near-bimodal target. For
   the audit story, add **per-pair causal attribution** (ablate each field's residual
   contribution, measure the real verdict flip) — that's a direct causal claim, not an
   R², and it's what a compliance buyer wants. It costs extra forward passes → run it on
   the review queue, not every decision.
2. **Perf for real volume.** Prefix-cache the system rubric (biggest CPU win) + wire the
   logit readout into the scorer for clean P(match); benchmark on GPU.
3. **Benchmark head-to-head, honestly.** Against the ER landscape on *held-out* data
   (walmart, not the contaminated in-training sets), reporting competitive-local, not
   SOTA. This is the step that turns "appears to address" into "demonstrably addresses."
4. **Multi-source-corpus rerun** of the truncate/strip sweep so the walmart absolutes
   match the shipped model (expected: same collapse pattern, higher baseline).

## The one-paragraph version

We causally mapped the 1.5B's match decision to a low-dim linear direction, translated
it into human field weights, and shipped a per-decision explainer whose frozen weights
are measurably ~as good as a fresh fit (R² 0.25–0.32 against look-alikes, 0.50 against
random negatives, explaining the model's real P(match)) —
addressing the explainability blocker in a way standard post-hoc XAI does not. Cost/
privacy are addressed by the *local + banded* deployment (not "LLM on everything for
free"); naive truncation does **not** yield a smaller *general* model because the late
layers carry cross-domain generalization. The thesis (cheap + local + explainable ER)
is a credible, evidence-backed "this could matter" — not a proven revolution — and the
work that would make it defensible is an honest head-to-head benchmark plus faithfulness
hardening on more than one domain.
