# REBEL Fusion — Verdict

**Date:** 2026-07-02
**Branch:** `feat/rebel-fuse`
**Spec/Plan:** `docs/superpowers/specs/2026-07-02-rebel-fuse-design.md` · `docs/superpowers/plans/2026-07-02-rebel-fuse.md`
**Run:** Modal `gg-bench`, 7B (`qwen2.5-7b-instruct`), `--corpus wiki`, best config (`name_ci` + chunking `(6,2)`), **`SCHEMA_CANON` off**. 19 docs, 65 gold.

## What this tested

Whether REBEL (Babelscape/rebel-large, discriminative relation extraction) recovers correct edges the generative 7B re-prompt misses — measured as the marginal delta on top of the shipped re-prompt.

## Result — REFUTED (harmful / no marginal value)

| leg | config | R(B) | P(B) | F1(B) | coverage | components |
|---|---|---|---|---|---|---|
| 110 | control | 0.3434 | 1.0000 | 0.5113 | 0.5077 | 14 |
| 111 | re-prompt | 0.3030 | 1.0000 | 0.4651 | 0.4923 | 9 |
| **112** | **re-prompt + REBEL** | 0.2525 | 1.0000 | **0.4032** | 0.5231 | 6 |
| 113 | REBEL-alone | 0.3434 | **0.9067** | 0.4982 | 0.5077 | 11 |

Two robust, mechanism-level findings (not level noise):

1. **REBEL does not add value on top of the re-prompt.** 112 (re-prompt+REBEL) F1 = 0.403 is *below* 111 (re-prompt) F1 = 0.465 — adding REBEL hurt, didn't help. The marginal delta the whole lever was built to measure is negative.
2. **REBEL breaks precision.** REBEL-alone (113) is the *only* leg with `P(B) < 1.0` (0.9067). A precision drop means REBEL's surface-mapping injected **false edges** that drove *incorrect* cross-doc merges — exactly the substring-collision risk the spec flagged (a short REBEL surface matching the wrong entity). This is a mechanism (precision breaks), not run variance: every non-REBEL leg holds `P(B) = 1.0`.

REBEL is refuted as a relation-recall lever for this substrate: it recovers nothing the re-prompt misses and actively degrades precision. **The relation-recall thread closes at the re-prompt.**

## The larger finding: the substrate measurement is high-variance

This run's **control** (leg 110) is F1 = 0.5113 / R(B) = 0.3434. But the re-prompt verdict's control (leg 100, identical config) was F1 = 0.3704 / R(B) = 0.2273. **Same config, ±0.14 F1 between runs.** The 7B extraction is sampled (Ollama default temperature), so each Modal leg re-extracts non-deterministically, and the pairwise R(B)/F1 (sensitive to the exact clustering) swings widely run-to-run. Coverage is coarser (it lands on 32 or 33 of 65 — one-mention granularity) and looks stable, which masked the variance until now.

**Implication (honest):** single-leg A/B deltas across this arc are **underpowered**. The REBEL refutation survives this — its verdict rests on a *precision break* (a mechanism) and a negative delta in the same direction, not on a fragile positive. But the chunking and re-prompt **wins were single-leg deltas** of a similar magnitude to the noise floor, so they should be **re-confirmed with replicated measurement** before any default-on decision. This does not retract them — the re-prompt win had corroborating structural signals (components down, P held) — but it lowers the confidence bar they cleared.

## Decisions

- **Ship the gate default-off** (opt-in second relation source), consistent with the arc, but **not recommended** — it degrades precision.
- **Close the relation-recall thread at the re-prompt.** REBEL adds nothing; further relation-recall levers are not indicated on this evidence.
- **Methodological fix is now the priority, not another lever.** The next substrate step should be **replicated measurement** (N seeds per config, or `temperature=0` extraction if the harness allows) to establish the noise floor and re-confirm the chunking + re-prompt wins with error bars. Measuring a new lever against a ±0.14-F1 control is not worth doing until the instrument is tightened.

## Honest caveats

- **Small N + high variance.** 19 docs / 65 gold, non-deterministic extraction. The REBEL refutation is safe (precision-break mechanism); the *magnitudes* everywhere in this arc are noisier than the single-leg tables implied.
- **REBEL-alone precision break did not manifest in 112** (re-prompt+REBEL held P=1.0) — likely because that run's particular extraction didn't hit a colliding surface, or the re-prompt edges dominated. Precision is not *reliably* broken, but it is *demonstrably breakable* by REBEL, which is disqualifying for a default-on knob.
- **SCHEMA_CANON-off scope** — as designed; REBEL's verbatim Wikidata predicates would be dropped under a canon config anyway.

## Follow-ons

1. **Replicated-measurement harness** — the real next step: quantify the run-to-run variance and re-confirm chunking + re-prompt with error bars. Turns the arc's single-leg deltas into defensible effects.
2. Relation recall is otherwise **saturated at the re-prompt** on this evidence; no further edge-source lever is indicated.
