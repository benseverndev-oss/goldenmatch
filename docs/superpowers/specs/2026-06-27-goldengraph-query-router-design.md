# GoldenGraph KG/RAG query-routing controller -- slice 1 (router kernel + aggregate lever)

**Status:** design
**Date:** 2026-06-27
**Owner:** Ben Severn
**Worktree:** `gg-query-router` (branch `feat/goldengraph-query-router`)

## Problem

GoldenGraph has distinct query modes (`local`/`hybrid`/`global` LLM-synthesis today; aggregation
and temporal-as-of traversals proven in the capability program but living only as bench functions).
The capability scorecard (slices A-D) showed each mode wins a DIFFERENT query class: aggregation
traversal crushes RAG on set/count, temporal as-of on dated queries, hybrid/RAG on prose multi-hop.
But the engine has no controller that, given a query, picks the right mode. The caller must know to
ask for `mode="aggregate"` -- which does not even exist as an `ask` mode.

The vision: an auto-config kernel for the KG/RAG query layer, analogous to the ER auto-config
controller (which profiles the DATA and a rule planner emits an ExecutionPlan). The query controller
profiles the QUERY and a rule planner emits a RetrievalPlan, so a single `ask(mode="auto")` routes
each query to the mode that wins it. ER quality is the shared substrate every mode exploits; a later
meta-kernel unifies the two controllers. This slice builds the first, load-bearing piece.

## Goal

Slice 1 of the query-router program. Deliver:

1. **The routing kernel** (`goldengraph/route.py`): `QueryProfile` (intent + extracted slots +
   confidence) <- `classify_query` (heuristic), and `RetrievalPlan` <- `plan_query` (rule table).
2. **Aggregation as a first-class, LLM-free `ask` mode** (`goldengraph/answer.py`): NL query ->
   (anchor, relation) slots -> engine-native `seeds_by_name` + 1-hop predicate-filtered traversal
   -> member set. Parity-locked to the B1 bench `goldengraph_aggregate`.
3. **`ask(mode="auto")`**: classify -> plan -> dispatch. Additive; existing modes unchanged.

A free, deterministic gate proves the router classifies aggregation queries and routes them to a
correct aggregate result (LLM-free). An opt-in real-LLM row shows routing-to-aggregate beats
routing-to-local on the same questions.

This is the first of: slice 2 (promote temporal as-of + route to it), slice 3 (confidence
hardening + LLM-assisted classifier tier), slice 4 (meta-kernel unifying ER + query controllers).

## Non-goals

- **No LLM classifier this slice.** Heuristic tier only (mirrors `HeuristicRefitPolicy`); an
  `LLMClassifier` is a documented seam for slice 3. Honest scope: heuristic classification is
  validated on the ENGINEERED corpus phrasing, NOT a claim of robust general-NL routing.
- **`temporal_asof` is classified but NOT routed to a winning mode** this slice (that mode lands in
  slice 2). `plan_query` routes it to `local` with an explicit `not_yet_promoted` marker so the plan
  is honest about which levers are live.
- No change to the ER controller or to the existing `local`/`hybrid`/`global` synthesis paths.
- No meta-kernel / no ER+query unification (slice 4).

## Architecture

### 1. Routing kernel (`goldengraph/route.py`, pure-Python, wheel-free)

```
class QueryIntent(str, Enum): AGGREGATE / TEMPORAL_ASOF / MULTI_HOP / LOOKUP

@dataclass
class QueryProfile:
    intent: QueryIntent
    anchor_surface: str | None   # the entity the query pivots on (aggregate/lookup)
    relation: str | None         # predicate (underscored), when the phrasing names one
    as_of: str | None            # raw date/qualifier when TEMPORAL_ASOF
    confidence: float            # 0..1 heuristic strength

def classify_query(query: str) -> QueryProfile: ...

@dataclass
class RetrievalPlan:
    mode: str                    # "aggregate" | "local" | "hybrid" | "global"
    note: str | None             # e.g. "not_yet_promoted" for temporal this slice
    params: dict                 # passage_k / hops / k overrides

def plan_query(profile: QueryProfile) -> RetrievalPlan: ...
```

- `classify_query` (heuristic): set/count phrasing (`list all`, `how many`, `which ... that
  <relation>`) -> AGGREGATE + extract `anchor_surface` (entity between "that"/"does" and the
  relation words) + `relation` (relation words underscored). Temporal qualifiers (`as of`,
  `in <year>`, `at the time`, `before/after <date>`) -> TEMPORAL_ASOF + `as_of`. Else MULTI_HOP
  (open prose). `confidence` from match specificity (a clean "list all X that <rel>" = high; a bare
  set-word = low).
