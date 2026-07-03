# schema_canon "self-harm" — REFUTED (correction)

**Date:** 2026-07-02
**Corrects:** the SP-C suggester smoke verdict (`2026-07-02-suggester-smoke-verdict.md`) follow-on #2, which listed "`schema_canon` self-harms even with a complete, correct vocab (drops recall)."

## The finding was a misattribution

The SP-C smoke's proposed config that lost recall (relational R 0.672 → 0.540) bundled **three** levers: `name_ci_type` + `entity_type_canon` + `schema_canon`. I attributed part of the recall loss to `schema_canon` **without isolating it** — a bundled-config delta read as a single-lever effect.

## Isolation (systematic-debugging)

**Box-safe (free):** the `RelationSchema` built from the LLM's proposed vocab (`acquired, located in, part of, works at, authored`) matches **all 18/18** distinct rendered predicate surfaces of the homograph engineered corpus (the vocab maps exactly onto the `_ENGINEERED_ALIASES` keys; the substring fallback even catches the homograph appositive-cue forms). So the schema drops **zero** gold predicates by construction.

**Modal ablation (confirming):** homograph engineered corpus, ambiguity=0, `GOLDENGRAPH_LLM_SEED=42`, `name_ci` fixed — the ONLY difference is `schema_canon` + the complete vocab:

| | baseline (`name_ci`) | +`schema_canon` |
|---|---|---|
| relational F1 | 0.7368 | **0.7823** (+0.045) |
| relational recall | 0.6720 | **0.7426** (+0.071) |
| relational precision | 0.8153 | 0.8264 |
| **edge_recall** | 0.8849 | **0.8849 (identical)** |

## Verdict

**`schema_canon` does NOT self-harm — with the correct complete vocab it HELPS** (F1 +0.045, recall +0.071) and drops **zero** edges (`edge_recall` byte-identical). It canonicalizes predicate variants + fixes reverse-direction phrasings → a more consistent graph → better cross-doc clustering (consistent with the original SCHEMA_CANON arc win).

**Corrected attribution of the SP-C recall drop:** it is **entirely `name_ci_type`** (the known type-jitter recall cost, #1335). `schema_canon` was actually *offsetting* it (+0.071 recall), so the bundled config's net recall loss means `name_ci_type`'s cost was even larger than the headline number suggested.

## Lesson

**Do not attribute a bundled-config delta to a single lever without isolating it.** The cheap box-safe schema check (predicates kept/dropped) should have preceded the claim. The self-verify guardrail was never at risk — the accepted config is genuinely good (the `name_ci_type` precision win is real; `schema_canon` is a bonus, not a harm). The one real follow-on left from the SP-C arc is **auto-beta** (perceive homographs/ambiguity → default beta<1, measured).

(Data: `data/2026-07-02-schemacanon-ablation-{baseline,schemacanon}.md`.)
