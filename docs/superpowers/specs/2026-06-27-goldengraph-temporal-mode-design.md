# GoldenGraph query-router slice 2 -- temporal as-of mode + routing

**Status:** design
**Date:** 2026-06-27
**Owner:** Ben Severn
**Worktree:** `gg-temporal-mode` (branch `feat/goldengraph-temporal-mode`, STACKED on
`feat/goldengraph-query-router` / slice 1 / PR #1283)

## Problem

Slice 1 built the query-routing kernel and promoted aggregation to a first-class LLM-free `ask`
mode. It also *classifies* temporal "as of <date>" queries (`QueryIntent.TEMPORAL_ASOF`) but routes
them to the general `local` mode with a `not_yet_promoted` marker, because no as-of answer mode
exists in the engine. The capability is proven (slice B2: `goldengraph_asof` answers past-date
queries 1.0 vs a temporal-blind floor 0.0) but lives only as a bench function. Slice 2 promotes it.

## Goal

Promote temporal as-of from the B2 bench traversal into a first-class `ask` mode and flip the
router's `temporal_asof` plan from `not_yet_promoted` to a real `as_of` lever. Same two-tier shape
as slice 1: a free deterministic gate (classifier + routed as-of-accuracy) + an opt-in real-LLM row.

This is slice 2 of the 4-slice router program (1: kernel + aggregate [shipped, #1283]; 2: temporal
[this]; 3: LLM classifier tier + confidence hardening; 4: meta-kernel unifying ER + query
controllers).

## Non-goals

- **Bare-integer dates only.** The engineered B2 question is `"As of {D}, what does {anchor}
  {rel_words}?"` with `D` an integer (1-100). Slice 2 parses that integer. Real calendar-date
  parsing (years / ISO / relative) is OUT -- it pairs naturally with the slice-3 LLM tier (or a
  dedicated date-util); stated honestly, not faked.
- **No change to the existing B2 bench** (`temporal.py` stays QID-named for its id-space parity).
  The gate builds its own concept-surface-named store (below).
- No LLM in the deterministic gate. No change to slice-1's aggregate path or to `local`/`hybrid`/
  `global`.

## Architecture

### 1. Router kernel (`goldengraph/route.py`, MODIFY)

- Extend `classify_query`'s `TEMPORAL_ASOF` branch to extract slots. New `_TEMPORAL_LEADIN_RE`
  matches `"As of <D>, what does <rest>"`; `D` -> `as_of` (the raw integer string), and `<rest>` is
  split into `anchor_surface` + `relation` by the SAME predicate-suffix match the aggregate path
  uses (`_extract_agg_slots` is generalized / reused). Confidence high when anchor+relation+as_of
  all resolve.
- `plan_query`: `TEMPORAL_ASOF` with anchor+relation+as_of and confidence >= MIN_CONF ->
  `RetrievalPlan(mode="as_of")`; below threshold or missing slots -> the existing safe `local`
  fallback (DROP the `not_yet_promoted` note).

### 2. As-of as a first-class `ask` mode (`goldengraph/answer.py`, MODIFY)

```
def asof_object(slice_graph, anchor_surface: str, relation: str) -> str | None:
    """The object on a (subj==seed, predicate==relation) edge present IN THIS SLICE.
    The slice already encodes the as-of window, so this is the aggregate traversal
    returning ONE object. LLM-free."""
    seeds = slice_graph.seeds_by_name(anchor_surface)
    if not seeds:
        return None
    sub = slice_graph.query(seeds, 1)
    id_to_name = {e["entity_id"]: e["canonical_name"] for e in sub.get("entities", ())}
    seedset = set(seeds)
    objs = {
        id_to_name[e["obj"]]
        for e in sub.get("edges", ())
        if e["subj"] in seedset and e["predicate"] == relation and e["obj"] in id_to_name
    }
    objs.discard(anchor_surface)
    return next(iter(sorted(objs)), None)
```

**The genuinely-new bit -- the date is the slice time.** In `ask(mode="auto")`, the temporal branch
parses `profile.as_of -> D = int(...)` and slices `store.as_of(D, tx_t)`, **overriding the caller's
`valid_t`** (a temporal query carries its own time). A non-integer/missing `as_of` (or any parse
failure) falls through to the safe general mode -- never raises. Add the branch alongside the
slice-1 aggregate branch, right after the existing `slice_graph = store.as_of(valid_t, tx_t)` line:

```
    if mode == "auto":
        profile = classify_query(query, predicates=_slice_predicates(slice_graph))
        plan = plan_query(profile)
        if plan.mode == "aggregate" and profile.anchor_surface and profile.relation:
            return _format_aggregate(aggregate_members(slice_graph, profile.anchor_surface, profile.relation))
        if plan.mode == "as_of" and profile.anchor_surface and profile.relation and profile.as_of:
            try:
                d = int(profile.as_of)
            except ValueError:
                d = None
            if d is not None:
                obj = asof_object(store.as_of(d, tx_t), profile.anchor_surface, profile.relation)
                return obj if obj is not None else "(unknown)"
        mode = plan.mode  # fall through to local/hybrid/global
```

### 3. Gate (free, deterministic, key-free; extends the slice-1 router gate)

The slice-1 `router_eval.py` gate is extended to cover BOTH levers (one `ROUTER.md`, one
`RouterResult`). Reuses the B2 corpus (`generate_temporal`) + `goldengraph_asof` as the parity
reference.

**The representation fix (the design's load-bearing point):** B2's `build_temporal_store` names
nodes by **QID** (`surface_names=[anchor_id]`), but the question text uses the concept *surface*
(`by_id[src_id].canonical`). So question-text `seeds_by_name(concept)` cannot find a QID-named
anchor. The gate builds a **concept-surface-named** bi-temporal store: a gate-local
`_build_concept_named_temporal_store(facts, by_id)` mirroring `build_temporal_store`'s valid_to
windows but with `surface_names=[by_id[id].canonical]` and `canonical_name=[concept]` (record_keys
stay the QID so oracle merge is unchanged). Then `canonical_name == concept surface`,
`seeds_by_name(concept)` resolves, and the returned object name is the object's concept surface,
compared to the name-projected gold `by_id[q.gold_obj].canonical`. This is both necessary for
name-seeding AND more realistic than the QID-named bench store (a real ingest produces
concept-named nodes). B2's own bench is untouched.

Gate assertions (all HARD, added to slice-1's three):
4. **Temporal classifier** (wheel-free): every B2 question classifies `TEMPORAL_ASOF` with correct
   `anchor_surface == by_id[q.anchor_id].canonical`, `relation == q.relation`, `int(as_of) == q.D`.
5. **Routed as-of-accuracy** (wheel): route each question through `classify_query -> store.as_of(D)
   -> asof_object`; as-of-accuracy (vs `by_id[q.gold_obj].canonical`) == 1.0 in BOTH regimes (past +
   current). The capability is the PAST regime (a temporal-blind reader returns the corrected-away
   value; B2 floor = 0.0 there).

Thresholds frozen from the first measured run (verify-then-freeze). STOP-and-surface if routed
as-of-accuracy is not 1.0 in either regime (the engine-native as-of traversal or the
date->slice-time wiring is wrong).

### Opt-in real-LLM confirmation (`bench-graphrag-qa`, ungated)

Extend slice-1's `run_router_capability` lane: on the past-regime questions, compare answer-match of
`ask(mode="auto")` (routes to as-of, slices at D) vs `ask(mode="local")` (general, no temporal
slice). Shows routing to the as-of lever WINS on a corrected-away past value. Non-gating, `|| true`,
billing-blocked.

## Components / file structure

- `packages/python/goldengraph/goldengraph/route.py` (MODIFY): `_TEMPORAL_LEADIN_RE`, temporal slot
  extraction in `classify_query`, `plan_query` `as_of` rule (drop `not_yet_promoted`).
- `packages/python/goldengraph/goldengraph/answer.py` (MODIFY): `asof_object`, the `as_of` branch in
  `ask(mode="auto")`.
- `packages/python/goldengraph/tests/test_route.py` (MODIFY): temporal intent + slot tests.
- `packages/python/goldengraph/tests/test_aggregate_mode.py` (MODIFY) or a new
  `tests/test_asof_mode.py` (CREATE): `asof_object` + `ask(mode="auto")` temporal dispatch (wheel;
  importorskip). Build a tiny 2-window store (X-rel-A [1,5), X-rel-B [5,inf)) and assert the answer
  flips across D.
- `erkgbench/qa_e2e/router_eval.py` (MODIFY): `_build_concept_named_temporal_store`,
  `temporal_classifier_accuracy`, `run_temporal_routed_accuracy`, extend `RouterResult` +
  `run_router_deterministic` + `evaluate_assertions` + `render_router_md`.
- `tests/test_qa_router.py` (MODIFY): temporal classifier-accuracy + gate-shape for the new
  assertions.
- `.github/workflows/bench-graphrag-qa.yml` (MODIFY): the `run_router_capability` step already runs
  `--with-llm`; extend `run_router_llm` to add the temporal auto-vs-local comparison (no new input).

## Error handling

- `classify_query` never raises; a temporal query with an unparseable date -> `as_of` set but the
  `ask` branch's `int()` guard falls through to `local`.
- `asof_object` returns `None` (anchor not found / no edge in slice) -> the `ask` branch returns
  `"(unknown)"`; the gate scores it as a miss.
- `ask(mode="auto")` temporal branch never raises (parse-guarded) -> no regression vs slice 1.

## Testing strategy (TDD)

Per-task failing-test-first, ruff-clean per commit. `route.py` temporal tests + temporal
classifier-accuracy run wheel-free; `asof_object` + routed correctness need the wheel
(`goldengraph-pipeline`). Verify routed as-of-accuracy == 1.0 in both regimes on the real B2 corpus
before freezing thresholds.

## Open risks

- **Concept-name collisions / multi-object windows.** At a given D exactly one of (A,B) is valid per
  fact by construction (windows `[T1,tc)` and `[tc,inf)` partition time), so `asof_object` sees one
  object; `sorted(...)[0]` is a deterministic tie-break guard if a corpus ever violates that.
- **The date-overrides-`valid_t` semantics.** Documented and intentional: a temporal-routed query
  determines its own slice. Non-temporal modes still honor the caller's `valid_t`. Stated in the
  `ask` docstring.
- **If routed as-of-accuracy is not 1.0** in either regime, STOP and surface -- the as-of traversal
  or the date wiring is wrong; do not loosen the gate.
- **Bare-int scope.** Real dates are S3; the gate's claim is scoped to the engineered integer-time
  corpus, said in the render.
