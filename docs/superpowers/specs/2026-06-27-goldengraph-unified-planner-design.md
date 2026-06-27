# GoldenGraph slice 4a -- workload-aware resolution planner (the meta-kernel join)

**Status:** design
**Date:** 2026-06-27
**Owner:** Ben Severn
**Worktree:** `gg-unified` (branch `feat/goldengraph-unified-planner`, STACKED on
`feat/goldengraph-llm-classifier` / slice 3 / PR #1285)

## Problem

The router program built a query controller (NL query -> RetrievalPlan, slices 1-3). Separately,
goldengraph resolves entities with a swappable strategy (`resolve(mentions)` over name+type[+context],
injectable into `ingest`). The two have never been joined: the engine resolves at a FIXED tier
regardless of what the queries will demand. But the capability program (A-D) proved weak ER costs
capability accuracy (exact-match ER == no-merge on aggregation + reachability; +13pp ER F1 from
fuzzy+context). So the resolution tier SHOULD be chosen from the query workload: a workload of
aggregation/temporal/multi-hop queries needs high-recall fuzzy ER; a lookup-only workload does not.

Slice 4a builds that decision -- the concrete join where the query layer informs the ER layer over
the shared resolved-graph substrate -- and proves the decision is grounded in the measured A-D data.

## Goal

A `UnifiedPlanner` that profiles a query workload and emits a `UnifiedPlan` picking the resolution
tier the workload demands, plus a free deterministic gate that (1) the planner routes correctly and
(2) the chosen tier actually wins the workload's capability (reusing slice-D's measured dial
scorecard). The unification thesis, MEASURED.

