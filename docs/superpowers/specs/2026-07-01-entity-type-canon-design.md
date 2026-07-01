# Homograph-Safe Entity-Type Canonicalization — Design

**Date:** 2026-07-01
**Status:** Design (approved for spec)
**Builds on:** `GOLDENGRAPH_XDOC_KEY` (PR #1331) — the type-agnostic cross-doc merge key that lifted substrate R(B) 0.23→0.75.
**Report context:** `docs/superpowers/reports/2026-07-01-substrate-recall-type-jitter.md`.

## Problem

`GOLDENGRAPH_XDOC_KEY=name_ci` recovers cross-document recall by dropping `typ` from the merge key — the fix for the 7B's per-document type jitter. But dropping type entirely is **homograph-unsafe**: two genuinely different entities that share a surface name (`Apple` the company vs `Apple` the fruit; `blocking` the ER technique vs `Blocking` a person) collapse into one node. The engineered corpus has unique concept names, so this precision risk is currently **invisible** — `name_ci` measured P(B)=0.89–0.94 only because no homographs exist to conflate.

This variant keeps the recall win while restoring a *coarse* type discriminator to the key, so homographs of different coarse types stay separate.

## Approach (decided)

- **Coarse type via extraction-time closed vocab** (not post-hoc embedding-NN or LLM classification — those are deferred). Constrain the extractor to emit a type from a small fixed set, mirroring the existing `_RELATION_VOCAB_INSTRUCTION` predicate constraint.
- **Homograph safety validated by extending the engineered generator** with a gated homograph injection — the corpus must *contain* homographs before we can claim to handle them.

Both were selected in brainstorming; the alternatives (embedding-NN / LLM classify; hand-built fixture) are recorded under Deferred.

## Architecture

Source-side, gated, deterministic. text → **extract (type constrained to the closed vocab)** → resolve → **`_key_payload(name_ci_type)` → `(name_ci, canonicalize_entity_type(typ))`** → store overlap-merge. Homographs of different coarse types produce different keys → they never merge.

## Components

### 1. Closed entity-type vocab
- `GOLDENGRAPH_ENTITY_TYPE_VOCAB` (comma-separated, mirrors `GOLDENGRAPH_RELATION_VOCAB`).
- Default coarse set constant in `goldengraph/schema.py`: `person, organization, location, concept, work, event, product, other`.
- **Granularity is the central tuning knob** — too fine → the 7B jitters *within* the vocab and recall re-fragments; too coarse → homographs sharing a coarse class still collide. Configurable so the eval can sweep it.

### 2. Extraction-time constraint
- New `_ENTITY_TYPE_VOCAB_INSTRUCTION` in `extract.py`, prepended to the prompt (exact analog of `_RELATION_VOCAB_INSTRUCTION`), listing the allowed types and instructing "pick the single closest".
- Gated by `GOLDENGRAPH_ENTITY_TYPE_CANON=1`; open extraction when off. Composes with the existing relation-vocab instruction.

### 3. Deterministic safety net — `canonicalize_entity_type(raw, vocab)`
- Pure function in `schema.py`. Case-fold + strip; exact vocab match → that entry; a small substring/keyword map for common off-vocab prose (`*technique|method|algorithm|process|index|measure|metric* → concept`; `*company|corp|inc|university|lab* → organization`; etc.); else `other`.
- **Load-bearing:** a 7B ignores the vocab constraint some fraction of the time, and a single stray raw type reintroduces jitter. The safety net makes the *key* robust even when extraction is unconstrained, so the key mode is useful independent of the prompt constraint.
- Pure + goldenmatch-free → unit-tested without the fingerprint.

### 4. Cross-doc key mode — `name_ci_type`
- `resolve._key_payload` gains `name_ci_type`: `{"name": name.strip().lower(), "typ": canonicalize_entity_type(typ, vocab)}`. Same single chokepoint all call sites already route through.
- `GOLDENGRAPH_XDOC_KEY` values become: unset (default `(name,typ)`) / `name` / `name_ci` / `name_ci_type`.

### 5. Homograph corpus — engineered generator extension
- `GOLDENGRAPH_BENCH_HOMOGRAPH=k`: after entity load, pick `k` disjoint pairs of gold entities and force them to render under the **same** surface name while carrying **different** gold coarse types. Deterministic for a seed.
- Gold mentions (`emit_gold_mentions`) already read `(entity_id, surface, doc_id)` off the documents, so the injected collision is captured correctly: the two entities keep distinct `entity_id`s (gold says "different") but share a surface (so `name_ci` wrongly merges them).
- The eval then scores: `name_ci` **loses precision** on these pairs (false co-reference); `(name_ci, coarse_type)` **holds** it because the coarse types differ.

## Data flow / failure modes it fixes

| case | `name_ci` | `name_ci_type` |
|---|---|---|
| same entity, type jitter across docs (`schema matching`: Process/Algorithm/…) | merges ✓ (coarse type identical after canon) | merges ✓ |
| homograph, different coarse type (`Apple` org vs fruit) | **wrongly merges ✗** | stays separate ✓ |
| homograph, *same* coarse type (two `concept`s named `blocking`) | wrongly merges | still merges (documented limit; needs context/neighborhood, out of scope) |

The third row is the honest boundary: a coarse-type key cannot separate same-coarse-class homographs — that needs the ER scorer (the deferred "profile-link on top of name_ci" follow-on), not a key.

## Validation / gate

Two substrate-eval runs, `name_ci` vs `name_ci_type`, on two corpora:

1. **Standard engineered corpus (recall parity):** `name_ci_type` R(B) must stay ≈ `name_ci` R(B) (within ~0.05 of 0.75 at ambiguity=0). A drop means the vocab is too fine and the 7B jitters within it.
2. **Homograph corpus (precision recovery):** `name_ci` P(B) must visibly **drop** (proves the corpus exercises the risk); `name_ci_type` P(B) must **hold** near the non-homograph level. That delta is the deliverable.

Gate: `name_ci_type` recall within 0.05 of `name_ci` on standard **and** precision strictly better than `name_ci` on homograph.

## Scope

**v1:** vocab + extraction constraint + `canonicalize_entity_type` safety net + `name_ci_type` key + homograph generator + the two-corpus eval. **Default-off.**

**Deferred (YAGNI / measure-first):**
- Embedding-NN or LLM type classification (the other two coarse-type mechanisms) — revisit only if extraction-constraint under-recovers.
- Any default flip of `GOLDENGRAPH_XDOC_KEY` — stays gated until measured across real corpora.
- Same-coarse-class homograph disambiguation — needs the ER scorer, tracked as the separate "profile-link on top of name_ci" follow-on.
- Real-corpus homograph validation.

## File plan

- `goldengraph/schema.py` — `DEFAULT_ENTITY_TYPE_VOCAB`, `entity_type_vocab()`, `canonicalize_entity_type(raw, vocab)`, `entity_type_canon_enabled()`.
- `goldengraph/extract.py` — `_ENTITY_TYPE_VOCAB_INSTRUCTION`; prepend it in `extract()` when the gate is on.
- `goldengraph/resolve.py` — `_key_payload` `name_ci_type` branch (reads the vocab lazily).
- `benchmarks/.../qa_e2e/engineered.py` — `GOLDENGRAPH_BENCH_HOMOGRAPH` injection.
- Tests: `test_xdoc_key.py` (name_ci_type payload), `test_entity_type_canon.py` (canonicalize_entity_type), `test_substrate_eval.py`/generator test (homograph gold correctness).

## Testing

Box-safe pure tests for `canonicalize_entity_type` (exact/substring/fallback/case-fold), the `name_ci_type` payload, and the homograph generator's gold (two entity_ids, one surface, distinct coarse types). One Modal run for the recall-parity + precision-recovery table.

## Risks

- **7B non-compliance with the vocab** — mitigated by the deterministic safety net (the key canonicalizes regardless).
- **Within-vocab jitter re-fragmenting recall** — measured directly by the recall-parity gate; the vocab-granularity knob is the lever if it fails.
- **Coarse vocab too coarse to separate real homograph classes** — surfaced by the homograph corpus; the honest boundary (same-coarse-class) is documented, not hidden.
