# SP-C Suggester Smoke — Verdict

**Date:** 2026-07-02
**Branch:** `feat/substrate-suggest`
**Run:** Modal `gg-bench`, `--eval suggest`, homograph engineered corpus (`GOLDENGRAPH_BENCH_HOMOGRAPH=4`, 139 docs), 7B (`qwen2.5:7b-instruct`), `GOLDENGRAPH_LLM_SEED=42`, reproducible build (#1380 reset).

## What this validated

`suggest_substrate_config` end-to-end: the LLM reads the corpus → proposes `for_profile` flags → the gold self-verify scores `for_profile(flags-off)` baseline vs `for_profile(**flags)` proposed via `build_and_score_real` and accepts only if `_score` (relational F1) improves. Two runs (6-doc sample, then whole-corpus sample).

## Result — the guardrail is proven; the win is metric-gated, not perception-gated

### Run 1 (6-doc sample): guardrail validated
LLM proposed `has_known_schema=True` + `relation_vocab=(works_at, located_in, acquired)` — **incomplete** (missed `part_of`/`authored`; the corpus has 5 relations). `schema_canon` with an incomplete vocab **drops out-of-vocab edges** → F1 collapsed **0.737 → 0.415**. The self-verify **rejected it → deterministic baseline won**. This is SP-C's whole safety guarantee, proven on a real harmful proposal: **the LLM cannot make the substrate worse.**

### Run 2 (whole-corpus sample): perception works; F1 hides the precision win
The 6-doc miss was a **sampling confound**, not a perception limit (4 homograph pairs diluted across 139 docs; both halves rarely in 6 docs; relations under-sampled). With the whole corpus in the prompt:

| | baseline (`name_ci`) | proposed (`name_ci_type` + canon + schema_canon) |
|---|---|---|
| relational F1 | **0.7368** | 0.6723 |
| relational P | 0.8153 | **0.8916** |
| accepted | — | **False** (F1 fell) |

- **Perception validated:** the 7B correctly flagged `expect_homographs=True` AND read the **complete** schema — `relation_vocab=(acquired, located in, part of, works at, authored)` (all 5 real relations) + `entity_type_vocab=(person, organization, concept)`. The LLM's corpus-reading is sound once it sees the corpus.
- **The proposed homograph-safe config did exactly what it's for — raised precision 0.815 → 0.892** (it stopped over-merging the `HGk` homograph pairs, the intended behavior).
- **But F1 fell 0.737 → 0.672**: the recall cost (`name_ci_type`'s ~0.06 recall hit per #1335 + `schema_canon` edge-dropping) outweighed the precision gain, so the **F1-based accept rejected it** → baseline won. The guardrail held again.

## The finding: `_score` = F1 is the wrong accept metric on a homograph corpus

On a homograph corpus the objective is **precision** (don't conflate distinct same-named entities). The proposed config delivered that (P 0.815 → 0.892) — but the F1 accept metric, which also charges the recall cost, **hides the precision win** and rejects the config. So the homograph win is **metric-gated, not perception-gated**: SP-C perceives correctly and proposes the right config; the F1 gate just doesn't reward it. (`_score` was chosen for consistency with SP-B2, reviewer-approved — this smoke is the first evidence it's the limiting factor on homographs.)

## Decision

- **Ship SP-C.** The two things that make it valuable are validated: (1) the **self-verify guardrail** — a harmful LLM proposal was measured and rejected, twice; the LLM can never do worse than deterministic; (2) **LLM perception** — with an adequate sample the 7B correctly detects homographs + the complete schema. Default-off bench/tuning tool; the no-gold MCP surface returns the (unverified) perception.
- **Sampling matters:** the proposer must see enough of the corpus. The runner now samples the whole (small) corpus; for large corpora a representative sample (covering all relation types + homograph pairs) is required, else the vocab is incomplete and `schema_canon` self-harms (Run 1).

## Follow-ons (clear, measured)

1. **Precision-aware / configurable accept metric** (the top lever): on a homograph corpus, accept if precision improves at acceptable recall — not F1 alone. Would have ACCEPTED Run 2's config (P 0.815 → 0.892). This is a small, high-value change to the `_score`/accept path (SP-B2 + SP-C both consume it).
2. **`schema_canon` self-harm even with a complete, correct vocab** (Run 2 still lost recall via schema_canon): worth a separate look — canonicalizing to the right vocab shouldn't drop this many edges. Possibly the 7B renders predicates in forms the closed vocab doesn't match, or a direction-canon interaction.
3. **DeepSeek-V3 ceiling** — not run; the confound was the corpus/sampling + metric, not the model, so V3 on the same corpus/metric wouldn't change the verdict. Worth it only alongside follow-on 1 (a precision-aware gate).

## Honest caveats

- One corpus (engineered homograph), one seed, one 7B. The precision gain (0.815 → 0.892) is a single measured delta.
- The homograph surfaces are synthetic (`HG0`…) with appositive type cues — a fair-ish but not natural homograph test; a natural-homograph corpus (Apple co/fruit) would strengthen the perception claim.

---

## Addendum (2026-07-02): precision-aware F-beta closes the loop

The top follow-on above — a precision-aware accept metric — was built (`_score` → F-beta, env `GOLDENGRAPH_SUBSTRATE_SCORE_BETA`, default 1.0 = F1; spec `docs/superpowers/specs/2026-07-02-precision-aware-score-design.md`). Re-running this exact smoke with **beta=0.5** flips the result:

| | F1 (beta=1.0) | F_0.5 (beta=0.5) |
|---|---|---|
| baseline (`name_ci`) | F1=0.7368 P=0.8153 | — |
| proposed (`name_ci_type`+canon) | F1=0.6723–0.6885 P=**0.892–0.932** | — |
| **accepted** | **False** (F1 fell) | **True** |

Under F_0.5 the proposed homograph-safe config's score (≈0.817) beats the baseline's (0.782), so the self-verify **accepts** it — the winner is `name_ci_type` + `entity_type_canon`, precision **0.815 → 0.932**. SP-C's value is now fully realized and measurement-verified: the LLM perceives the homographs, proposes the right config, and a precision-tuned metric rewards the precision win the F1 default hid — all with the guardrail intact (a truly-worse config would still be rejected at any beta). The homograph win was **metric-gated, and the metric is now tunable.** (Data: `data/2026-07-02-suggest-smoke-beta05.md`.)
