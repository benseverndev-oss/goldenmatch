# GoldenGraph slice 4b -- wire the UnifiedPlan into the build path (tier -> resolver)

**Status:** design
**Date:** 2026-06-27
**Owner:** Ben Severn
**Worktree:** `gg-tier-resolver` (branch `feat/goldengraph-tier-resolver`, STACKED on
`feat/goldengraph-unified-planner` / slice 4a / PR #1286)

## Problem

Slice 4a built the meta-kernel decision: a query workload -> `UnifiedPlan{resolution_tier}`. But the
plan is INERT -- nothing consumes it. The engine still resolves at a fixed strategy
(`resolve(mentions)` over name+type+context) regardless of the chosen tier. Slice 4b makes the
decision real: the `resolution_tier` selects the actual resolver used to BUILD the graph, with a free
gate proving the tiers resolve differently (the mechanism behind the measured capability gap).

## Goal

A `resolver_for_tier(tier) -> Resolver` factory + a `plan_resolver(workload) -> (UnifiedPlan, Resolver)`
executable join, plus a free deterministic gate that the resolvers (a) are distinct and (b) achieve
materially different resolution recall on the engineered universe (FUZZY merges variant surfaces EXACT
cannot). Plus an opt-in real-LLM end-to-end row (build the corpus under EXACT vs FUZZY, show
FUZZY-built capability >> EXACT-built).

Slice 4b of the meta-kernel (4a join [merged-pending #1286]; 4b wire-into-build [this]; 4c unified
entry point + explicit ExecutionPlan/scale delegation + cross-controller budget).

## Non-goals

- **Explicit scale/compute delegation is OUT (4c).** `FUZZY`/`FUZZY_CONTEXT` route through
  `gm.dedupe_df`, which ALREADY runs goldenmatch's auto-config (backend/scale planning) internally --
  so "delegate compute to the ER controller" is structurally inherent. Explicitly surfacing a
  goldenmatch `ExecutionPlan` (e.g. for distributed builds) is deferred to 4c. 4b is the resolver
  wiring only.
- No new query-routing or planner logic (4a is reused as-is).
- No change to the default `resolve` behavior (backward-compatible refactor; `resolve` stays
  `FUZZY_CONTEXT`).
- No toy-frame fuzzy-merge assertions: `dedupe_df` on tiny frames is version-flaky (known gotcha) ->
  the resolution-recall gate runs on the FULL engineered universe (stable, as slice-D's goldengraph
  dial already demonstrates).

## Architecture

### 1. Tier -> resolver (`goldengraph/resolve.py`, MODIFY)

Refactor `resolve` to share a parameterized fuzzy core; add an exact resolver:

```
def _fuzzy_resolve(mentions, *, use_context: bool) -> list[ResolvedEntity]:
    # the existing resolve() body, but the `context` column is included only when use_context is True
    # AND any mention carries context.

def _exact_resolve(mentions) -> list[ResolvedEntity]:
    # group mentions by EXACT (name, typ); each distinct (name,typ) is its own entity. Pure-Python
    # (no dedupe_df) -> fully deterministic. record_keys via the same _record_key as the fuzzy path.

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

### 3. Gate (free, deterministic, key-free)

- **`resolver_for_tier` smoke (wheel-free, no goldenmatch):** returns three distinct callables; EXACT
  is `_exact_resolve`.
- **EXACT resolver behavior (wheel-free, pure):** identical `(name,typ)` mentions merge into one
  entity; a variant surface (`"Apple"` vs `"Apple Inc"`, same typ) stays SEPARATE. Fully deterministic.
- **Resolution-recall on the engineered universe (needs goldenmatch dedupe_df -- runs in the
  goldengraph-pipeline lane, NOT the toy-frame path):** `erkgbench/qa_e2e/tier_eval.py` builds a Mention
  per concept surface (`[concept] + variants`, typ = entity_type) over `load_concepts`, runs
  `resolver_for_tier(tier)`, and computes `resolution_recall(tier)` = fraction of same-concept surface
  PAIRS placed in one resolved group. Assert `FUZZY_recall - EXACT_recall >= MARGIN` (EXACT can't merge
  distinct strings -> ~0; FUZZY merges string-close variants). MARGIN frozen from the measured run
  (verify-then-freeze). This ties 4b's NEW resolver code to real ER quality on the full universe.

`run_tier_eval.py` CLI -> `TIER.md`; `gate_exit_code`. STOP-and-surface if FUZZY does NOT out-recall
EXACT (the resolver wiring would be broken / the tier distinction meaningless).

### 4. Opt-in real-LLM end-to-end (`bench-graphrag-qa`, the loop closed)

Build the engineered corpus THROUGH `ingest` (real LLM extraction) under `resolver_for_tier(EXACT)`
vs `(FUZZY)`, then score a capability metric (bridge-recall or aggregation) on each built store. Show
FUZZY-built capability >> EXACT-built -- the full end-to-end proof that the planner's tier choice
changes outcomes. Budget-capped, `|| true`, billing-blocked. (The deterministic gate proves the
resolver behaviour; this proves it FLOWS to capability through the real build.)

## Components / file structure

- `packages/python/goldengraph/goldengraph/resolve.py` (MODIFY): `_fuzzy_resolve(use_context)`,
  `_exact_resolve`, `resolve` delegates.
- `packages/python/goldengraph/goldengraph/unified.py` (MODIFY): `resolver_for_tier`, `plan_resolver`.
- `packages/python/goldengraph/tests/test_resolve_tiers.py` (CREATE): EXACT behavior + resolver_for_tier
  smoke + plan_resolver (wheel-free; EXACT path needs no goldenmatch).
- `erkgbench/qa_e2e/tier_eval.py` (CREATE): `resolution_recall(tier)` over the universe + `TierResult` +
  gate + render.
- `erkgbench/qa_e2e/run_tier_eval.py` (CREATE): CLI -> TIER.md.
- `erkgbench/qa_e2e/.../tests/test_qa_tier.py` (CREATE): wheel-free gate-shape + EXACT-recall-is-low.
- `.github/workflows/goldengraph-pipeline.yml` (MODIFY): tier gate step + upload (after Upload UNIFIED.md).
- `.github/workflows/bench-graphrag-qa.yml` (MODIFY): opt-in end-to-end EXACT-vs-FUZZY build row.

## Error handling

- `_exact_resolve` / `_fuzzy_resolve` never raise on well-formed mentions; empty -> `[]`.
- `resolver_for_tier` is total over the enum.
- `resolution_recall` skips concepts with no variants (no same-concept pairs); a 0-pair universe ->
  recall 0.0 (the gate's MARGIN assertion would catch a degenerate asset).
- Opt-in end-to-end is `|| true` + budget-capped; a build failure (429/missing key) -> n/a row.

## Testing strategy (TDD)

Per-task failing-test-first, ruff-clean per commit. EXACT behavior + resolver_for_tier + plan_resolver
+ gate-shape are wheel-free (no goldenmatch needed for the pure EXACT path); the resolution-recall
metric needs goldenmatch dedupe_df -> runs in the goldengraph-pipeline lane (which already runs
dedupe_df via kg_scorecard). Verify FUZZY/EXACT recall on the real universe before freezing MARGIN.

## Open risks

- **dedupe_df version-sensitivity.** Mitigated by running resolution-recall on the FULL universe (not
  a toy frame) where dedupe_df is stable (slice-D goldengraph dial precedent), and by freezing MARGIN
  with headroom below the measured FUZZY recall. EXACT recall is deterministically ~0 (distinct strings
  never exact-merge), so the gap is robust to FUZZY jitter.
- **Recall, not precision.** The gate measures same-concept MERGE recall (the EXACT-can't-merge point);
  FUZZY false-merges (precision) are captured downstream by the capability metrics (slice D / the opt-in
  end-to-end), not here. Stated.
- **4b wires the resolver but the engine default is unchanged.** `resolve` stays FUZZY_CONTEXT; a caller
  must opt into `plan_resolver`. Making `ingest` consult the planner by default is a 4c/product
  decision, not 4b. Stated.
