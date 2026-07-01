# Real-Corpus Substrate Validation — Verdict

**Date:** 2026-07-01
**Branch:** `feat/real-corpus-substrate`
**Spec/Plan:** `docs/superpowers/specs/2026-07-01-real-corpus-substrate-design.md` · `docs/superpowers/plans/2026-07-01-real-corpus-substrate.md`
**Run:** Modal `gg-bench` (A10G, `qwen2.5-7b-instruct`), substrate eval, 48 real entities (`records.csv`: Wikidata / RxNorm / event ids), `GOLDENGRAPH_BENCH_ENTITIES=real`, full ambiguity sweep.

## What this validates

The entire substrate arc — the R(B)=0.23 floor, the type-jitter root cause, the `name_ci` 0.23→0.75 win — was measured on the synthetic **engineered concept corpus**. The step-back flagged two risks: (1) unvalidated on real entities, (2) the all-concept corpus is plausibly a *worst case* for type-jitter (abstract entities, effectively one type → maximal jitter). This run swaps in 48 real entities with real types (org/drug/place) and real aliases, and re-runs the same eval.

## Results — R(B), real entities vs engineered

| ambiguity | baseline (name,typ) **real** | `name_ci` **real** | baseline **engineered** | `name_ci` **engineered** |
|---|---|---|---|---|
| 0.0 | **0.7303** | **0.9760** | 0.2314 | 0.7475 |
| 0.3 | 0.3872 | 0.5514 | 0.1126 | 0.4282 |
| 0.6 | 0.2096 | 0.3111 | 0.0743 | 0.3502 |

(Real, ambiguity=0: baseline P(B)=0.968 / 1 component; `name_ci` P(B)=0.966, F1(B)=0.971, A−B gap **−0.014** — the built graph *beats* the type-blind resolver-in-isolation.)

## Verdict — two findings, both important

**1. `name_ci` GENERALIZES to real entities — and does even better.** On real entities `name_ci` lifts R(B) 0.7303 → **0.9760** at ambiguity=0 (near-perfect cross-doc resolution), and helps at every ambiguity level (0.39→0.55, 0.21→0.31). The fix is **not** a concept-corpus artifact: real entities still carry residual cross-doc type-jitter, and `name_ci` closes it. The ceiling is actually *higher* on real entities (0.976 vs the engineered 0.75) — real names have less residual noise once type-jitter is removed. This retires the step-back's biggest risk: **the substrate fix is validated on real entities.**

**2. The engineered corpus OVERSTATED the baseline severity.** The real `(name,typ)` baseline is **0.73** at ambiguity=0 — vs the engineered corpus's 0.23. Real entities (IBM, NATO, warfarin, drug brands, places) have crisp, stable types, so the 7B types them consistently and the plain baseline *already* resolves most of them. The dramatic engineered 0.23→0.75 headline was, in the baseline term, a property of the abstract all-concept domain (where every entity is a fuzzy "concept" and jitter is maximal). The **value** of `name_ci` holds (0.73→0.976); the **starting floor** it was rescuing from was domain-specific.

Honest synthesis: *the fix is real and generalizes; the concept corpus was a worst-case stress test that made the baseline look more broken than it is on real, crisply-typed data.* Both halves matter — the first says ship the fix with confidence, the second recalibrates how we quote the headline (name_ci is a smaller relative win on real data, but a higher absolute ceiling).

## Ambiguity note

At ambiguity 0.3/0.6 the real R(B) drops (0.55, 0.31 with `name_ci`) — this is **real alias variance** (IBM ↔ International Business Machines ↔ IBM Corp.), not type-jitter, and `name_ci` can't merge distinct surfaces. That residual is the genuine surface-variance frontier (the deferred profile-link-on-`name_ci` lever), now shown to be real on real aliases too.

## Scope / boundary

Validates real *entities / types / aliases*, **not** real sentence prose — edges + rendering stay synthetic (semantically-empty `X {rel} Y` over real entities). So this confirms the type-jitter fix survives a real entity/type distribution; a **real Wikipedia-prose run (level 2)** is still the remaining de-risk before the substrate story is fully closed. Deferred (unchanged): real-homograph `name_ci_type` validation (`Georgia`→country/state is in the data), real Wikidata edges, real prose.
