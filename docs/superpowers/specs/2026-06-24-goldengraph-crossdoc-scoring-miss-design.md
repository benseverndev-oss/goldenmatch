# goldengraph — cross-doc SCORING-miss lever (scoping)

**Status:** scoping / design — measure-first (measurement dispatched)
**Date:** 2026-06-24
**Author:** measure-driven loop
**Parent:** `2026-06-24-goldengraph-synthesis-lever-design.md` (synthesis lever was a negative result; pivoted here)

## 1. Why — the BROKEN-CHAIN bucket + a confound I have to surface

The traced N=50 run (28121496629) loss histogram:

| stage | count | % |
|-------|-------|---|
| SYNTHESIS | 25 | 50% |
| **RETRIEVAL-BROKEN-CHAIN** | **12** | **24%** |
| EXTRACTION | 11 | 22% |
| RETRIEVAL-BUDGET | 2 | 4% |

BROKEN-CHAIN = the gold answer IS in the graph but in a DIFFERENT connected
component from the question's seeds, so the multi-hop chain is severed before
retrieval runs. The shatter-probe verdict: **`{SCORING-miss: 11, RECALL-miss: 1}`**
— 11 of 12 are high-similarity pairs across a component boundary
(`cosine=1.000 fuzzy=100 shared_token=True`, e.g. "Fujian"/"Fujian") that a
cross-doc matcher *could* merge to connect the chain.

### The confound: cross-doc linking was OFF in every measurement so far

The bench defaults `cross_doc_link=false` and `profile_link=false`, and none of the
runs in this whole loop set them. **So the goldenprofile anti-shatter matcher
(#1249) was never running** — the graph carried only within-document resolution,
which is why it shattered into **~620 connected components** (top sizes
`[2356, 95, 57, …]`). The shatter-probe's "SCORING-miss" is therefore a
*hypothesis* ("a matcher could merge this pair"), NOT the goldenprofile matcher's
actual decision (it didn't run).

**This means the BROKEN-CHAIN bucket (24%) is mostly "linking was off," not
"the matcher is broken."** The honest first step is to turn linking ON and
re-measure — not to tighten a matcher that wasn't executing.

## 2. Step 1 (DISPATCHED) — measure with linking ON

A traced N=50 run with `cross_doc_link=true profile_link=true literal_attrs=true`
is running. The reads:
- Does the component count collapse (≈620 → tens)?
- Does the BROKEN-CHAIN bucket shrink, and into which bucket does it move
  (hits, or SYNTHESIS, or BUDGET)?
- Headline judge/answer_match vs the linking-off baseline (0.34 / 0.30).

History caveat: an earlier cross-doc measurement (pre-budget-raise, older main)
came in **flat** on answer_match (0.18/0.18/0.16). This re-measures on current main
(post #1241 budget raise, #1249 matcher, literal attrs) — the config has moved, so
the flat result is not assumed to still hold.

## 3. Step 2 (CONTINGENT) — if SCORING-misses persist with linking ON

If, with the matcher running, identical/near-identical-name pairs are STILL left
in separate components, the cause is the goldenprofile **hard gate**
(`score.rs::score_pair`):

```
gated_in = (category_lex >= category_gate (0.60)
            OR category_gate_cos >= category_embedding_gate (0.85))
           AND name >= name_gate (0.80)
```

For "Fujian"/"Fujian", `name = 1.0` clears `name_gate`, so the pair scores 0 ONLY
when **category_ok fails** — the two mentions carry lexically-divergent category
labels AND their category embedding cosine < 0.85. #1249 added the
category-embedding escape hatch, but it only fires when (a) a category-only
embedding is supplied AND (b) the synonym categories actually embed close. A
near-exact NAME match is itself strong identity evidence that the current gate
does not let override a category disagreement.

**Candidate fix:** an exact/near-exact-name override of the category gate —
`if name >= exact_name_gate (≈0.97): category_ok = true`. Rationale: two entities
with (near-)identical surface names are far more likely a synonym-category bridge
("Fujian" province vs region) than a cross-sense collision; the cross-sense risk
the category gate guards ("Apple" company vs fruit) involves DIFFERENT senses that
rarely share an (near-)exact name. This is a precision/recall trade to MEASURE,
not assume — over-merge risk is real for genuinely ambiguous exact names (e.g.
"Lincoln" the person vs the city), so the override must be gated high and measured
against the engine's parity suite + the bench.

This stays a scoping hypothesis until Step 1 shows the matcher-on shatter actually
persists.

## 4. Success criterion

Component count collapses and the BROKEN-CHAIN bucket shrinks **without an
over-merge regression** — watched on the bench (judge/answer_match must not drop)
AND the goldenprofile parity/precision tests (no spurious cross-sense merges). A
naive "merge all same-name pairs" would tank precision; the win is connecting the
severed chains the trace flagged while holding the Row-4 (Nabbes/Shakespeare) and
cross-sense ("Apple") guards #1249 established.

## 5. Plan

1. **Measure linking ON** (dispatched) — read component count + stage histogram.
2. If BROKEN-CHAIN largely resolves → the lever was a config default; consider
   whether `cross_doc_link`/`profile_link` should be the bench default, and move on
   to the next-largest bucket.
3. If SCORING-misses persist → implement the exact-name category-gate override in
   `score.rs`, gate it high, add parity tests, and A/B on the bench + an over-merge
   check.

## 6. Note on the synthesis bucket (still the largest)

SYNTHESIS (50%) remains the biggest bucket but the A+B levers were a measured
negative (see parent doc) — it's a multi-hop *reasoning* failure, harder to move.
The cross-doc lever is taken first because it is more mechanical and measurable;
synthesis may warrant a different attack (e.g. a stronger reasoning model for the
synthesis call only, or a verify-then-answer two-pass) later.
