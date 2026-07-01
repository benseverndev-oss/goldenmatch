# Recall-Tuned Extraction Prompt — Design

**Date:** 2026-07-01
**Status:** Design (approved for spec)
**Follows:** the L2 clean-absolute finding (PR #1345) — real-prose `coverage` is ~0.44 *even with the best alignment*, so the substrate ceiling on real prose is **extraction recall**: the 7B doesn't extract ~56% of the wikilinked entities from dense Wikipedia sentences. This is the first extraction-recall lever.

## Problem

The extract prompt is **relation-centric** — it frames the task as "extract a knowledge graph" where entities exist to be indexed by relationships. It never instructs exhaustive named-entity extraction, so a wikilinked entity mentioned *without* a clear relation is dropped. `ingest` also does ONE extraction pass per doc, so a dense multi-entity lead yields only the salient subset. This lever tests the cheapest hypothesis first: **is the miss relation-centric framing?** (If not, it's density → chunking, a separate follow-on.)

## Approach (decided)

A gated recall-tuned instruction (`GOLDENGRAPH_EXTRACT_RECALL=1`) prepended to the extract prompt, mirroring the existing `_RELATION_VOCAB_INSTRUCTION` / `_ENTITY_TYPE_VOCAB_INSTRUCTION` gates. Measured on the wiki corpus via `coverage` (the extraction-recall instrument). Both a candidate fix and a diagnostic.

## Architecture

Source-side, gated, prompt-only. text → **extract (recall instruction prepended when gated)** → resolve → store. No new machinery; no goldengraph structural change. Validation reuses the existing `--corpus wiki` eval (aliased aligner, the clean instrument).

## Components

### 1. `_RECALL_INSTRUCTION` + gate (`extract.py`)
- A new constant, e.g.: *"Extract EVERY named entity mentioned — people, organizations, places, products, works — and list it in `entities` even if it does not participate in any relationship. Do not omit an entity just because it lacks a clear relation."*
- Prepended in `extract()` when `GOLDENGRAPH_EXTRACT_RECALL` is truthy (a `extract_recall_enabled()` helper or an inline env read, consistent with the sibling gates). Composes with the relation-vocab / type-vocab instructions.

### 2. Measurement (existing `--corpus wiki` eval)
- Run the wiki substrate eval baseline vs `EXTRACT_RECALL=1`, compare **`coverage`** (the fraction of gold mentions aligned = extraction recall proxy) AND the guardrails **P(B)** + **component count** (over-extraction could add junk entities / fragment the graph).

## Validation / read

Wiki eval, `GOLDENGRAPH_EXTRACT_RECALL` ∈ {off, on}, with `name_ci` (the shipped key):
- **coverage up (0.44 → higher), P(B) holds, components stable** → relation-centric framing WAS the miss; ship the gate (default-off); the clean real-prose absolute rises. Best outcome.
- **coverage flat** → the miss is density, not framing; recall-prompt REFUTED; chunking is the next lever (separate sub-project). A clean negative.
- **coverage up but P(B) drops / components explode** → over-extraction adds noise (spurious entities that fragment the graph); a measured tradeoff, documented, and argues for a more targeted lever.

This is a **calibration**, not a pass/fail gate — report honestly whatever the numbers say.

## Scope

**v1:** the recall-prompt gate + the wiki measurement. **Default-off.** Deferred (escalation path if refuted): sentence/paragraph **chunking** (extract per chunk, union); **GLiNER** NER extractor (`extract_local.gliner_extractor`).

## File plan

- `packages/python/goldengraph/goldengraph/extract.py` — `_RECALL_INSTRUCTION` + gated prepend in `extract()`.
- Test: `packages/python/goldengraph/tests/test_extract_recall.py` — recall instruction present only when gated (capturing-stub LLM, `GOLDENGRAPH_EXTRACT_JSON_MODE=0` to force `.complete`, like `test_entity_type_constraint.py`).

## Testing

Box-safe: the instruction is prepended when `GOLDENGRAPH_EXTRACT_RECALL=1`, absent by default; composes with other prepends. One Modal wiki run (baseline vs recall) for coverage. The measurement uses the aliased aligner (#1345), so the impl branch rebases onto main-with-#1345 before the run.

## Risks

- **Over-extraction noise** — exhaustive extraction may surface spurious/partial entities that fragment the graph or dilute precision. The wiki P(B) + component count are the guardrails; if they degrade, the lever is a tradeoff, not a win.
- **Prompt-constraint unreliability** — the arc's lesson (`SCHEMA_CANON` / entity-type) is that prompt instructions on a 7B are hit-or-miss; a flat coverage result is an expected, informative outcome (→ chunking), not a failure of the experiment.
