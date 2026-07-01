# Level 2: Real Wikipedia-Prose Substrate Validation — Verdict

**Date:** 2026-07-01
**Branch:** `feat/wiki-prose-substrate`
**Spec/Plan:** `docs/superpowers/specs/2026-07-01-wiki-prose-substrate-design.md` · `docs/superpowers/plans/2026-07-01-wiki-prose-substrate.md`
**Corpus:** `dataset/wiki_corpus.jsonl` — 19 real enwiki tech-company lead sections, 65 wikilink→QID gold mentions, 10 entities co-referenced across ≥2 docs (IBM in 9). Modal `gg-bench` (A10G, `qwen2.5-7b-instruct`).

## What this validates

The final de-risk of the substrate arc: does the `name_ci` fix survive **real sentence extraction** — complex clauses, apposition, distractor entities — vs the clean `"{surface} {rel} {surface}."` rendering used at levels 0/1? Gold is Wikipedia's own `[[Target|Surface]]` wikilinks resolved to QIDs; built-graph nodes are aligned to gold by a new surface+doc matcher (the engineered `src::rel::dst` doc-id oracle doesn't exist on real prose).

## Results — the whole arc

| level | corpus | baseline R(B) | `name_ci` R(B) | P(B) | coverage |
|---|---|---|---|---|---|
| L0 | engineered concepts | 0.23 | 0.75 | 0.94 | oracle |
| L1 | real entities, clean sentences | 0.73 | 0.976 | 0.97 | oracle |
| **L2** | **real Wikipedia prose** | **0.1263** | **0.2323** | **1.0000** | **0.40** |

(L2: 19 docs, 65 gold; components 17 baseline → 15 `name_ci`.)

## Verdict

**1. `name_ci` still helps on real prose — DIRECTION validated.** On real Wikipedia sentences it lifts R(B) **0.1263 → 0.2323 (~1.8×)** with **perfect precision (P(B)=1.0)**. Combined with L0 (0.23→0.75) and L1 (0.73→0.976), the fix now helps at *every* level of realism — engineered concepts, clean real-entity sentences, and real prose. This retires the arc's last standing risk: the type-jitter fix is not a synthetic artifact; it generalizes to real sentence extraction.

**2. The L2 absolute numbers are COVERAGE-LIMITED (0.40) — inconclusive as an absolute, honest as a method boundary.** Only 40% of gold mentions aligned to a built node. The spec's own guard set ≥0.7 for the absolutes to mean anything, so **L2's absolute R(B) is a floor within the aligned subset, not a clean resolution measurement.** The 60% miss has two real causes, both genuine level-2 findings:
   - **Real-prose extraction drops entities** — the 7B extracts far fewer of the wikilinked entities from dense Wikipedia sentences than from a bare template (a true substrate-quality degradation on real prose).
   - **Surface-form mismatch** — the gold wikilink surface (`Big Blue`, `International Business Machines Corporation`) often differs from the form the 7B extracts (`IBM`), so surface+doc alignment misses even when the entity *was* extracted (a limitation of the *method*, not the substrate).

   The baseline-vs-`name_ci` comparison holds *within* the same 40% coverage, so the ~1.8× recall lift is a valid relative signal; the absolute R(B)=0.23 is not a clean number.

## Honest synthesis

The fix generalizes to real prose (direction, precision). The clean *absolute* real-prose number is not achievable with a surface-match aligner — it needs gold mention **offsets** (an entity-linking corpus like AIDA-CoNLL, or Wikipedia wikilink *character spans* rather than just anchor text), so the aligner keys on position, not surface. That is the definitive-L2 follow-on. What we have is enough to close the arc's central question — *the substrate fix is real and generalizes* — while being straight that the real-prose *magnitude* is bounded below by an alignment ceiling, not measured cleanly.

## Follow-ons

1. **Offset-based alignment** (wikilink char-spans / an EL dataset) for the clean absolute L2 number.
2. Larger / multi-domain wiki corpus (this is 19 tech articles).
3. Pronoun/coreference gold (Wikipedia doesn't wikilink pronouns → named-mention co-reference only).
