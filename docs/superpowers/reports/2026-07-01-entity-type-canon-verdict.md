# Entity-Type Canonicalization — Verdict

**Date:** 2026-07-01
**Branch:** `feat/entity-type-canon`
**Spec/Plan:** `docs/superpowers/specs/2026-07-01-entity-type-canon-design.md` · `docs/superpowers/plans/2026-07-01-entity-type-canon.md`
**Follows:** `GOLDENGRAPH_XDOC_KEY=name_ci` (#1331) — recovered substrate R(B) 0.23→0.75 but is homograph-unsafe (drops type entirely).

## What shipped

A gated `GOLDENGRAPH_XDOC_KEY=name_ci_type` cross-doc key → `(name_ci, canonicalize_entity_type(typ))`, plus an extraction-time closed type vocab (`GOLDENGRAPH_ENTITY_TYPE_CANON` + `GOLDENGRAPH_ENTITY_TYPE_VOCAB`, default 8 coarse types) and a homograph injection in the engineered generator (`GOLDENGRAPH_BENCH_HOMOGRAPH=k`). All default-off.

## Validation — two corpora × two keys (engineered, ambiguity=0, 7B)

| leg | corpus | key | R(B) | P(B) | edge_recall |
|---|---|---|---|---|---|
| 30 | standard | `name_ci` | 0.7475 | 0.9379 | 0.9281 |
| 31 | standard | `name_ci_type` + canon | **0.6374** | 0.8175 | 0.9496 |
| 32 | homograph | `name_ci` (control) | 0.6745 | **0.8038** | 0.8633 |
| 33 | homograph | `name_ci_type` + canon | 0.5173 | **0.8856** | 0.8273 |

## Verdict: the mechanism works, but it is NOT free

**Precision recovery — PASS.**
- Negative control fired: `name_ci` P(B) drops **0.9379 → 0.8038** from the standard to the homograph corpus — the injected same-surface/different-type collisions genuinely conflate under a type-blind key, so the corpus exercises the risk (not a broken injection).
- `name_ci_type` recovers it: on the homograph corpus **P(B) 0.8856 > 0.8038 (+0.082)**. The coarse-type key keeps the homographs apart. A second, qualitative signal: on the homograph corpus the A−B gap goes **negative** (Level A, the type-blind resolver in isolation, scores 0.197 and over-merges the collisions; the type-aware Level-B build beats it) — the key is doing exactly what a type-blind resolver cannot.

**Recall parity — FAIL.**
- On the standard corpus `name_ci_type` costs **0.11 recall** (R(B) 0.7475 → 0.6374), well outside the ≤0.05 gate. This is the spec's predicted failure mode: the 8-type vocab is too fine, so even a constrained 7B jitters *within* it (types one entity `concept` in one doc and something else in another) and re-fragments ~15% of the recall the bare `name_ci` key had recovered.

**Combined gate: FAIL on recall parity.** `name_ci_type` is therefore NOT a drop-in replacement for `name_ci` — it trades recall for homograph safety.

## What this means

- **Ship gated, as planned.** The feature is default-off and its characteristics are now measured: homograph precision +0.08 at a recall cost of −0.11 vs bare `name_ci`. It is a legitimate opt-in tool for corpora where homographs matter, not a default.
- **The vocab-granularity knob is the named, untested lever.** The recall cost is within-vocab type jitter; a *coarser* vocab (fewer buckets → less room to jitter, e.g. `person / organization / concept / other`) should recover recall while still separating the injected homographs (which map to distinct coarse buckets). That sweep is the immediate follow-on — the eval + the two corpora are now in place to run it cheaply.
- **The honest boundary stands:** a coarse-type *key* cannot separate two same-coarse-class homographs; that needs the ER scorer (the separate "profile-link on top of name_ci" follow-on).

## Follow-ons

1. **Vocab-granularity sweep** — the direct lever for the recall cost; re-run legs 30/31 across 2–3 vocab sizes.
2. Embedding-NN / LLM type derivation (the deferred coarse-type mechanisms) if extraction-constraint consistency stays the bottleneck.
3. Same-coarse-class homograph disambiguation via the ER scorer.
