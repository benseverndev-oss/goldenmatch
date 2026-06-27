# GoldenGraph query-router slice 3 -- LLM classifier tier + confidence hardening

**Status:** design
**Date:** 2026-06-27
**Owner:** Ben Severn
**Worktree:** `gg-llm-classifier` (branch `feat/goldengraph-llm-classifier`, STACKED on
`feat/goldengraph-temporal-mode` / slice 2 / PR #1284)

## Problem

The router's heuristic classifier (slices 1-2) only matches the engineered phrasing
(`"List all entities that X <rel>"`, `"As of D, what does X <rel>?"`). Real users phrase
queries arbitrarily ("who all does X work with?", "what was X's employer in year 3?"), which
the regex lead-ins miss -> the query routes to the general `local` mode instead of the
capability lever it should hit. The router is correct but brittle. Slice 3 adds a second
classifier tier: when the heuristic is low-confidence, an LLM classifies intent + extracts
slots from arbitrary NL. Heuristic-first, LLM last-resort, budget-capped -- the exact shape of
the ER controller's `LLMRefitPolicy`.

## Goal

Make the router robust to arbitrary NL, with MEASURED evidence. Deliver:

1. A `QueryClassifier` protocol + an `LLMQueryClassifier` + a two-tier `resolve_profile`
   (heuristic high-conf -> heuristic; low-conf -> injected LLM tier; abstain -> safe `local`).
2. `ask(mode="auto")` accepts an optional injected classifier (default None == heuristic-only
   == byte-identical to slices 1-2).
3. A small hand-authored PARAPHRASED-NL eval asset.
4. A free deterministic gate proving the heuristic MISSES the paraphrases AND a STUB classifier
   tier RECOVERS them (the escalation mechanism), plus an opt-in real-LLM accuracy row.

Slice 3 of the 4-slice router program (1 aggregate [merged #1283]; 2 temporal [#1284]; 3 LLM
tier [this]; 4 meta-kernel unifying ER + query controllers).

## Non-goals

- **No general-NL benchmark.** The paraphrase set is small, hand-authored, engineered -- it
  demonstrates the MECHANISM (heuristic-misses -> LLM-recovers), not a real NL distribution.
  Stated in the render.
- **The LLM tier is opt-in (default off).** `ask`'s default + the deterministic gate use NO LLM;
  default/CI behavior is byte-identical heuristic-only. Real-LLM accuracy is billing-blocked.
- No change to the heuristic classifier's canonical behavior, to the aggregate/as-of modes, or to
  `local`/`hybrid`/`global`.
- No streaming / multi-turn / conversational routing.

## Architecture

### 1. Classifier protocol + LLM tier + two-tier resolution (`goldengraph/route.py`, MODIFY)

```
class QueryClassifier(Protocol):
    def classify(self, query: str, *, predicates) -> QueryProfile: ...

class LLMQueryClassifier:
    def __init__(self, llm: LLMClient, *, max_calls: int = 5): ...
    def classify(self, query, *, predicates) -> QueryProfile:
        # budget guard (max_calls); prompt the LLM for {intent, anchor, relation, as_of} given the
        # query + the predicate vocab; defensive JSON parse (reuse extract.py's _strip_fence
        # pattern); map to QueryProfile. Confidence ~0.85 on a clean structured answer; on
        # abstain / unparseable / budget-exhausted -> QueryProfile(MULTI_HOP, confidence=0.0).

def resolve_profile(query: str, *, predicates=None, llm_classifier: QueryClassifier | None = None) -> QueryProfile:
    h = classify_query(query, predicates=predicates)         # heuristic FIRST
    if h.confidence >= MIN_CONF or llm_classifier is None:
        return h
    ll = llm_classifier.classify(query, predicates=predicates)  # LLM LAST-RESORT
    return ll if ll.confidence > h.confidence else h
```

- Heuristic-first: the LLM is called ONLY when the heuristic is below `MIN_CONF` AND a classifier
  is injected. The LLM result wins only if strictly more confident (so a confidently-abstaining LLM
  -> keep the heuristic's low-conf -> `plan_query` routes to `local`). No over-routing.
- The LLM classifier's intent/slot vocab is constrained: it must emit one of the `QueryIntent`
  values and a relation drawn from `predicates` (the prompt lists them); an out-of-vocab relation is
  dropped (-> low confidence -> abstain). Budget-capped + fail-open (any exception -> abstain).

### 2. Wire into `ask` (`goldengraph/answer.py`, MODIFY)

Add `query_classifier: object | None = None` to `ask`'s signature; in the `mode == "auto"` block,
replace `classify_query(query, predicates=_slice_predicates(slice_graph))` with
`resolve_profile(query, predicates=_slice_predicates(slice_graph), llm_classifier=query_classifier)`.
Default `None` -> `resolve_profile` returns the heuristic unchanged -> **byte-identical to slice 2**.
Additive; the clamp + aggregate/as_of branches are untouched.

### 3. Paraphrased-NL eval asset (er-kg-bench, CREATE)

`erkgbench/qa_e2e/router_paraphrases.py`: a hand-authored list of paraphrased questions, each a
`Paraphrase(question, intent, anchor_surface, relation, as_of)` carrying the gold slots, generated
from the SAME engineered universe (so anchor/relation/gold are real concept surfaces/predicates).
~12-20 entries spanning aggregation + temporal, deliberately phrased so the heuristic lead-in
regexes do NOT match (different verbs, word order, embedded dates). Deterministic (no LLM, no
randomness) so the gate is reproducible.

### 4. Gate (free, deterministic, key-free; extends the router gate)

A `StubClassifier` (deterministic): a dict keyed by the paraphrase question -> the gold
`QueryProfile` (the "oracle" an ideal LLM would return). **LOAD-BEARING: the StubClassifier MUST set
`confidence >= MIN_CONF` (use 1.0) on each oracle `QueryProfile`** -- `QueryProfile.confidence`
defaults to 0.0, and both `resolve_profile` (LLM wins only if `ll.confidence > h.confidence`) and
`plan_query` (gates `aggregate`/`as_of` on `confidence >= MIN_CONF`) would otherwise discard it and
the stub-escalation accuracy would read 0.0 instead of 1.0. (Same requirement holds for the real
`LLMQueryClassifier`: emit ~0.85 on a clean answer.) New `router_eval` functions + assertions:
1. **Heuristic perfect on canonical** -- unchanged (slices 1-2 verdicts stay green).
2. **Heuristic MISSES paraphrases (HARD):** the heuristic's slot-accuracy on the paraphrase set is
   <= a frozen ceiling (e.g. it routes < 50% correctly, likely ~0) -- proves the gap the LLM tier
   fills. (Frozen from the measured heuristic-on-paraphrases run.)
3. **Stub escalation RECOVERS (HARD):** `resolve_profile(q, predicates=..., llm_classifier=StubClassifier(...))`
   yields the correct `(intent, anchor, relation, as_of)` for EVERY paraphrase (slot-accuracy 1.0),
   AND `plan_query` on that profile picks the right specialized mode -- proving the escalation fires
   and the recovered slots flow to a correct plan. All deterministic (stub, no real LLM).

STOP-and-surface if the stub-escalated slot-accuracy is not 1.0 (the two-tier resolution or
`plan_query` is wrong), or if the heuristic somehow already handles the paraphrases (the asset
isn't actually exercising the gap -- re-author harder paraphrases).

### 5. Opt-in real-LLM accuracy (`bench-graphrag-qa`, ungated)

The existing `run_router_capability` workflow input already drives `run_router_eval --with-llm` ->
`run_router_llm`, which today measures `ask(mode=auto)`-vs-`ask(mode=local)` answer-setF1 over the B1
aggregation questions. Slice 3 ADDS a new measurement to that same lane (no new workflow input): a
`llm_classifier_accuracy(paraphrases, llm)` function that runs the REAL `LLMQueryClassifier` (via the
goldengraph `OpenAIClient`) over the paraphrase asset and reports intent-accuracy + slot-accuracy vs
the heuristic baseline (which fails them). `run_router_llm` calls it and `render_router_llm_md` adds
the rows. Shows the real LLM recovers the NL the heuristic can't. Budget-capped, `|| true`,
billing-blocked.

## Components / file structure

- `packages/python/goldengraph/goldengraph/route.py` (MODIFY): `QueryClassifier` Protocol,
  `LLMQueryClassifier`, `resolve_profile`.
- `packages/python/goldengraph/goldengraph/answer.py` (MODIFY): `query_classifier` kwarg + the one
  `resolve_profile` call swap.
- `packages/python/goldengraph/tests/test_route.py` (MODIFY): `resolve_profile` two-tier dispatch
  (stub classifier), `LLMQueryClassifier` parse (stub LLM returning JSON), budget guard, abstain.
- `packages/python/goldengraph/tests/test_aggregate_mode.py` or `test_asof_mode.py` (MODIFY): one
  `ask(mode="auto", query_classifier=<stub>)` test that a paraphrase the heuristic misses routes to
  the right lever via the injected stub (wheel).
- `erkgbench/qa_e2e/router_paraphrases.py` (CREATE): the hand-authored paraphrase fixture.
- `erkgbench/qa_e2e/router_eval.py` (MODIFY): `StubClassifier`, `heuristic_paraphrase_accuracy`,
  `stub_escalation_accuracy`, extend `RouterResult` + assertions + render; opt-in
  `llm_classifier_accuracy`.
- `tests/test_qa_router.py` (MODIFY): paraphrase heuristic-miss + stub-escalation + gate shape.
- `.github/workflows/bench-graphrag-qa.yml` (MODIFY): the `run_router_capability` step adds the
  real-LLM classifier accuracy (no new input).

## Error handling

- `LLMQueryClassifier.classify` is fail-open: budget-exhausted / exception / unparseable JSON /
  out-of-vocab relation -> `QueryProfile(MULTI_HOP, confidence=0.0)` (abstain) -> `resolve_profile`
  keeps the heuristic -> safe `local` route. Never raises into `ask`.
- `resolve_profile` with `llm_classifier=None` is a pure pass-through to the heuristic.
- `ask` default (`query_classifier=None`) is byte-identical to slice 2.

## Testing strategy (TDD)

Per-task failing-test-first, ruff-clean per commit. `resolve_profile` + `LLMQueryClassifier`
(stub-LLM) + the paraphrase gate are wheel-free; the one `ask(...query_classifier=stub)` routing
test needs the wheel (goldengraph-pipeline). Verify the heuristic-miss ceiling + stub-recovery 1.0
on the real paraphrase asset before freezing.

## Open risks

- **The paraphrase asset must actually defeat the heuristic.** If a paraphrase accidentally matches
  a lead-in regex, the heuristic-miss assertion weakens. The gate asserts heuristic slot-acc <=
  ceiling, so a too-easy asset fails the gate (forcing harder paraphrases) -- self-correcting.
- **The stub is an oracle, not the real LLM.** The deterministic gate proves the MECHANISM (a
  correct tier-2 answer flows through); it does NOT prove the real LLM is accurate -- that's the
  opt-in row's job (billing-blocked). Stated honestly in the render.
- **Budget / cost.** The LLM tier is one extra call per low-confidence query, capped by `max_calls`;
  default-off means zero cost unless a caller injects it. The opt-in lane is budget-capped.
- **Confidence thresholds are heuristic.** `MIN_CONF` (0.8) gates escalation; the LLM's 0.85
  "confident" value is a constant, not calibrated. Good enough for tier selection (the only decision
  is heuristic-vs-LLM); real calibration is out of scope.
