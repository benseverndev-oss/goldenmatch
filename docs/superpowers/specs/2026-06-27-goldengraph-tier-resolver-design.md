# GoldenGraph slice 4b -- wire the UnifiedPlan into the build path (tier -> resolver)

**Status:** design
**Date:** 2026-06-27
**Owner:** Ben Severn
**Worktree:** `gg-tier-resolver` (branch `feat/goldengraph-tier-resolver`, STACKED on
`feat/goldengraph-unified-planner` / slice 4a / PR #1286)

## Problem

Slice 4a built the meta-kernel decision: a query workload -> `UnifiedPlan{resolution_tier}`. But the
plan is INERT -- nothing consumes it; the engine resolves at a fixed strategy (`resolve(mentions)`)
regardless of the chosen tier. Slice 4b makes the decision executable: the `resolution_tier` selects
the actual resolver `ingest` uses to build the graph, with a free deterministic gate proving the tiers
resolve differently (the mechanism behind the measured capability gap).

## Goal

A `resolver_for_tier(tier) -> Resolver` factory + a `plan_resolver(workload) -> (UnifiedPlan, Resolver)`
executable join, plus a free deterministic gate that (a) the resolvers are distinct, (b) EXACT groups
deterministically, and (c) on the engineered universe FUZZY achieves materially higher resolution
recall than EXACT (it merges variant surfaces EXACT cannot). 4b is fully deterministic -- no opt-in
lane (see Non-goals re: the end-to-end build comparison).

Slice 4b of the meta-kernel (4a join [pending #1286]; 4b wire-into-build [this]; 4c unified entry point
+ explicit ExecutionPlan/scale delegation + cross-controller budget).

## Non-goals (3 of these were REFRAMED after spec review)

- **No end-to-end "build the corpus via `ingest` under EXACT vs FUZZY and compare capability".** The
  resolver runs INTRA-document (`_prepare_doc`), but `generate_engineered` emits ONE document per edge
  (`"{s} {rel} {o}."`, s != o, self-edges excluded), so a document never contains two surfaces of the
  same entity -> intra-doc resolution has nothing to merge, and cross-document reconciliation is
  resolver-independent (the store merges on `record_key` overlap; both tiers emit the same
  `_record_key(name,typ)`). So EXACT-built ~= FUZZY-built on THIS corpus -- the comparison would be
  vacuous. The resolution-recall gate (below) IS the mechanism proof; the build-flows-to-capability
  link is slice-D's dial scorecard (already measured) + 4a's gate (reused, not re-derived). A true
  end-to-end build comparison needs a MULTI-surface-per-document (prose) corpus -- a future asset, not
  4b.
- **Explicit scale/compute delegation is OUT (4c).** `FUZZY`/`FUZZY_CONTEXT` route through
  `gm.dedupe_df`, which already runs goldenmatch's auto-config (backend/scale planning) internally, so
  "delegate compute to the ER controller" is structurally inherent; surfacing a goldenmatch
  `ExecutionPlan` is deferred to 4c.
- **EXACT is NOT goldenmatch-free.** `_exact_resolve` populates `record_keys` via the shared
  `_record_key` (a `goldenmatch.record_fingerprint`) so EXACT- and FUZZY-built stores reconcile across
  documents with the SAME fingerprints. So `_exact_resolve` imports goldenmatch (it just avoids
  `dedupe_df`/polars). goldenmatch is installed in every lane that runs these tests (the local `.venv`
  and the goldengraph-pipeline lane), so this is not a CI problem -- but the EXACT path is "no
  dedupe_df / deterministic grouping", NOT "no goldenmatch".
- No change to the default `resolve` behavior (backward-compatible refactor; `resolve` stays
  `FUZZY_CONTEXT`); nothing forces `ingest` to consult the planner.

## Architecture

### 1. Tier -> resolver (`goldengraph/resolve.py`, MODIFY)

Refactor `resolve` into a `use_context`-gated fuzzy core + an exact resolver:

```
def _fuzzy_resolve(mentions, *, use_context: bool) -> list[ResolvedEntity]:
    # the existing resolve() body; the `context` column is included only when
    # use_context AND any(m.context). (At use_context=True this is byte-identical to today's resolve.)

def _exact_resolve(mentions) -> list[ResolvedEntity]:
    # group mentions by EXACT (name, typ); each distinct (name,typ) is its own entity. No dedupe_df
    # (deterministic grouping). record_keys via the SAME _record_key as the fuzzy path (so EXACT- and
    # FUZZY-built stores share fingerprints -> cross-doc reconciliation is tier-compatible). Imports
    # goldenmatch only for _record_key.

def resolve(mentions) -> list[ResolvedEntity]:
    return _fuzzy_resolve(mentions, use_context=True)   # backward-compatible default (FUZZY_CONTEXT)
```

### 2. Tier factory + executable join (`goldengraph/unified.py`, MODIFY)

```
def resolver_for_tier(tier: ResolutionTier) -> Resolver:
    from .resolve import _exact_resolve, _fuzzy_resolve
    if tier is ResolutionTier.EXACT:
        return _exact_resolve
    if tier is ResolutionTier.FUZZY:
        return lambda ms: _fuzzy_resolve(ms, use_context=False)
    return lambda ms: _fuzzy_resolve(ms, use_context=True)   # FUZZY_CONTEXT

def plan_resolver(queries, *, predicates=None, llm_classifier=None) -> tuple[UnifiedPlan, Resolver]:
    plan = plan_resolution(profile_workload(queries, predicates=predicates, llm_classifier=llm_classifier))
    return plan, resolver_for_tier(plan.resolution_tier)
```

A caller: `plan, resolver = plan_resolver(workload); ingest_corpus(texts, store, resolver=resolver, llm=...)`.
`Resolver = Callable[[list[Mention]], list[ResolvedEntity]]` (the existing `ingest` param type).

**Tier the planner selects (MINOR review note):** `plan_resolution` (4a) emits EXACT or FUZZY -- so
`plan_resolver` selects `FUZZY` (name+type) for capability workloads. That is deliberate: `FUZZY` ==
the slice-D `goldengraph` dial (name+type), the tier whose capability win over EXACT is MEASURED
(0.797 vs 0.510 agg; 0.558 vs 0.234 bridge). `FUZZY_CONTEXT` (= the default `resolve`, strictly more
signal) is reachable via `resolver_for_tier(FUZZY_CONTEXT)` but is NOT auto-selected -- its capability
win on the engineered corpus isn't separately measured (the gold-triple dial build carries no
context), so auto-selecting it would be an unmeasured upgrade. Calibrating the capability branch to
FUZZY_CONTEXT is a deferred (4c) refinement, not a 4b change; 4a stays unchanged.

### 3. Gate (free, deterministic, key-free)

- **`resolver_for_tier` smoke (needs goldengraph only):** returns three distinct callables; EXACT is
  `_exact_resolve`.
- **EXACT resolver behavior:** assert on the GROUPING (member_idx / surface_names): identical
  `(name,typ)` mentions land in one entity; a variant surface (`"Apple"` vs `"Apple Inc"`, same typ)
  stays SEPARATE. Deterministic (no dedupe_df). (Needs goldenmatch for `_record_key`, present in all
  lanes.)
- **Resolution-recall on the engineered universe (needs goldenmatch dedupe_df -> goldengraph-pipeline
  lane):** `erkgbench/qa_e2e/tier_eval.py` builds a Mention per **DISTINCT** concept surface
  (`dict.fromkeys([concept] + [v.surface for v in variants])`, typ = entity_type) over `load_concepts`,
  runs `resolver_for_tier(tier)`, and computes `resolution_recall(tier)` = fraction of same-concept
  DISTINCT-surface PAIRS placed in one resolved group. **Deduping identical surfaces is load-bearing:**
  the dataset plants `cross_document_exact` variants byte-identical to the canonical, which EXACT
  *would* merge -- deduping removes those trivial pairs so the metric measures merges of genuinely
  distinct strings, making EXACT recall a true ~0 (distinct strings never exact-match) and the
  FUZZY-EXACT gap robust to FUZZY jitter. Assert `FUZZY_recall - EXACT_recall >= MARGIN`, MARGIN frozen
  from the measured run (verify-then-freeze). STOP-and-surface if FUZZY does NOT out-recall EXACT (the
  resolver wiring is broken or the tier distinction is meaningless on this corpus -- many variants are
  abbreviations [`LSH`/`WCC`/`EM`] that name-only fuzzy may not merge, so the measured FUZZY recall
  could be modest; freeze MARGIN against it, and if it's ~0 the slice's premise needs Ben).

`run_tier_eval.py` CLI -> `TIER.md`; `gate_exit_code`.

## Components / file structure

- `packages/python/goldengraph/goldengraph/resolve.py` (MODIFY): `_fuzzy_resolve(use_context)`,
  `_exact_resolve`, `resolve` delegates.
- `packages/python/goldengraph/goldengraph/unified.py` (MODIFY): `resolver_for_tier`, `plan_resolver`.
- `packages/python/goldengraph/tests/test_resolve_tiers.py` (CREATE): EXACT grouping behavior +
  resolver_for_tier smoke + plan_resolver (needs goldenmatch for `_record_key`; goldengraph-pipeline
  lane + local `.venv` both have it).
- `erkgbench/qa_e2e/tier_eval.py` (CREATE): `resolution_recall(tier)` over the universe + `TierResult` +
  gate + render.
- `erkgbench/qa_e2e/run_tier_eval.py` (CREATE): CLI -> TIER.md.
- `erkgbench/qa_e2e/.../tests/test_qa_tier.py` (CREATE): wheel-free gate-shape (hand-built TierResult).
- `.github/workflows/goldengraph-pipeline.yml` (MODIFY): tier gate step + upload (after Upload UNIFIED.md).

## Error handling

- `_exact_resolve` / `_fuzzy_resolve` never raise on well-formed mentions; empty -> `[]`.
- `resolver_for_tier` is total over the enum.
- `resolution_recall` counts only same-concept DISTINCT-surface pairs; a concept with <2 distinct
  surfaces contributes no pairs; a 0-pair universe -> recall 0.0 (the MARGIN assertion catches it).

## Testing strategy (TDD)

Per-task failing-test-first, ruff-clean per commit. EXACT grouping + resolver_for_tier + plan_resolver
+ gate-shape run wherever goldenmatch is installed (local `.venv`, goldengraph-pipeline lane); the
resolution-recall metric needs goldenmatch `dedupe_df` -> runs in the goldengraph-pipeline lane (which
already runs dedupe_df via kg_scorecard). Verify FUZZY/EXACT recall on the real universe (deduped
surfaces) before freezing MARGIN; if FUZZY ~= EXACT, surface to Ben.

## Open risks

- **dedupe_df version-sensitivity** -- mitigated by the full universe (stable, slice-D precedent) +
  deduped distinct surfaces + freeze-with-headroom. EXACT recall is a true ~0 AFTER deduping (distinct
  strings never exact-merge).
- **Modest FUZZY recall on abbreviation variants.** Many planted variants are abbreviations name-only
  fuzzy won't merge, so FUZZY recall may be well below 1.0; that's fine as long as FUZZY > EXACT by the
  frozen MARGIN (verify-then-freeze). If FUZZY ~= EXACT, STOP -- the tier distinction is too weak on
  this corpus to gate.
- **Recall, not precision.** The gate measures same-concept merge recall; FUZZY false-merges are
  captured downstream (slice-D capability), not here.
- **4b wires the resolver but the engine default is unchanged + nothing forces `ingest` to consult the
  planner.** Making `ingest`/a CLI consult the planner by default is a 4c/product decision. Stated.
