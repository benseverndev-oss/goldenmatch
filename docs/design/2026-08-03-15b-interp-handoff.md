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
  number), or **0.64** in the opt-in `high_faithfulness=True` mode. 42 unit tests.
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
4. **Open:** re-measure faithfulness on pairs whose corruption level is matched across
   classes, which would strip the shortcut and give the honest richer-basis number.
   Untested on walmart — its pairs come from DeepMatcher candidate generation rather
   than corruption-based mining, so the mechanism there is likely different.

Person schema only — the product schema has no derived weights.

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
3. **Strip the corruption shortcut and re-measure.** The grouped fit settled that the
   modes will not merge by better fitting (above) — the richer basis partly rides a
   cross-field corruption proxy that is an artifact of probe mining. Re-run
   `faithfulness_eval` on pairs matched for corruption level across classes; that number
   is the honest one for the richer basis, and it will be lower than 0.64.
4. **Perf for real volume.** Prefix-cache the system rubric (biggest CPU win) + wire the
   logit readout into the scorer for clean P(match); benchmark on GPU.
5. **Benchmark head-to-head, honestly.** Against the ER landscape on *held-out* data
   (walmart, not the contaminated in-training sets), reporting competitive-local, not
   SOTA. This is the step that turns "appears to address" into "demonstrably addresses."
6. **Multi-source-corpus rerun** of the truncate/strip sweep so the walmart absolutes
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