- `plan_query` rule table: AGGREGATE -> `aggregate` (HIGH conf) else `local` (low-conf fallback);
  TEMPORAL_ASOF -> `local` + `note="not_yet_promoted"` (slice 2 flips this to `as_of`); MULTI_HOP ->
  `hybrid`; LOOKUP -> `local`. **Confidence floor:** below `MIN_CONF` any intent routes to the safe
  general mode (`local`/`hybrid`), never to a specialized mode on a weak signal.

### 2. Aggregate as a first-class `ask` mode (`goldengraph/answer.py`, needs the wheel)

```
def aggregate_members(slice_graph, anchor_surface: str, relation: str) -> set[str]:
    """Engine-native exact aggregation: seed the anchor by name, 1-hop ball, return the
    canonical NAMES (surfaces) of objects on edges (subj in seeds, predicate==relation).
    LLM-FREE. NOTE the representation: this returns canonical NAMES, whereas the bench
    `goldengraph_aggregate` returns canonical IDS -- the gate compares in name space (see
    Gate). `seeds_by_name` returns a LIST (every node whose canonical/merged surface equals
    the name); on the oracle-keyed gate store all of an anchor's mentions merge into one node,
    so the seed set is effectively a single node (surface collisions are out-of-scope here)."""
    seeds = slice_graph.seeds_by_name(anchor_surface)
    if not seeds:
        return set()
    sub = slice_graph.query(seeds, 1)
    id_to_name = {e["entity_id"]: e["canonical_name"] for e in sub.get("entities", ())}
    seedset = set(seeds)
    return {
        id_to_name[e["obj"]]
        for e in sub.get("edges", ())
        if e["subj"] in seedset and e["predicate"] == relation and e["obj"] in id_to_name
    }
```

