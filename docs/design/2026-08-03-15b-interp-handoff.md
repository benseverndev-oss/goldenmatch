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
  **surname ~0.04 and dob ~0.01**), cross-validated by an independently
  trained SAE basis. `modal_interp.py::layer2_abstraction` + the pure/tested
  `scripts/er_matcher/interp/field_attribution.py`. **R² = 0.51 against the internal
  projection**; for faithfulness against the model's actual verdict — the number that
  matters for the explainer — see the faithfulness section below.
- **Faithfulness measurement — committed.** `modal_interp.py::faithfulness_eval`
  measures the shipped weights against the model's real P(match) on a cluster-disjoint
  split. Replaces the lost `scratchpad/faithfulness.py`; see the results section below.
- **Causal attribution — committed + wired to the product.**
  `modal_interp.py::causal_attribution` ablates each field and measures the real verdict
  flip; `LocalLlamaAdapter.score_and_explain(..., counterfactuals=True)` surfaces it per
  decision. Stronger audit artifact than any R², and it **generalizes to the messy
  product domain** where the weight table collapses — but it **contradicts two of the
  shipped field weights**. See its section below.
- **Shipped product — the per-decision explainer.** `goldenmatch/core/er_matcher/
  explainer.py` + `LocalLlamaAdapter.score_and_explain`. Model-free by default
  (jaro-winkler + the learned weights), schema-agnostic, with an opt-in
  `counterfactuals=True` path that re-scores per field on the live model. Ships TWO
  labelled tables:
  `PERSON_FIELD_IMPORTANCE` (scoring: does agreement track the verdict?) and
  `PERSON_FIELD_CAUSAL_RANKING` (necessity: does the model need the field?), which
  disagree on purpose. Faithfulness now the measured **0.27** (was 0.51, the projection
  number), or **0.33** in the opt-in `high_faithfulness=True` mode. 42 unit tests.
  Additive; `score_pair` unchanged.
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
  `causal_attribution_{hard,random,walmart_amazon}_seed{0,1,2}.json`,
  `direct_attribution_L14.json`,
  `faithfulness_{cluster,record}-disjoint_{hard,random}_seed{0..4}[_logit].json`,
  `faithfulness_deepmatcher-train-test_walmart_amazon_seed0.json`. Modal token is set
  locally. The product runs read the already-fetched DeepMatcher tables at
  `/out/magellan/<dataset>` (cite-only license, never committed).
- **Pinned GGUF** for on-box work: `scratchpad/er-1p5b.gguf` (Q4, the same weights as
  `/out/model_1p5b/merged`). llama.cpp only exposes the final layer — GPU/Modal needed
  for residual-stream + hooks.
