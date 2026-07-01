# Recall-Tuned Extraction Prompt — Verdict

**Date:** 2026-07-01
**Branch:** `feat/extract-recall-prompt`
**Spec/Plan:** `docs/superpowers/specs/2026-07-01-extract-recall-prompt-design.md` · `docs/superpowers/plans/2026-07-01-extract-recall-prompt.md`
**Run:** Modal `gg-bench` (7B), `--corpus wiki`, `name_ci`, surface aligner (the *delta* is aligner-independent).

## What this tested

The L2 clean-absolute finding put the real-prose substrate ceiling at extraction recall (~0.44 coverage — the 7B doesn't extract ~56% of wikilinked entities from dense prose). First lever, cheapest-first: does a **recall-tuned prompt** (`GOLDENGRAPH_EXTRACT_RECALL=1`, "extract EVERY named entity even without a relationship") lift extraction recall? Also a diagnostic: is the miss relation-centric *framing* (fixable by prompt) or *density* (needs chunking)?

## Result — REFUTED (counterproductive)

| leg | coverage | R(B) | P(B) | components |
|---|---|---|---|---|
| control (no recall) | 0.4000 | 0.2323 | 1.0 | 12 |
| `EXTRACT_RECALL=1` | **0.3538** | 0.1616 | 1.0 | **25** |

The recall prompt made every substrate signal **worse**: coverage −0.05, R(B) −0.07, components **doubled** (12→25). Precision held (1.0).

**Mechanism:** "extract EVERY entity" pushed the 7B to *list more entities* at the expense of *extracting relations*. The substrate build is edge-centric (a node exists via the edges it participates in; the aligner keys on edge `source_refs`), so fewer edges → fewer gold mentions alignable (coverage down) and the extra entity-only nodes fragment the graph (components up). The prompt **traded edges for entity noise.**

## Conclusion

- **The real-prose miss is NOT relation-centric framing.** Exhaustive-entity prompting is the wrong lever — it degrades the substrate. A clean negative, consistent with the arc's prior that 7B prompt-constraints are hit-or-miss.
- **The gate ships default-off** as a documented, opt-in recall-over-precision knob (legitimate for a NER-style goal), NOT recommended for the substrate.
- **Next lever: chunking.** Extract per sentence/paragraph and union — preserves *both* entities and relations per chunk (unlike this prompt, which sacrificed relations), directly attacking the one-pass-over-a-long-dense-doc problem. That is the next sub-project.

## Follow-ons

1. **Sentence/paragraph chunking** — the next extraction-recall lever (preserves edges).
2. **GLiNER NER extractor** (`extract_local.gliner_extractor`) — high-recall entities + LLM relations (hybrid), if chunking under-delivers.
3. Re-confirm any positive lever on the aliased aligner (this run used the surface aligner; the delta is aligner-independent, but absolutes should be read on the clean aligner).
