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

## Vocab-granularity sweep + isolation (follow-up)

The recall cost is NOT mostly the 7B. Two follow-up sweeps decomposed it.

**Coarser vocab is a real lever, with a sweet spot.** Standard-corpus R(B) under `name_ci_type` by vocab size:

| vocab | R(B) standard | homograph P(B) |
|---|---|---|
| `name_ci` ceiling (no type) | 0.7475 | 0.8038 (control) |
| 8-type | 0.6374 | 0.8856 |
| **4-type** | **0.6856** | **0.9309** |
| 3-type | 0.6696 | — |

Coarsening 8→4 recovers a third of the recall gap AND *improves* homograph precision (0.886→0.931) — because the injected homographs are assigned distinct coarse buckets, coarsening never costs precision. 3-type is worse than 4-type (non-monotonic), so **4 is the sweet spot** on this concept corpus. **The default vocab is now 4-type.**

**Isolation — the residual is the extraction constraint, not the key or the model.** Decomposing the 0.062 gap from the `name_ci` ceiling (0.7475) to `name_ci_type`+canon+4-type (0.6856):

| step | R(B) | Δ | attributed to |
|---|---|---|---|
| `name_ci`, no canon | 0.7475 | — | ceiling |
| `name_ci`, **+canon** | 0.7092 | **−0.038** | extraction-constraint perturbation |
| `name_ci_type`, +canon | 0.6856 | **−0.024** | the coarse-type key (within-vocab jitter) |

**61% of the recall cost is the prompt constraint perturbing what the 7B extracts** — a prompt-interaction effect, not model capability. Only 0.024 is type-key jitter (part model-consistency, part irreducible: an abstract concept's coarse type isn't a stable property). This means the recommended config may be `name_ci_type` **without** `ENTITY_TYPE_CANON` — rely on the deterministic `canonicalize_entity_type` substring-snap over OPEN extraction, recovering the 0.038 (the arc's recurring lesson: deterministic post-hoc snap beats prompt-constraint, cf. `SCHEMA_CANON`). Untested; the leading follow-on.

## What this means

- **Ship gated, as planned.** The feature is default-off and its characteristics are now measured: homograph precision +0.08 at a recall cost of −0.11 vs bare `name_ci`. It is a legitimate opt-in tool for corpora where homographs matter, not a default.
- **The vocab-granularity knob is the named, untested lever.** The recall cost is within-vocab type jitter; a *coarser* vocab (fewer buckets → less room to jitter, e.g. `person / organization / concept / other`) should recover recall while still separating the injected homographs (which map to distinct coarse buckets). That sweep is the immediate follow-on — the eval + the two corpora are now in place to run it cheaply.
- **The honest boundary stands:** a coarse-type *key* cannot separate two same-coarse-class homographs; that needs the ER scorer (the separate "profile-link on top of name_ci" follow-on).

## Follow-ons

1. **`name_ci_type` without `ENTITY_TYPE_CANON`** (deterministic post-hoc snap over open extraction) — the isolation attributes 61% of the recall cost to the extraction constraint, so dropping it may recover ~0.038 and nearly collapse the tradeoff. The leading next leg.
2. ~~Vocab-granularity sweep~~ — DONE; 4-type is the sweet spot, now the default.
3. Embedding-NN / LLM type derivation (the deferred coarse-type mechanisms) if `canonicalize_entity_type`'s substring-snap coverage proves the bottleneck.
4. Same-coarse-class homograph disambiguation via the ER scorer.
