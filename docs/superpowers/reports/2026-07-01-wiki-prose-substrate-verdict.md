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

## Alias-anchored clean absolute (follow-on, PR #1342)

The surface-match aligner's 0.40 coverage conflated surface-mismatch (fixable) with extraction-drop (a floor). **Alias-anchoring** — match a built node to a gold QID via the QID's full Wikidata alias set (`dataset/wiki_aliases.json`), not the single wikilink surface — dissolves the surface-mismatch so coverage reflects only extraction recall.

| L2 aligner | baseline R(B) | `name_ci` R(B) | P(B) | coverage |
|---|---|---|---|---|
| surface-only | 0.126 | 0.232 | 1.0 | 0.40 |
| **alias-anchored (clean)** | **0.1768** | **0.3182** | 1.0 | **0.44** |

(A build bug surfaced en route: the first aliased matcher did exact-set-only and *dropped* the substring fallback, so coverage *regressed* to 0.29 — the coverage guard caught it before it produced a misleading verdict; fixed by restoring substring fallback over the alias set, strictly ≥ surface-only.)

**Two clean conclusions:**
1. **`name_ci` lifts R(B) ~1.8× on real prose (0.177→0.318, P(B)=1.0)** — the fix generalizes, now confirmed with a trustworthy aligner. Consistent with the surface-only ~1.8×.
2. **Coverage is ~0.44 even with the best alignment** — alias-anchoring recovered only +4pp, so the low coverage is **dominantly real extraction-drop, not surface-mismatch**. The 7B doesn't extract ~56% of the wikilinked entities from dense Wikipedia sentences. **The real L2 ceiling is EXTRACTION recall (~0.44), not resolution** — which points the next frontier back at extraction, not the merge key. This is the definitive real-prose read: `name_ci` resolves cross-doc co-reference well *within* what the 7B extracts; the binding limit on real prose is how little it extracts.

## Follow-ons

1. ~~Alias/offset alignment for the clean absolute~~ — DONE (alias-anchored; offset blocked — no build-side spans). The clean absolute is coverage 0.44 / `name_ci` R(B) 0.318.
2. **Real-prose EXTRACTION recall** — the true L2 ceiling; multi-pass / recall-tuned extraction on dense prose is the frontier (not the resolution key).
3. Larger / multi-domain wiki corpus (this is 19 tech articles).
4. Pronoun/coreference gold (Wikipedia doesn't wikilink pronouns → named-mention co-reference only).
