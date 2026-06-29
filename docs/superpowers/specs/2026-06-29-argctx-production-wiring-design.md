# Argument-context resolution — production wiring (stage 1) — design

**Status:** Designed (approved 2026-06-29); ready for implementation plan.
**Owner:** Ben Severn
**Context:** The argument-context experiment ([[2026-06-29-argument-context-relation-resolution-experiment-design]])
PROVED — locally, on gold structure — that resolving predicates by the entity pairs they connect
crosses the open-vocab synonymy boundary that defeated every phrase-level method (B-cubed 1.0/1.0;
co-occurrence is the necessary+sufficient signal, ablation-confirmed). This wires that resolver into the
LIVE goldengraph discovery pipeline (real 7B extraction, not gold) and validates it on a controlled
co-occurrence corpus — stage 1 of a staged rollout (real-corpus benchmark is the follow-up milestone).

## Goal

Make open-vocab schema discovery work in the live pipeline: a `GOLDENGRAPH_DISCOVER_RESOLVE=argctx`
backend that clusters the 7B's extracted predicates by the **surface entity pairs** they connect, so
synonymous relations merge on a corpus that provides co-occurrence — recovering toward the closed-vocab
discovery number (~0.65) where every phrase-level method collapsed to ~0.17.

## Architecture

Two focused changes plus a validation run; the canonicalizer and ingest flow are untouched.

- The argctx resolver is a new value of the existing `GOLDENGRAPH_DISCOVER_RESOLVE` switch in
  `schema_discovery.discover_schema`. It reuses the experiment's proven pair-set Jaccard logic, but over
  **surface-normalized pairs built from the live extracted edges** — which `discover_schema` already
  collects in its `by_phrase` map (`predicate -> [(subj_surface, pred, obj_surface, source)]`). So the
  resolver needs almost no new threading; the data is already in hand.
- Co-occurrence alone is sufficient (ablation: types neither necessary nor sufficient), so there is **no
  entity-typing dependency**.
- The engineered corpus gains a co-occurrence rendering mode so the live pipeline actually HAS the
  signal (the existing `GOLDENGRAPH_BENCH_REL_PARAPHRASE` renders ONE phrasing per edge — the
  no-co-occurrence case that failed).

```
live docs (co-occurrence) -> 7B extract -> discover_schema [argctx: surface-pair-set clustering]
   -> RelationSchema -> canonicalize_extraction (UNCHANGED) -> resolve -> store -> ask
```

## Components

### 1. `_cluster_predicates_argctx(by_phrase)` — `schema_discovery.py`

`by_phrase` maps each predicate to its extracted edges. Build `pair_set[pred] = {(_norm(subj_surface),
_norm(obj_surface))}` and cluster by pair-set **Jaccard ≥ threshold** (default 0.5, env-tunable
`GOLDENGRAPH_ARGCTX_JACCARD`) via union-find — the experiment's `resolve_distributional` without the type
blocker (surface-only). Wire as `argctx` in `discover_schema`'s `GOLDENGRAPH_DISCOVER_RESOLVE` dispatch.
Singleton-isolation is automatic: a predicate sharing no pairs stays its own cluster.

### 2. Co-occurrence corpus rendering — `engineered.py` (gated `GOLDENGRAPH_BENCH_COOCCUR=1`)

Render the SAME edge `(subj, rel, dst)` in MULTIPLE docs — one per phrasing from `_REL_PHRASINGS[rel]`
— so the same `(subj,obj)` pair appears with different predicate phrasings (co-occurrence). The edge
graph and the sampled multi-hop questions are UNCHANGED (only more docs per edge); doc-ids get a
phrasing-index suffix so `_edge_doc_id` stays unique. Default off; composes with `ambiguity` (stage 1
uses ambiguity=0 so surfaces are canonical and pairs align without a resolution step).

### 3. No change to `canonicalize_extraction` / the walk

`argctx` is just another `GOLDENGRAPH_DISCOVER_RESOLVE` value; the discovered `RelationSchema` flows
through the existing path unchanged.

## Validation — the live-pipeline proof (the delta)

Two e2e runs on the SAME co-occurrence corpus (`BENCH_COOCCUR=1`, ambiguity=0, live 7B), so the delta
attributes the win to argctx, not an easier corpus:

- **argctx** (`DISCOVER_RESOLVE=argctx`): target recovery toward **~0.65** (closed-vocab discovery); the
  discovered-schema dump should show live-extracted synonyms clustered into the right relations.
- **default string backend** (control): expected to **fragment (~0.2)** — string clustering can't merge
  synonyms (the Phase-2 result).

**PASS = argctx clearly beats the default on the same corpus AND lands in range of closed-vocab
discovery (≥ ~0.55, allowing for the live-extraction noise the gold experiment didn't have).** That
delta proves the validated signal carries into the live, noisy pipeline. If argctx ≈ default (~0.2), the
live 7B isn't producing usable co-occurrence (extraction too noisy / surface pairs don't align) — a
real finding, diagnosed via the schema dump (the existing `[schema-discover]` logging).

**Unit tests (wheel-free, deterministic).** `_cluster_predicates_argctx` over a small synthetic
`by_phrase` (two phrasings sharing pairs merge; a distinct-pair predicate stays apart; a no-pair
spurious predicate stays a singleton). The co-occurrence renderer: the same edge yields multiple docs
with distinct phrasings + unique doc-ids, and the edge graph/questions are unchanged vs the
non-co-occurrence corpus.

## Error handling

- `argctx` fails-soft: any error in the backend → fall back to the default string clustering, logged.
- Extraction-noise isolation: a spurious predicate with no shared pairs stays a **singleton** (pair-set
  Jaccard 0 with everything) — cannot pollute real clusters.
- Determinism: the clustering is deterministic given a fixed extraction; the e2e carries the usual
  live-7B non-determinism (same caveat as every live run).

## Scope (YAGNI)

- **Surface-normalized pairs only.** Resolved-entity identity (for surface variants / real corpora) is
  the explicit real-corpus-stage follow-up, NOT stage 1. Stage 1 uses ambiguity=0 so surfaces align.
- **No type signal** (co-occurrence is sufficient — ablation-proven).
- Stage 1 = co-occurrence engineered live-pipeline proof; the **real-corpus benchmark is the next
  milestone** (where resolved-entity identity and real co-occurrence density get tested).
- No change to the canonicalizer or the ingest control flow.

## Files

- Modify: `packages/python/goldengraph/goldengraph/schema_discovery.py`
  (`_cluster_predicates_argctx` + the `argctx` dispatch branch).
- Modify: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/engineered.py`
  (co-occurrence rendering, gated).
- Modify/Create: `packages/python/goldengraph/tests/test_schema_discovery.py` (argctx unit tests);
  a corpus test under the bench `tests/` dir for the co-occurrence renderer.
- Validation: the existing `scripts/distill/modal_bench.py` (opts pass the new env flags; no bench code
  change beyond what already threads opts → env).