A thin `_format_aggregate(members) -> str` renders the set for the `ask` string return. The
SET-returning `aggregate_members` is the tested/gated unit (so the gate scores set-F1 directly, not
a parsed string). Uses the latest slice (`store.as_of(valid_t, tx_t)` with the caller's clock; for
aggregation the caller passes the store's current time, same convention as B1).

### 3. `ask(mode="auto")` dispatch (`goldengraph/answer.py`)

```
# placed AFTER the existing `slice_graph = store.as_of(valid_t, tx_t)` line so it reuses it
if mode == "auto":
    profile = classify_query(query)
    plan = plan_query(profile)
    if plan.mode == "aggregate" and profile.anchor_surface and profile.relation:
        return _format_aggregate(aggregate_members(slice_graph, profile.anchor_surface, profile.relation))
    mode = plan.mode  # fall through to the existing local/hybrid/global path
# ... existing body unchanged ...
```

Additive: `auto` is opt-in; explicit modes are byte-identical. `auto` with a low-confidence or
non-aggregate profile flows into today's `local`/`hybrid` path, so it never regresses.

## Gate (free, deterministic, key-free; in `goldengraph-pipeline.yml`)

Reuses the B1 aggregation corpus. **Pinned to `ambiguity=0.0`** for the routed-correctness row (see
why below). A new `erkgbench/qa_e2e/router_eval.py` + `run_router_eval.py` + `test_qa_router.py`:

1. **Classifier accuracy (wheel-free):** `classify_query` labels the B1 list-questions as AGGREGATE
   and a held-out set of non-aggregation phrasings (multi-hop / lookup templates) as NOT-aggregate,
   above a frozen accuracy threshold. Slot assertion: the extracted `anchor_surface` equals the
   question's true anchor SURFACE `by_id[q.anchor_id].canonical` (NOT the QID `q.anchor_id`), and
   `relation` equals `q.relation`.
2. **Routed aggregate correctness (needs wheel), at `ambiguity=0.0`:** build the B1 store at
   `ambiguity=0.0` via the oracle-keyed `_build_store` path. For each list-question, the FULL router
   path (`classify_query` -> `aggregate_members`) returns a set EQUAL to the NAME-PROJECTED gold
   `{by_id[m].canonical for m in q.gold_members}`, i.e. set-F1 == 1.0. (Optional informative
   cross-check: the same set equals the bench `goldengraph_aggregate` projected to names via
   `g.canonical_name`.) Proves the router picks the right lever AND the lever computes the exact set,
   no LLM.

**Why `ambiguity=0.0` (resolves the spec-review BLOCKER + MAJOR):** (a) the engine `aggregate_members`
returns canonical NAMES while the bench/gold are canonical IDS -- comparing requires one
representation, so the gate projects gold IDS through names (`by_id[id].canonical`). (b) name-seeding
(`seeds_by_name(anchor_surface)`) only resolves if the anchor's canonical surface was actually rendered
into the store; at `ambiguity>0` `_render_mention` emits a variant with probability `ambiguity`, so a
small-set anchor may never appear under its canonical surface and the seed misses. At `ambiguity=0.0`
every mention is the canonical surface, so the anchor node's `canonical_name` == the concept AND
`seeds_by_name(concept)` resolves -- making name-space, name-seeded correctness exact and reproducible.
Higher-ambiguity corpora are for the robustness/LLM rows, not this deterministic gate. (The bench's
id-seeded coverage path is ER-robust by construction; the engine's name-seeded path is what a real
caller has, so testing it at `ambiguity=0` is the honest scope for "does the lever traverse right".)

Gate HARD on (1) classifier accuracy >= frozen threshold and (2) routed correctness set-F1 == 1.0 at
`ambiguity=0.0`. Verify-then-freeze the classifier threshold from the first measured run (per B1/C/D).
STOP-and-surface if the heuristic classifier can't cleanly separate aggregation from non-aggregation
on the engineered phrasing (the routing signal would be too weak even on the easy corpus), or if
routed correctness is not 1.0 at `ambiguity=0.0` (the engine-native aggregate traversal is wrong).

### Opt-in real-LLM confirmation (`bench-graphrag-qa`, ungated)

`run_router_eval --with-llm`: on the same list-questions, compare answer-match of `ask(mode="auto")`
(routes to aggregate) vs `ask(mode="local")` (the general mode). Shows routing to the right lever
WINS, not just matches a reference. Budget-capped, `run_router_capability` input, non-gating.

## Components / file structure

- `packages/python/goldengraph/goldengraph/route.py` (CREATE): the kernel (intent enum, QueryProfile,
  classify_query, RetrievalPlan, plan_query). Pure-Python, no wheel.
- `packages/python/goldengraph/goldengraph/answer.py` (MODIFY): `aggregate_members`,
  `_format_aggregate`, and the `mode == "auto"` branch in `ask`.
- `packages/python/goldengraph/tests/test_route.py` (CREATE): classify_query intents + slot
  extraction + plan_query rules + confidence floor (pure-Python).
- `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/router_eval.py` (CREATE):
  classifier-accuracy + routed-correctness (name-space, ambiguity=0.0) over the B1 corpus; gate
  verdicts + render.
- `.../qa_e2e/run_router_eval.py` (CREATE): CLI (deterministic + `--with-llm`).
- `.../tests/test_qa_router.py` (CREATE): wheel-free classifier-accuracy + gate shape.
- `.github/workflows/goldengraph-pipeline.yml` (MODIFY): router gate step + upload.
- `.github/workflows/bench-er-kg.yml` (MODIFY): wheel-free router test on the pure-Python list.
- `.github/workflows/bench-graphrag-qa.yml` (MODIFY): `run_router_capability` opt-in step.

## Error handling

- `classify_query` never raises on any string; an unrecognized query -> MULTI_HOP, confidence 0 ->
  `plan_query` routes to the safe general mode.
- `aggregate_members` returns `set()` when the anchor isn't found (no seed) -> `_format_aggregate`
  renders an empty answer; the gate scores it as a miss, not an error.
- `ask(mode="auto")` with a bad/low-confidence profile falls through to `local` -> no regression vs
  today.

## Testing strategy (TDD)

Per-task failing-test-first, ruff-clean per commit. Classifier + plan + gate-shape run wheel-free
(`route.py` is pure-Python; `goldengraph` standalone pkg on PYTHONPATH). `aggregate_members` +
routed-parity need the wheel -> the `goldengraph-pipeline` lane. Verify classifier accuracy on the
real B1 phrasing before freezing the threshold.

## Open risks

- **Heuristic classification is corpus-shaped.** It will classify the engineered "List all entities
  that X <rel>" cleanly; real NL ("who all does X work with?") is the slice-3 LLM tier's job. The
  gate's claim is scoped to the engineered phrasing; the render says so.
- **Anchor/relation slot extraction vs the predicate vocabulary.** The relation words must map to the
  stored predicate (underscore-join). If a relation phrase doesn't round-trip to a real predicate,
  `aggregate_members` returns an empty set -> a miss the gate catches. Extraction is validated
  against the B1 questions' true anchor SURFACE (`by_id[q.anchor_id].canonical`, NOT the QID) +
  relation in the classifier-accuracy gate.
- **Representation + name-seeding (resolved by the `ambiguity=0.0` pin).** `aggregate_members` works
  in NAME space (`seeds_by_name`, `canonical_name`), whereas the bench/gold are canonical IDS. The
  deterministic gate runs at `ambiguity=0.0` and compares the engine's name-set to the gold IDS
  projected through `by_id[id].canonical`; at `ambiguity=0` the canonical surface is always rendered
  (so `seeds_by_name` resolves) and the node `canonical_name` == the concept (so name-projection is
  exact). This is the honest scope: it tests the engine-native traversal a real caller gets, with ER
  held perfect; ER-under-ambiguity is slice A's concern, not this lever's.
- **If routing doesn't win (opt-in).** If `auto` (aggregate) does NOT beat `local` on answer-match,
  that is a real finding about whether the aggregate mode's exactness survives formatting/synthesis;
  report it, do not gate on it (the deterministic gate is parity, which is LLM-free and robust).