This is slice 4a of the meta-kernel (4a join [this]; 4b wire the plan into the build path + delegate
scale to goldenmatch's ExecutionPlan controller; 4c unified entry point + cross-controller budget).

## Non-goals

- **Nothing CONSUMES the `UnifiedPlan` yet.** 4a is the decision + its measured justification (the
  brain). Selecting the resolver in `ingest` from the plan is 4b. The planner emits a grounded plan;
  the gate proves it's right.
- **Workload-only.** 4a drives off the query workload (the novel query->ER direction). Corpus/scale
  planning (delegating to goldenmatch's auto-config `ExecutionPlan`) is 4b/4c -- NOT here.
- No learned policy: the `capability_fraction -> tier` rule is one calibrated threshold (like the ER
  controller's planner rules).
- No new resolver implementations: `ResolutionTier` NAMES the existing tiers (exact / fuzzy /
  fuzzy+context); 4b wires them.

## Architecture

### 1. The planner (`goldengraph/unified.py`, pure-Python, no wheel)

```
class ResolutionTier(StrEnum): EXACT / FUZZY / FUZZY_CONTEXT
    # EXACT = name-only merge; FUZZY = name+type (current default); FUZZY_CONTEXT = name+type+context
    # (the SP6/#1148 +13pp lever). Ordered by recall/cost.

@dataclass
class WorkloadProfile:
    intents: dict           # QueryIntent -> count
    capability_fraction: float   # (AGGREGATE + TEMPORAL_ASOF + MULTI_HOP) / total
    retrieval_modes_needed: set  # the plan_query modes the workload uses (aggregate/as_of/hybrid/local)

@dataclass
class UnifiedPlan:
    resolution_tier: ResolutionTier
    retrieval_modes_needed: set
    capability_fraction: float
    rationale: str

CAP_THRESHOLD = 0.5  # calibrated: >= this fraction of capability-demanding queries -> high-recall ER

def profile_workload(queries, *, predicates=None, llm_classifier=None) -> WorkloadProfile:
    # resolve_profile (slice 3) per query -> QueryProfile; histogram intents; capability_fraction =
    # share of {AGGREGATE, TEMPORAL_ASOF, MULTI_HOP}; retrieval_modes_needed = {plan_query(p).mode}.
    # Passes llm_classifier through to resolve_profile so NL workloads profile via the LLM tier.

def plan_resolution(wp: WorkloadProfile) -> UnifiedPlan:
    # capability_fraction >= CAP_THRESHOLD -> FUZZY (high-recall); else EXACT (cheap).
    # rationale names the driving fraction + the A-D justification.
```

Rationale for FUZZY vs FUZZY_CONTEXT in 4a: the planner's HARD decision is EXACT-vs-FUZZY (the
measured, gateable gap -- slice D's goldengraph dial == FUZZY). `FUZZY_CONTEXT` is in the enum for
4b (a future refinement: if the workload is reachability-heavy / multi-hop, prefer +context); 4a
emits FUZZY for any capability workload and notes FUZZY_CONTEXT as the 4b upgrade in the rationale.

- **Capability intents:** AGGREGATE + TEMPORAL_ASOF + MULTI_HOP demand high-recall ER (B1 set
  aggregation, B2 temporal, A multi-hop reachability all degrade under weak ER). LOOKUP (single-entity
  fetch) does not -> excluded from `capability_fraction`.

### 2. Gate (free, deterministic, key-free; new `unified_eval.py` + a `goldengraph-pipeline` step)

`erkgbench/qa_e2e/unified_eval.py` builds two workloads from the engineered corpus and reuses
slice-D's `kg_scorecard`:
1. **Routing correctness (wheel-free):** a CAPABILITY workload (the B1 aggregation + B2 temporal
   question texts) -> `plan_resolution` picks `FUZZY`; a LOOKUP workload (synthetic "what is <concept>?"
   queries over the same entities) -> `EXACT`. HARD. Deterministic (heuristic profiling). Also assert
   the capability workload's `capability_fraction >= CAP_THRESHOLD` and the lookup workload's `< CAP_THRESHOLD`.
2. **The choice is justified by measured capability data (needs wheel -- the unification thesis):**
   run `kg_scorecard.run_scorecard_deterministic(...)` (slice D); for the capability the workload
   demands, assert the planner's chosen tier (`FUZZY` == the `goldengraph` dial) beats the cheaper tier
   (`EXACT` == the `exact_match` dial) by a frozen margin on BOTH `aggregation_f1` and `bridge_recall`
   (slice-D measured: agg 0.797 vs 0.510 = 0.287; bridge 0.558 vs 0.234 = 0.324). HARD. This proves the
   planner's decision WOULD WIN -- and it is NOT circular: the validation comes from slice D's
   independent per-dial measurement, not from the planner.

Render `UNIFIED.md` (workload profiles + chosen tiers + the justification deltas + verdicts).
STOP-and-surface if the planner picks the wrong tier, or if `kg_scorecard` shows fuzzy does NOT beat
exact on the capability (that would refute the thesis the whole program rests on).

### Opt-in (no real LLM needed for 4a)
The planner accepts an optional `llm_classifier` passthrough; a wheel-free test profiles a paraphrase
workload (slice-3 `router_paraphrases`) with a STUB classifier and asserts it still picks `FUZZY`
(NL workloads route correctly via the tier-2 classifier). No billing-blocked lane needed -- the
measured justification is already deterministic via kg_scorecard.

## Components / file structure

- `packages/python/goldengraph/goldengraph/unified.py` (CREATE): `ResolutionTier`, `WorkloadProfile`,
  `UnifiedPlan`, `profile_workload`, `plan_resolution`, `CAP_THRESHOLD`.
- `packages/python/goldengraph/tests/test_unified.py` (CREATE): profiling, threshold routing,
  modes_needed, stub-classifier paraphrase workload -> FUZZY (all pure-Python).
- `erkgbench/qa_e2e/unified_eval.py` (CREATE): build capability + lookup workloads from the engineered
  corpus; routing-correctness (wheel-free) + the kg_scorecard justification (wheel); `UnifiedResult` +
  gate verdicts + render.
- `erkgbench/qa_e2e/run_unified_eval.py` (CREATE): CLI -> `UNIFIED.md`, exit non-zero on HARD failure.
- `erkgbench/qa_e2e/.../tests/test_qa_unified.py` (CREATE): wheel-free routing + gate-shape.
- `.github/workflows/goldengraph-pipeline.yml` (MODIFY): unified gate step + upload.
- `.github/workflows/bench-er-kg.yml` (MODIFY): wheel-free unified test on the pure-Python list.

## Error handling

- `profile_workload` / `plan_resolution` never raise on well-formed input; an empty workload ->
  capability_fraction 0.0 -> EXACT (safe cheap default).
- Routing-correctness is offline + deterministic; the justification reuses kg_scorecard (which needs
  the wheel) and runs only in the pipeline lane after the wheel build.

## Testing strategy (TDD)

Per-task failing-test-first, ruff-clean per commit. `unified.py` + routing-correctness are wheel-free;
the kg_scorecard justification needs the wheel (goldengraph-pipeline). Verify the planner's tier
choices + the kg_scorecard deltas on the real corpus before freezing the margin.

## Open risks

- **The justification reuses slice-D's measured deltas.** If slice D's kg_scorecard ever regresses (gg
  no longer beats exact on capability), this gate goes RED -- correctly, because the planner's whole
  premise would be false. That coupling is intentional (the planner's policy is only valid while the
  measured thesis holds).
- **`CAP_THRESHOLD=0.5` is a calibrated heuristic**, not learned. The gate uses clearly-separated
  workloads (capability ~1.0, lookup ~0.0) so the exact threshold isn't load-bearing for the gate; it
  is a tunable policy knob for real mixed workloads (4b/4c may refine it).
- **4a emits a plan nothing consumes.** Deliberate -- the decision + its measured justification is the
  unit of value; wiring is 4b. Stated in the render so no one mistakes 4a for an end-to-end change.