- **Data:** `scripts/autoconfig_quality/vendored/historical_50k.parquet` (person, the
  probe set); training corpus `data/er_matcher/*.jsonl` (2,844 synthetic-only — NOT the
  shipped model's ~17,690-row multi-source corpus; matters for fair strip comparisons).

## Faithfulness hardening — result (0.51 measured the wrong target; 0.87 did not survive)

The 0.51 that `explainer.py` used to publish is the R² of explaining the internal
diff-of-means **projection** — a lossy 1D shadow of the ~8D decision, and the wrong target for a
per-decision explainer. The right target is the model's **actual P(match)**.

That is now measured by a **committed, reproducible** stage —
`modal_interp.py::faithfulness_eval` (pure helpers + 41 harness tests + a 34-test parity gate in
`field_attribution.py` / `test_field_attribution.py`) — on a **cluster-disjoint** split
(no entity shared train↔test), the fp16 `/out/model_1p5b/merged`, teacher-forced
`{"match":` → softmax(true,false) readout. Artifacts:
`interp/faithfulness_{cluster,record}_{hard,random}_seed{0..4}[_logit].json`
(`split="record"` reproduces the weaker split for comparison; `link="logit"` the
alternative link — see the logit section below).

| basis (linear link) | **cluster** / hard s0 | cluster / hard s1 | cluster / random | **record** / hard | record / random |
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
shipped weights land in **0.21–0.58**, and on the honest split in the discriminative
look-alike regime — precisely where the explainer runs, since a review queue *is*
look-alikes — it is **0.27 ± 0.07** (5 seeds, linear link). The published **0.51 is
therefore optimistic, not pessimistic, for the regime the explainer actually runs in**;
it is defensible only as a whole-corpus figure. `explainer.py` left unchanged pending
the decision below, but this is the number to revisit first.

### Logit link — tested, and it does not help (a wrong prediction, corrected)

This doc previously predicted that a *linear* link to a near-bimodal target was the
wrong functional form and that a logit link "would likely raise all four rows." **That
was wrong.** `faithfulness_eval` now takes `link="linear"|"logit"` (fit against
log-odds, scored back in *probability* space so the two stay comparable). Five seeds on
the honest config (cluster-disjoint / hard negatives):

| basis | linear (mean ± sd, n=5) | logit (mean ± sd, n=5) |
|---|---|---|
| **`fixed`** — shipped weights, frozen | **0.27 ± 0.07** | 0.21 ± 0.15 |
| `simple` | 0.30 ± 0.06 | 0.26 ± 0.14 |
| `richer` | 0.64 ± 0.08 | **0.73 ± 0.12** |
| `gbm` | 0.77 ± 0.06 | 0.77 ± 0.10 |

The logit link **lowers** the two rows that matter and roughly **doubles their
variance** — `fixed` swings 0.015→0.391 across seeds under logit vs 0.215→0.364 under
linear. In hindsight the mechanism is clear: against a saturated target `logit(p)` is
dominated by the clipped extremes, so a frozen 1-D score regressed on log-odds becomes
hostage to how many pairs land on the clip boundary. Only `richer` benefits (36 features
can absorb it); `gbm` is unchanged. **Keep `link="linear"` as the default.**

**The bigger lesson from this sweep: single-seed numbers here are not publishable.**
Seed-to-seed spread exceeds the linear-vs-logit effect, and the mined test sets differ
materially in difficulty (P(match) test mean ranges 0.36–0.67 across seeds). The earlier
"0.25–0.32" range for `fixed` came from two seeds and was too tight; over five it is
**0.27 ± 0.07**. Report means over ≥5 seeds here, not point estimates.

**Caveats that remain:** structured person data is the easy case; messy/product domains
untested; and all of the above scores the fp16 model, not the Q4 GGUF that ships.

**Follow-up:** ~~(a) re-measure with a logit link~~ — **done, see above; it does not
help and `linear` stays the default.** (b) decide whether to add a richer/GBM
"high-faithfulness" mode (0.64–0.77 on the honest split, 5-seed means) alongside the
simple/legible one, and whether to report the hard-negative number in the product;
(c) re-measure on a messy/product domain — that is the number a skeptic will ask for;
(d) score the **Q4 GGUF** as the target, since Q4 is what actually ships and it is the
largest remaining unexplained difference vs the old 0.87.

## Per-pair causal attribution — done, and it disagrees with the shipped weights

`modal_interp.py::causal_attribution` (helpers + tests in `field_attribution.py`).
Occlusion, not another R²: blank a field on **both** records and re-score. The prompt
renders an absent value as `(missing)` and the system rubric explicitly trains the model
to treat a missing field as "ignore, do not penalize", so this removes evidence
*in-distribution* rather than poking the model off-manifold. Necessity (leave-one-out)
and sufficiency (leave-one-in) per field, 400 pairs/run, base accuracy 0.88–0.90.
Artifacts: `interp/causal_attribution_{hard,random}_seed{0,1,2}.json`.

Stable across 4 runs (hard s0/s1/s2 + random s0):

| field | causal rank | shipped weight | mean ΔP | flip rate | verdict |
|---|---|---|---|---|---|
| birth_place | **1st, every run** | 0.30 (2nd) | +0.02…+0.04 | 7.5–10% | agrees |
| first_name | 2nd–3rd | 0.42 (1st) | −0.00…−0.02 | 5.5–6% | ~agrees |
| **dob** | **2nd–3rd** | **0.01 (last)** | −0.02…−0.03 | 4.5–5.5% | **under-weighted** |
| postcode_fake | 4th | 0.08 (4th) | +0.00…+0.01 | 3.5–5% | agrees |
| surname | 5th | 0.04 (5th) | +0.00…+0.01 | 2.5–3% | agrees |
| **occupation** | **last, every run** | **0.15 (3rd)** | ~0.00 | 1–2% | **over-weighted** |

Spearman(causal, shipped weights) = **+0.14 … +0.43** — weak, never strong.

**Three findings:**

1. **The signs are coherent and readable.** On look-alikes, removing `first_name` or
   `dob` *raises* P(match) (they carry the evidence AGAINST a match — the
   discriminators), while removing `birth_place` or `surname` *lowers* it (evidence
   FOR). That is exactly the story you would want an explainer to tell, and it comes
   straight from the model's behaviour.
2. **Two shipped weights are wrong in a user-visible way.** `dob` is documented as
   "near-ignored" at 0.01 but is a top-3 causal field; `occupation` sits at 0.15 (3rd)
   but is dead last causally, flipping 1–2% of verdicts. An explanation that tells an
   auditor "the model ignores date of birth" is, by the ablation test, false.
   *Fair caveat:* occlusion measures necessity **given the other five fields**, while
   the Layer-2 coefficients measure contribution to the internal direction under a
   standardized regression — under redundancy these genuinely differ, so this is not
   simply "the weights are broken". But for a user-facing claim about what drives a
   decision, ablation is the more defensible ground truth.
3. **The decision is highly redundant — no single field is usually necessary.**
   Removing *any one* field changes the verdict in only **~18–19%** of pairs. Good news
   for robustness (a missing or corrupt field rarely breaks the model) and it explains
   the low faithfulness R²: the model integrates redundant evidence rather than keying
   on one or two fields. It also bounds the product story — a "removing X flips this
   verdict" counterfactual exists for roughly one decision in five, so per-pair
   attribution belongs on the **review queue**, not on every decision.

### Re-deriving the weights from ablation — TRIED AND REJECTED ON EVIDENCE

The obvious fix was to replace `PERSON_FIELD_IMPORTANCE` with the ablation magnitudes
(normalized mean |ΔP|, 3 hard seeds → `birth_place` 0.27, `first_name` 0.21, `dob` 0.18,
`postcode_fake` 0.15, `surname` 0.12, `occupation` 0.07; a random-negative run agrees to
±0.01). It was implemented, then checked with `faithfulness_eval` — and **it made the
explanation measurably worse**:

| weights | held-out R² vs real P(match), 5 seeds | mean |
|---|---|---|
| original (direction regression) | 0.251 / 0.317 / 0.364 / 0.215 / 0.214 | **0.27** |
| ablation-derived | 0.164 / 0.162 / 0.283 / 0.013 / **−0.124** | **0.10** |

One seed goes *negative* — worse than predicting the mean. **Reverted.**

**Why, and the lesson:** the two measures answer different questions and only one suits
the scoring job. Ablation asks *"does the model need this field?"* — a marginal,
necessity-given-the-rest quantity, which under a redundant decision is small and nearly
flat across fields. `explain_pair` needs *"does this field's agreement level track the
verdict?"*, which is the regression quantity. Flattening the weights toward the ablation
profile pushes the score toward an unweighted mean of agreements and discriminates less.

**What shipped instead:** both, kept separate and labelled — `PERSON_FIELD_IMPORTANCE`
(scoring, unchanged) and the new `PERSON_FIELD_CAUSAL_RANKING` (necessity). The wrong
*claim* is fixed without corrupting the scoring: the code and docs no longer say the
model ignores dob, and a test locks the two rankings apart so they can't be silently
merged. `PERSON_IMPORTANCE_FAITHFULNESS_R2` is now **0.27** (was 0.51 — the projection
number), and the rationale text no longer says "decision geometry".

## Messy domain (walmart_amazon) — the correlational story collapses, the causal one holds

The number a skeptic asks for. Both stages now take `--dataset walmart_amazon` and run
on DeepMatcher's **own train/test splits** (pre-labeled pairs, so no negative mining and
no cluster leak to worry about — the benchmark's split *is* the honest split). Same
model, same code path as person; only the pairs differ. Test accuracy 0.95–0.96.

**Faithfulness — the shipped feature basis does not survive the move:**

| basis | person (cluster/hard) | **walmart_amazon** |
|---|---|---|
| `simple` — one jaro-winkler agreement per field | 0.300 | **0.024** |
| `richer` — 36/30 features | 0.667 | 0.508 |
| `gbm` | 0.750 | 0.529 |

`simple` explains **~2%** of the model's verdict on product data. The `fixed` row is
absent by design: `PERSON_FIELD_IMPORTANCE` is person-only, and faking a weight table
for a product schema would be dishonest.

**Why, and it's fixable:** `richer` recovering to 0.51 shows the signal is there — what
fails is specifically *whole-string jaro-winkler per field*. `"Sony 60GB PS3"` vs
`"PlayStation 3 60 GB Sony"` is a match with low string similarity. Product fields need
token-level/semantic agreement features, not a single fuzzy-string score.

**Causal attribution — generalizes cleanly, with no tuning:**

| field | rank | mean ΔP | flip rate | reading |
|---|---|---|---|---|
| **title** | 1st | **+0.069** | 7.5% | evidence FOR a match (removing it lowers P) |
| **modelno** | 2nd | −0.035 | 8.3% | the discriminator (removing it raises P) |
| price | 3rd | −0.030 | 4.8% | discriminator |
| category | 4th | −0.006 | 4.3% | weak |
| brand | 5th | −0.005 | 3.3% | weak |

That is the domain-correct story — titles carry the match evidence, model numbers and
prices are what rule a pair out — recovered from the model's behaviour with zero
person-specific machinery.

**Two conclusions that should steer the product:**

1. **The causal/counterfactual route is the one that generalizes; the weight-table route
   is domain-brittle.** A per-field weight table has to be re-derived per schema and its
   simple-agreement basis silently degrades to ~0 on messy text. Ablation needs no
   per-schema calibration and produced a sensible ranking on a domain it had never seen.
   Prefer counterfactuals as the primary explanation and weights as the cheap
   person-data fallback.
2. **The ~18–19% redundancy constant holds across both domains** (person 0.175–0.195,
   walmart 0.180). Independent evidence that this is a property of how the model
   decides, not an artifact of the person corpus — and it means the low faithfulness R²
   was never going to be fixed by better weights.

## Token-aware agreement — 6× on messy text, zero cost on person

The walmart result above was a *feature-basis* failure, so the basis was fixed.
`explainer.field_agreement` is now `max(jaro_winkler, token_agreement)`, where
`token_agreement` splits on alphanumeric boundaries (`"60GB"` → `["60", "gb"]`,
`"PS3"` → `["ps", "3"]`) and takes token containment for multi-token values, Jaccard
when either side is single-token.

`max` is the whole design: token overlap can only ever ADD agreement a string metric
missed, never remove it. That is what protects person data, where a token-sort metric is
known to collapse under corruption (`project_ncvr_recall_regression`).

**Measured both ways before and after** (`simple` = the shipped basis):

| | before | after |
|---|---|---|
| walmart_amazon `simple` | 0.024 | **0.149** (6×) |
| walmart_amazon `richer` | 0.508 | **0.549** |
| person `fixed` (5-seed mean) | 0.2722 | **0.2726** |
| person `simple` (5-seed mean) | 0.3042 | **0.3044** |

Person is unchanged to three decimals — exactly as the `max` construction predicts,
since single-token person values have no token overlap to gain.

**Do not overstate it.** 0.149 is a 6× improvement on a very low base and still well
short of `richer` (0.549) on the same pairs. The per-field story on messy text went from
useless to weak, not to good. Closing the rest of that gap means richer per-field signals
(the exact/missing/conflict/edit-distance decomposition `richer` already uses), not a
better single scalar. `PERSON_IMPORTANCE_FAITHFULNESS_R2` stays **0.27**.

### Basis parity — the harness measures what the product ships, and it's gated

Every faithfulness number in this doc is a claim about the **shipped** explainer, which
is only true while the harness and the product compute the same basis. That is now a
guarantee rather than a convention:

- **The basis lives in the shipped module.** `explainer.FIELD_SIGNAL_NAMES` +
  `field_signal_vector()` own the six-signal decomposition (agreement / exact / missing
  / conflict / len_ratio / edit_norm), reusing the same `_CONFLICT_THRESHOLD` the
  rationale renderer uses — so "the explanation called this a conflict" and "the
  conflict feature fired" cannot disagree.
- **The harness injects it.** `faithfulness_eval` passes `field_agreement` into
  `field_agreements(...)` and `field_signal_vector`/`FIELD_SIGNAL_NAMES` into
  `richer_field_features(...)`. The standalone fallbacks in `field_attribution.py`
  exist only so that module stays unit-testable without the package importable.
- **A parity gate enforces it.** `tests/test_er_matcher_basis_parity.py` asserts the
  signal names and conflict threshold match, that the structural signals agree
  value-for-value across the cases that have bitten this thread, that the shipped
  metric never scores *below* the fallback (the "strict improvement, not a trade"
  claim), and — importantly — greps `modal_interp.py` to confirm the injection is
  still wired. Drop the injection and this test fails instead of the numbers quietly
  becoming about a basis nobody ships.

**Unifying the basis also improved the richer rows** (the previous `richer`/`gbm`
figures were computed on a plain-jaro-winkler decomposition the product never used):

| | before | after |
|---|---|---|
| walmart `richer` | 0.508 | **0.549** |
| walmart `gbm` | 0.529 | **0.614** |
| person `richer` (5-seed mean) | 0.643 | **0.671** |
| person `gbm` (5-seed mean) | 0.773 | 0.770 |
| person `fixed` / `simple` | 0.2722 / 0.3042 | 0.2726 / 0.3044 |

No row regressed. `fixed` and `simple` are unchanged because they already ran through
`field_agreement`; `PERSON_IMPORTANCE_FAITHFULNESS_R2` stays **0.27**.

## Two-mode explainer — shipped, and the trade is now measured

Next-step #2 done. The richer decomposition was derived the SAME way the 6-field table
was (regressing the causally-validated direction, `layer2_abstraction`), just onto the
36-signal basis instead of one agreement scalar per field. It explains **0.888 of the
direction vs 0.511** — the scalar was discarding most of the direction's structure. The
6 field coefficients reproduce exactly (0.420/0.305/0.147/0.082/0.038/0.014), confirming
token-aware agreement did not disturb the person derivation.

**Frozen, it roughly doubles output faithfulness** (`fixed_richer` row in
`faithfulness_eval`, 5 seeds, cluster-disjoint, hard negatives):

| basis | per-seed | mean |
|---|---|---|
| `fixed` — 6 field weights | 0.252 / 0.318 / 0.364 / 0.215 / 0.214 | **0.27 ± 0.07** |
| `fixed_richer` — 36 signal weights | 0.436 / 0.678 / 0.540 / 0.478 / 0.499 | **0.53 ± 0.09** |
| `richer` — refit ceiling | 0.669 / 0.754 / 0.618 / 0.600 / 0.715 | 0.67 |

Better on **every** seed, and most of the way to the refit ceiling.

**But it reads badly, and that is the whole reason it is a separate mode.** Summed per
field, the 36 weights rank `occupation` FIRST and `first_name` LAST — near-exactly the
reverse of the ablation ranking, and `occupation` is the field ablation says the model
needs *least*. That is collinearity across 36 correlated signals: individual
coefficients stop being readable long before the fit stops being accurate.

### Sparsity fixed both problems at once

The obvious follow-up — is collinearity the cause rather than information content? — was
run, and the answer is yes. An **L1 fit (alpha=0.05) keeps 14 of 36 signals**, costs
almost nothing in direction-R² (0.867 vs 0.888), and:

| basis | frozen output R² (5 seeds) | rollup vs ablation |
|---|---|---|
| `fixed` — 6 field weights | 0.27 ± 0.07 | readable |
| `fixed_richer` — 36 dense | 0.53 ± 0.09 | **inverted** (ρ = −0.77) |
| **`fixed_sparse` — 14 L1** | **0.64 ± 0.09** | **agrees at both ends** (ρ = +0.43) |
| `richer` — refit ceiling | 0.67 | n/a |

**Sparse beats dense on every seed and essentially reaches the refit ceiling using only
frozen weights.** L1 regularization improved generalization *and* legibility at the same
time: the dense fit was spreading weight over collinear signals that fit the direction
in-sample but did not transfer to P(match). Its rollup now ranks `birth_place` first and
`occupation` last — matching ablation at both ends — and the surviving signals read
coherently on their own (`edit_norm` negative, `exact` positive).

So the two modes did NOT have to stay split on a trade-off; the dense fit was simply
the wrong fit. `PERSON_SIGNAL_IMPORTANCE` is now the sparse table,
`PERSON_SIGNAL_FAITHFULNESS_R2` is **0.64**, and the dense set is retained only as
`PERSON_SIGNAL_IMPORTANCE_DENSE` — the measured comparison that justifies the choice
(`fixed_richer` row), pinned by a test so the reason survives.

### The modes do NOT merge — and the reason is a probe confound, not a fit problem

The "one more attempt" was run: a **two-stage grouped fit** (regress each field's six
signals to a composite, L1-normalize the within-field pattern, then regress the six
composites so the second-stage coefficients ARE the per-field importances by
construction). R² 0.863 — and the rollup came out
`['surname', 'birth_place', 'postcode_fake', 'dob', 'first_name', 'occupation']`,
**identical in ordering to the sparse fit. first_name still 5th.**

Three independent fit types — dense OLS, L1, grouped two-stage — agree. So it is not
collinearity and not the fit. Hunting the confound found it:

| check (probe set, hard negatives, 800 pairs) | value |
|---|---|
| mean `edit_norm` over all six fields, corr with label | **−0.903** |
| `edit_norm` cross-field correlation (mean / max) | +0.382 / +0.626 |
| `agreement` cross-field correlation (mean / max) | +0.217 / +0.368 |
| `surname__edit_norm` vs label | −0.790 |
| `birth_place__edit_norm` vs label | −0.807 |
| `first_name__edit_norm` vs label | −0.710 |

**`edit_norm` is a shared corruption-level proxy.** Probe matches are corrupted copies
of one entity and non-matches are different entities, so overall string distance
separates the classes almost on its own — a single global number gets −0.90. The richer
fits therefore rank whichever field is the best *corruption* proxy (surname,
birth_place) above the field that carries *identity* evidence (first_name).

**Consequences, and they matter more than the 0.64:**

1. **Read the high-faithfulness number as "predicts P(match) on this probe set", not
   as "a better account of the model's field reasoning."** Its 0.64 is real but partly
   earned on a property of how the pairs were mined. This is the same class of finding
   as everything else in this thread; the standing discipline caught it.
2. **Ablation is immune to this and the correlational bases are not.** Occlusion
   perturbs one field at a time on the live model, so a cross-field nuisance correlate
   cannot fool it. That is now a second, independent argument for causal attribution as
   the audit method — it survived the messy-domain move AND it survives this confound.
3. **The two modes stay split**, but for a better-understood reason: not a
   legibility/accuracy trade, but that the accurate basis is measuring something partly
   extrinsic to per-field reasoning. `PERSON_FIELD_IMPORTANCE` remains the prose;
   `explain_pair(..., high_faithfulness=True)` reports 0.64 + `signal_contributions`
   with the per-field prose byte-identical (pinned by a test).
### The de-confounded numbers (corruption-matched pairs)

Done: `faithfulness_eval --corruption-matched` pairs each match with a non-match at the
same corruption level (greedy nearest-neighbour on mean `edit_norm`), so that channel
carries no label signal. Correlation drops **−0.88 → −0.23**; the residual is not zero
because the classes barely overlap in corruption, which is exactly why the shortcut
works. 5 seeds, ~500 train / ~380 test after matching.

| basis | standard probe | **corruption-matched** |
|---|---|---|
| **`fixed` — shipped 6 weights** | 0.27 ± 0.07 | **0.26 ± 0.08** |
| `fixed_richer` — 36 dense | 0.53 ± 0.09 | 0.23 |
| `fixed_sparse` — 14 L1 | 0.64 ± 0.09 | **0.33 ± 0.06** |
| `simple` — refit | 0.30 | 0.37 ± 0.06 |
| `richer` — refit | 0.67 | 0.63 ± 0.05 |
| `gbm` — refit | 0.77 | 0.66 |
| **model accuracy** | 0.88 | **0.72** |

**Three conclusions, and the first is the one that matters:**

1. **The shipped 6-field weights are robust: 0.27 → 0.26, essentially unchanged.** They
   were measuring real per-field evidence all along, not the shortcut. After a day of
   numbers that did not survive scrutiny, the one the product actually ships did.
2. **The high-faithfulness basis was mostly shortcut: 0.64 → 0.33.** Its honest edge
   over the legible table is 0.33 vs 0.26 — real, holds on every seed, but a fraction of
   the 2.4× the raw probe implied. `PERSON_SIGNAL_FAITHFULNESS_R2` corrected to **0.33**.
   The mode is kept because the edge is real, but it no longer justifies itself on
   magnitude; if the two-mode split ever costs anything, retire it.
3. **The MODEL uses the shortcut too** — accuracy 0.88 → 0.72 once corruption is matched.
   So the explainer was not inventing the dependence; it was reflecting a real property
   of how the model decides on this probe. Worth remembering before treating 0.88 as the
   model's discriminative ability: a chunk of it is "these strings are similar overall".

Refit bases hold up (`richer` 0.67 → 0.63) because refitting adapts to the de-confounded
data, whereas the FROZEN weights were derived on confounded pairs and transfer poorly.
That gap between frozen and refit is itself the size of the confound's fingerprint.

**Still open:** untested on walmart — its pairs come from DeepMatcher candidate
generation rather than corruption-based mining, so the mechanism there is likely
different and the product-domain numbers do not inherit this caveat automatically.

Person schema only — the product schema has no derived weights.

## Multi-field ablation — how many fields must go before the verdict moves

Single-field occlusion said "no ONE field decides this" for ~81% of pairs. The sharper
question is whether any *pair* or *triple* does — the difference between a decision that
is decomposable but not 1-sparse (explain it in pairs) and one that is densely redundant
(no small-set attribution exists). `causal_attribution --max-order K` sweeps every
combination up to size K. Person, hard negatives, 400 pairs, all 63 subsets:

| k | cumulative flippable by ≤ k | best combo at that order |
|---|---|---|
| 1 | **0.195** | `birth_place` (0.090) |
| 2 | 0.395 | `dob + birth_place` (0.140) |
| 3 | 0.573 | `first_name + dob + birth_place` (0.255) |
| 4 | **0.815** | `+ postcode_fake` (0.510) |
| 5 | 0.897 | `+ occupation` (0.568) |
| 6 | 0.910 | all six (0.385) |
| — | **0.090 never flipped at any order** | |

**The decision is densely distributed, not small-set attributable.** The curve is close
to linear through k=4 (+0.20, +0.18, +0.24) with no early saturation — each extra field
buys roughly a constant share of pairs, which is the signature of additive evidence
integration rather than a small deciding set. To cover 80% of decisions you need
**four-field** counterfactuals; out of six fields, "remove most of the record" is not an
explanation.

**Two internal consistency checks, both passed:**

- The best triple is exactly `{first_name, dob, birth_place}` — causal ranks 2, 3, 1
  from the single-field sweep. Multi-field agrees with single-field rather than
  contradicting it.
- **Order 6 flips 0.385, and the base MATCH rate is 0.382.** Blanking every field leaves
  no evidence, so the model says no-match, so exactly the match-verdict pairs flip. The
  machinery behaves as it must at the limit.

**Important asymmetry — do not read the 9% as "unexplainable".** Ablation only *removes*
evidence, so it flips match→no-match readily but no-match→match only by deleting the
conflicting fields. Pairs that are no-match for lack of positive evidence cannot be
flipped by removing more. The honest ceiling for this method is therefore below 1.0 by
construction, and it also explains why intermediate orders flip pairs that order 6 does
not (removing the discriminators raises P(match); removing everything drops it again).

### Cross-domain: the low orders replicate, the ceilings do not (and can't be compared)

Same sweep on walmart_amazon (5 fields, DeepMatcher test split, acc 0.96):

| k | person | walmart |
|---|---|---|
| 1 | 0.195 | **0.180** |
| 2 | 0.395 | 0.352 |
| 3 | 0.573 | 0.420 |
| 4 | 0.815 | 0.443 |
| 5 | 0.897 | 0.443 (plateau) |
| never flipped | 0.090 | 0.557 |

**Low-order attributability replicates for the third time**: ~18–20% at k=1 and ~35–40%
at k≤2, on two unrelated corpora with different schemas, different pair-generation
processes, and 5 vs 6 fields. That constant is now the most reproducible number in this
thread.

**The ceilings are NOT comparable, and the difference is mostly base rate.** Ablation
only removes evidence, so match-verdict pairs flip easily while no-match pairs flip only
if their conflicts are deleted. Person is 38% match verdicts; walmart is 14%. The
order-N consistency check holds in both (walmart: all-fields flip 0.107 vs base match
rate 0.141; person: 0.385 vs 0.382), which confirms the mechanism rather than a bug.
Normalizing per verdict class, ~53% of person's no-match pairs flip vs ~35% of
walmart's — a real but much smaller gap than 0.91 vs 0.44 suggests. **Do not cite
walmart as "more redundant" without that normalization**; the right measurement is
flippability split by base verdict, which this sweep does not yet report.

Per-order `any_flip` is legitimately non-monotonic (walmart order 3 = 0.403, order 4 =
0.330) because removing *more* fields can restore the original verdict; the cumulative
curve is monotone, as it must be.

**What this means for the thesis.** This is the measurement that caps the strong
"pause mid-thought and see which circuit fired" claim, at least for this model and task:
for ~80% of decisions no one- or two-field account exists, and the honest per-decision
artifact is "these four fields jointly carry it," which is close to saying "the record
does." Small-set attribution is not merely hard to compute here — the evidence says it
mostly is not there. That is a real constraint on auditable-AI claims and it was
measured by intervention, the one method that has survived every check in this thread.

The cross-domain replication of the k=1 and k≤2 numbers is what makes this a claim about
the models rather than about the person probe. The honest headline: **a single-field
counterfactual exists for roughly one decision in five, in both domains tested.**

## Exact direct attribution — the first mechanistic (not correlational) result

Everything above this section estimates what the model *might* be doing. This states it.

A transformer's residual stream is a **sum** (embeddings + every attention head's output
+ every MLP's output) and the decision readout is a **linear projection** onto the
causally-validated match direction. So each component's contribution is exactly
computable. `modal_interp.py::direct_attribution` decomposes to per-layer-MLP and
per-attention-HEAD granularity (splitting `o_proj` by head — exact, since it is linear
and bias-free): **183 components for the layer-14 readout.**

**Exactness verified, not assumed:** contributions sum to the observed projection with
`max_abs_err = 5.7e-03`, **relative error 5.4e-04** — consistent with fp16 accumulation
over 183 terms. There is no R² here because nothing is fitted; the reconstruction error
is a *correctness check*, and the stage prints a warning and invalidates its own ranking
if it fails.

### Ranking by magnitude was wrong — a methodological catch

The first ranking put `L5.mlp` on top (mean −1.6898). But its `mean_abs` was **also**
1.6898 — identical, meaning it contributes the same sign and magnitude on *every* pair.
That is a constant offset, not decision logic. Ranking by magnitude conflates "large
fixed bias" with "carries the decision."

**98 of 183 components are near-constant** (std < 5% of |mean|), including most of the
largest by magnitude: `L5.mlp` (var/mag 0.010), `L6.mlp` (0.014), `L3.mlp` (0.015),
`L12.attn.h9` (0.030). Most of the projection's *magnitude* is an operating-point offset;
the decision lives in a much smaller varying part.

### Re-ranked by variance — where the decision actually varies

| component | mean | std |
|---|---|---|
| **`L13.mlp`** | +1.1598 | **0.1706** |
| `L11.mlp` | −0.2340 | 0.0745 |
| `L12.mlp` | −0.4846 | 0.0600 |
| `L10.mlp` | +0.9821 | 0.0510 |
| `L13.attn.h11` | +0.2636 | 0.0470 |
| `L13.attn.h9` | +0.6052 | 0.0378 |
| `L13.attn.h5` | +0.2351 | 0.0329 |

**The varying computation concentrates in layers 10–13** — independently matching the
~layer-13 formation point found by the per-layer probes, by a completely different
method. Two independent routes to the same answer is the strongest internal
corroboration in this thread.

### Ranked by VARIANCE SHARE -- the correct metric, and it finds a circuit

`std` was also wrong: it measures variability, not decision-relevance. The right metric
rests on an identity rather than a heuristic. Because the projection **is** the sum of
the contributions,

    var(proj) = cov(sum_j c_j, proj) = sum_j cov(c_j, proj)

each component's covariance with the projection is its **exact additive share of the
decision's variance**, and the shares must sum to 1 -- a second correctness check
(measured 1.000083; the 8e-5 deviation is fp16 accumulation over ~200 terms, the same
source as the reconstruction error, so the tolerance is set to fp16 reality not 1e-6).

| top-k by variance share | cumulative |
|---|---|
| 1 | 0.256 |
| 3 | 0.422 |
| 5 | 0.541 |
| 10 | 0.731 |
| 20 | 0.894 |
| **21 of 183** | **0.90** |

**21 components carry 90% of the decision variance** -- against 89 by std and 96 by
magnitude. A ~4x reduction purely from measuring the right thing.

| component | var share | label corr |
|---|---|---|
| **`L13.mlp`** | **+0.256** | +0.837 |
| `L11.mlp` | +0.097 | +0.799 |
| `L12.mlp` | +0.068 | +0.671 |
| `L13.attn.h11` | +0.067 | +0.781 |
| `L13.attn.h9` | +0.052 | +0.739 |
| `L10.mlp` | +0.051 | +0.594 |
| `L13.attn.h5` | +0.046 | +0.776 |
| `L10.attn.h0` | +0.035 | +0.731 |
| `L13.attn.h7` | +0.034 | +0.741 |

The structure is coherent: **late MLPs (10-13) plus a cluster of layer-13 attention
heads {5, 6, 7, 8, 9, 11}**, all with high label correlation (0.6-0.84) -- so they move
the decision *and* move it correctly, which `std` alone could not distinguish.

**Suppression is real and measurable.** 59 of 183 components have *negative* share: they
systematically oppose the decision. The strongest (`L5.attn.h1` -0.009, label_r -0.52;
`L12.attn.h10`; `L12.attn.h3`) also anti-correlate with the label. That is why cumulative
share exceeds 1.0 at top-50 (1.044) before the negatives pull it back -- structure, not
error.

### Three metrics, three answers -- the methodological point

| ranking metric | components for 90% | what it actually measures |
|---|---|---|
| mean abs contribution | 96 | magnitude, including constant offsets |
| std | 89 | variability, including decision-irrelevant variation |
| **variance share** | **21** | exact additive share of the decision |

The same exact decomposition underlies all three. **The measurement was never the hard
part; choosing what to rank by was.** My first two readings of this experiment were
wrong, and the earlier "no sparse circuit" conclusion was an artifact of the metric
rather than a property of the model.

### Where it does stay dense

| top-k by std | share of total varying signal |
|---|---|
| 1 | 0.126 |
| 5 | 0.297 |
| 10 | 0.404 |
| 20 | 0.543 |
| 50 | 0.775 |
| **89 of 183** | **0.90** |

By raw magnitude the computation looks dense (96/183), and the *input* dependence
genuinely is dense (multi-field ablation). But the decision-carrying computation is not:
21 components, concentrated in layers 10-13. The head-level "field comparison circuit"
hypothesis is **partly** supported -- the layer-13 head cluster is real -- but late MLPs
carry more of the variance than any single head, so it is not a pure attention story.

**What this establishes.** An exact, complete, per-decision account exists, is cheap,
and **compresses to ~21 components** — Layer 1 in the strict sense, verified to
floating-point twice over (reconstruction, and the share-sum identity). That is a
circuit-sized number and the first genuinely mechanistic result in this thread.

**What it does not establish.** That those 21 components are *human-readable*. Naming
what `L13.mlp` computes is the abstraction-layer problem and it is unsolved — but it is
now a tractable 21-component problem rather than a 183-component one.

**Standing caveat:** these are DIRECT contributions. Indirect effects (one head changing
another's attention pattern) are real causal paths that direct attribution assigns to the
downstream component. Path patching is required for those; a decomposition exact on
direct paths and silent on indirect ones is complete-looking and wrong.

### Circuit validation -- the ranking survives, the CAUSAL claim does not

Variance share says a component correlates with the decision along the direct path. It
does not say the model needs it. `circuit_validation` runs the standard pair of
interventions by MEAN-ablation (not zero -- 98/183 components are near-constant offsets,
so zeroing would destroy the operating point and confound a scale change with an effect),
across four arms plus a sanity arm.

| arm | layer-14 projection std retained | accuracy | verdict agreement |
|---|---|---|---|
| baseline | 1.000x | 0.885 | -- |
| **ablate the 21** | **0.161x** | 0.887 | 0.998 |
| ablate complement (162) | 0.565x | 0.885 | 1.000 |
| ablate random 21 (control) | 0.968x | 0.890 | 0.995 |
| **ablate all 183 (sanity)** | **0.000x** | **0.887** | **0.998** |

**The ranking is validated.** The sanity arm drives projection variance to exactly zero,
proving the intervention lands. Ablating the 21 removes **84%** of the layer-14 decision
variance while ablating a random 21 removes **3%** -- a decisive separation. The 21
components really are the ones carrying that projection.

**The causal claim is refuted.** Ablating the 21 changes accuracy 0.885 -> 0.887 and
leaves 99.8% of verdicts identical. So does ablating **everything** at layers 0-13. The
layer-14 decision direction is *not necessary* for the model's output.

**Why:** the intervention is at the DECISION POSITION only. The evidence lives at the
field-token positions, and layers 14-27 simply re-read it by attention and rebuild the
verdict. The model routes around the ablation entirely.

**This qualifies the Layer-1 "lock" and it is the most important correction in the
thread.** Steering the direction drives P(match) 0->1 (sufficiency: pushing hard along
`d` changes the output). Mean-ablating it changes nothing (no necessity). Those are
different claims, and "causally validated" has been carrying the stronger reading. The
direction is a real, readable, exactly-decomposable *correlate of* the decision at layer
14 -- not a bottleneck the computation must pass through.

**What survives:** the exact decomposition (verified twice), the 21-component ranking
(validated against a control), the layer 10-13 concentration, and suppression structure.
All of that describes the layer-14 readout faithfully.

**What does not:** any claim that explaining layer 14 explains the model's decision.

**Next, and this is now the top of the list:** re-run ablation at ALL token positions,
not just the decision position, and across layers 14-27 as well. If the behaviour still
survives, the decision is genuinely distributed across positions and depth and no
single-site circuit exists. If it collapses, the circuit is real but larger than the
readout site -- and the honest unit of explanation is (position x layer x component),
not component alone.

### Where the decision actually enters -- and the third instance of redundancy

`layer_cutoff_sweep` mean-ablates every head and MLP at the decision position for
layers `< k`, sweeping k. Ablating layers 0-13 does nothing (previous section), and the
logits are a function of the final residual at that position, so k = n_layers must
destroy behaviour. The interesting quantity is where in between.

**Cumulative sweep -- a sharp cliff:**

| ablate layers | accuracy | P(match) std retained | verdict agreement |
|---|---|---|---|
| < 14 | 0.887 | 0.999x | 0.998 |
| < 15 | 0.885 | 0.999x | 0.995 |
| < 16 | 0.877 | 0.978x | 0.993 |
| < 17 | 0.823 | 0.919x | 0.938 |
| **< 18** | **0.500** | **0.226x** | **0.615** |
| < 28 (sanity) | 0.500 | 0.000x | 0.615 |

Behaviour is untouched until layer 16, degrades slightly at 17, and collapses to chance
at 18.

**Isolation control -- and it refutes the single-layer reading:**

| ablate ONLY | accuracy |
|---|---|
| layer 14 | 0.885 |
| layer 15 | 0.882 |
| layer 16 | 0.833 |
| **layer 17** | **0.770** |
| layer 18 | **0.905** (up) |
| layer 19 | **0.920** (up) |

No single layer is the write point. Layer 17 alone costs 11 points -- real, but far from
the collapse to 0.500 that removing 0-17 *together* produces. **The decision is written
into the decision position redundantly across layers ~14-17**; remove any one and the
others carry it, remove all of them and there is nothing left before layer 18. The cliff
is a threshold effect over a redundant group, not a bottleneck.

**Two further results:**

- **Ablating layers 18-19 IMPROVES accuracy** (0.885 -> 0.905 / 0.920). Those layers
  actively degrade the verdict on this probe set, consistent with the 59 negative-share
  suppression components found by direct attribution.
- **The whole interpretability effort targeted the wrong layer.** Layer 14 was chosen
  because the direction is first linearly *readable* there. Ablating layer 14 alone
  changes accuracy by 0.000. The load-bearing window is 15-17. "Where a feature first
  becomes readable" and "where the computation is necessary" are 3-4 layers apart in
  this model, and every Layer-2, faithfulness, and attribution number above was measured
  at the readable layer rather than the necessary one.

**Redundancy is now confirmed at three independent granularities:**

| level | finding |
|---|---|
| input fields | a single-field counterfactual exists for ~19% of decisions (both domains) |
| components | 21/183 carry 90% of the layer-14 readout, but ablating all 183 changes nothing |
| layers | no single layer necessary; only a prefix through 17 destroys behaviour |

That consistency is the strongest claim this thread can make, and it is a negative one:
**at every granularity examined, this model has no small necessary set.** Small-set
attribution is not missing for want of better tooling -- three different decompositions,
each validated against a control, agree that it is not there.

## Next steps (highest leverage first)

1. **Find a call site for the ER-matcher explainer.** `score_and_explain` /
   `explain_for_review` are a complete, tested API surface with NO caller — the review
   queue's `why_for_correction` uses the older generic `explain_pair_nl` path instead.
   Wiring the local-model explainer into that path (behind the existing
   `use_llm`-style opt-in) is what turns this work from an available capability into a
   shipped one. Until then "counterfactuals on the review queue" means the method
   exists and defaults correctly, not that reviewers see them.
2. **Derive product-schema signal weights.** The two-mode explainer (above) doubles
   faithfulness on PERSON only, because the 36 weights come from a person-data direction
   regression. walmart has no derived table at all, so its `fixed`/`fixed_richer` rows do
   not exist and the product-domain explanation still rests on the unweighted basis.
   Running `layer2_abstraction` against a product-domain probe set is the missing piece.
3. **Decide whether the high-faithfulness mode earns its keep.** De-confounded it is
   0.33 vs 0.26 for the legible table (above) — a real edge on every seed, but small
   enough that two modes may not be worth the surface area. A product call, not a
   measurement one; the measurement is done.
4. **Redo the attribution at layers 15-17, not 14.** Every number above was measured
   where the direction is first READABLE; ablation says the load-bearing window is
   15-17, and ablating layer 14 alone changes accuracy by 0.000. Re-running
   `direct_attribution` and `layer2_abstraction` at layer 17-18 is the single highest-
   value repeat, because it targets the computation that matters rather than its
   earliest shadow.
5. **Name what `L13.mlp` computes.** It alone carries 25.6% of the decision variance at
   label-correlation 0.84. An SAE or transcoder on that one MLP is now the highest-value
   interpretability target in the model — one component rather than 183.
6. **Split flippability by base verdict.** The multi-field ceilings are confounded by
   class balance (person 38% match verdicts, walmart 14%) because ablation can only
   remove evidence. Reporting the curve separately for match- and no-match-verdict pairs
   makes the two domains comparable and is a small change to `ablation_flip_profile`.
7. **Perf for real volume.** Prefix-cache the system rubric (biggest CPU win) + wire the
   logit readout into the scorer for clean P(match); benchmark on GPU.
8. **Benchmark head-to-head, honestly.** Against the ER landscape on *held-out* data
   (walmart, not the contaminated in-training sets), reporting competitive-local, not
   SOTA. This is the step that turns "appears to address" into "demonstrably addresses."
9. **Multi-source-corpus rerun** of the truncate/strip sweep so the walmart absolutes
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
