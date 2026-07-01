# Homograph-Safe Entity-Type Canonicalization — Design

**Date:** 2026-07-01
**Status:** Design (approved for spec)
**Hard prerequisite:** `GOLDENGRAPH_XDOC_KEY` / `_key_payload` land in **PR #1331** (branch `feat/xdoc-type-jitter-fix`). They are NOT on `main` yet. Implementation MUST begin only after #1331 merges; the impl branch rebases onto main and **re-verifies** that `_key_payload` is the single cross-doc-key chokepoint (in #1331, `_record_key` routes through `_key_payload` and both `_exact_resolve`/`_fuzzy_resolve` call `_record_key` — confirm this still holds post-merge before adding the `name_ci_type` branch). This spec was reviewed against `origin/main`, where these symbols do not yet exist — that absence is the prerequisite, not a design error.
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

This is the piece the design review flagged as unmeasurable in its first form. A homograph test **only works if the rendered text gives the extractor a basis to type the two same-surface entities DIFFERENTLY** — otherwise the 7B types them identically, `name_ci_type` collapses them exactly as `name_ci` does, and the precision-recovery deliverable is a false negative. Four required parts:

- **Thread the coarse type into the corpus.** `concepts_loader` carries an upstream `entity_type` per concept, but `engineered._Entity` currently drops it (only `id`/`canonical`/`variants`). Add `coarse_type` to `_Entity`, mapped from the upstream type into the closed vocab (§1) via `canonicalize_entity_type`. This is the gold-type channel the current corpus lacks.
- **Inject collisions between DIFFERENT coarse types.** `GOLDENGRAPH_BENCH_HOMOGRAPH=k`: deterministically pick `k` disjoint pairs of entities that (a) both appear as edge endpoints (an edge-less entity emits no docs → no mentions) and (b) have **different** `coarse_type`. Force both to render under one shared surface string (a new token, not either canonical, to avoid colliding with a third entity). The shared surface replaces the rendered surface for BOTH entities across ALL their edge docs (and question mentions), keeping distinct `entity_id`s.
- **Render a type-disambiguating cue.** Homograph docs render with an appositive naming the coarse type — `"{surface}, a {coarse_type}, {rel} {obj}."` (e.g. `"Vertex, an organization, acquired Beats."` vs `"Vertex, a product, is part of the suite."`). This is what lets even a weak 7B assign the two the correct, DIFFERENT coarse type. The cue names the exact vocab word, so the test isolates the KEY's separation behavior, not the extractor's typing acuity (extractor typing consistency is tested separately by the standard-corpus recall arm).
- **Gold is captured for free.** `emit_gold_mentions` reads `(entity_id, surface, doc_id)` off the docs; the two entities keep distinct `entity_id`s under the shared surface, so `name_ci` wrongly co-references them (P(B) drop) and `(name_ci, coarse_type)` keeps them apart (P(B) held). P(B) scores on `entity_id`, so no gold-type plumbing into the eval is needed — only into the rendering.

## Data flow / failure modes it fixes

| case | `name_ci` | `name_ci_type` |
|---|---|---|
| same entity, type jitter across docs (`schema matching`: Process/Algorithm/…) | merges ✓ (coarse type identical after canon) | merges ✓ |
| homograph, different coarse type (`Apple` org vs fruit) | **wrongly merges ✗** | stays separate ✓ |
| homograph, *same* coarse type (two `concept`s named `blocking`) | wrongly merges | still merges (documented limit; needs context/neighborhood, out of scope) |

The third row is the honest boundary: a coarse-type key cannot separate same-coarse-class homographs — that needs the ER scorer (the deferred "profile-link on top of name_ci" follow-on), not a key.

## Validation / gate

Two substrate-eval runs, `name_ci` vs `name_ci_type`, on two corpora:

The two arms test two different things:

1. **Standard engineered corpus (recall parity — tests EXTRACTOR type consistency):** with `ENTITY_TYPE_CANON=1`, `name_ci_type` R(B) must stay ≈ `name_ci` R(B) (within ~0.05 of 0.75 at ambiguity=0). A drop means the vocab is too fine and the 7B jitters *within* it (the constraint didn't yield consistent coarse types) — the vocab-granularity knob is the lever.
2. **Homograph corpus (precision recovery — tests the KEY's separation):** the injected docs carry explicit coarse-type cues, so this isolates whether the key separates different-typed same-name entities. `name_ci` P(B) must visibly **drop** (a negative control proving the corpus exercises the risk — if it doesn't drop, the injection is broken, not the fix); `name_ci_type` P(B) must **hold** near the non-homograph level. That delta is the deliverable.

Gate: `name_ci_type` recall within 0.05 of `name_ci` on standard **and** — with `name_ci` P(B) confirmed to drop on the homograph corpus (the control) — `name_ci_type` P(B) strictly better than `name_ci` there.

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
- `goldengraph/resolve.py` — `_key_payload` `name_ci_type` branch (reads the vocab + `canonicalize_entity_type` lazily). **After #1331 merges**; re-verify single-chokepoint first.
- `benchmarks/.../dataset/concepts_loader.py` (read-only check) — confirm the `entity_type` field the injection maps from.
- `benchmarks/.../qa_e2e/engineered.py` — add `coarse_type` to `_Entity` (mapped via `canonicalize_entity_type`); `GOLDENGRAPH_BENCH_HOMOGRAPH=k` injection (pick edge-endpoint pairs of DIFFERENT coarse type, shared surface across all their docs); type-cued appositive rendering for homograph docs.
- Tests: `test_entity_type_canon.py` (canonicalize_entity_type: exact/substring/fallback/case-fold), `test_xdoc_key.py` (name_ci_type payload), a generator test (homograph docs carry two distinct entity_ids under one surface with differing coarse-type cues; `emit_gold_mentions` captures both).

## Testing

Box-safe pure tests for `canonicalize_entity_type` (exact/substring/fallback/case-fold), the `name_ci_type` payload, and the homograph generator's gold (two entity_ids, one surface, distinct coarse types). One Modal run for the recall-parity + precision-recovery table.

## Risks

- **Homograph docs without a type cue = false negative** (the design-review catch). If the injected docs render the bare `"{s} {rel} {o}."` template, the 7B has no basis to type the two same-surface entities differently, so `name_ci_type` collapses them like `name_ci` and the test wrongly reads "fix doesn't work." Mitigated by the mandatory type-disambiguating appositive rendering (§5). The negative control (`name_ci` P(B) must drop on the homograph corpus) catches a broken injection before it can produce a misleading result.
- **7B non-compliance with the vocab** — mitigated by the deterministic safety net (the key canonicalizes whatever raw type extraction emits).
- **Within-vocab jitter re-fragmenting recall** — measured directly by the recall-parity gate; the vocab-granularity knob is the lever if it fails.
- **Coarse vocab too coarse to separate real homograph classes** (two `concept`s named `blocking`) — surfaced by the homograph corpus; the honest boundary (same-coarse-class needs the ER scorer, not a key) is documented, not hidden.
- **Prerequisite drift** — #1331 may change shape before merge; the impl step re-verifies the `_key_payload` chokepoint before extending it.
