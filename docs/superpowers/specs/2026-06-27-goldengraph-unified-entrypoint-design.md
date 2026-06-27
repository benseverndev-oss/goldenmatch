# GoldenGraph slice 4c -- unified entry point (GoldenGraph facade + ExecutionPlan surface + cross-controller budget)

**Status:** design
**Date:** 2026-06-27
**Owner:** Ben Severn
**Worktree:** `gg-unified-entry` (branch `feat/goldengraph-unified-entrypoint`, off main; 4a #1286 + 4b #1287 MERGED)

## Problem

Slices 1/2/3 built the QUERY controller (`ask(mode="auto")` routes one query to aggregate/as_of/local/
hybrid/global). Slices 4a/4b built the meta-kernel JOIN (`plan_resolver(workload) -> (UnifiedPlan,
Resolver)` picks the ER resolver tier and makes it executable). But nothing TIES build + answer
together: a caller still manually runs `plan_resolver(workload)` -> `ingest_corpus(docs, resolver=...)`
-> `ask(q, mode="auto")` per query, with no shared budget and no visibility into the scale plan the ER
controller would pick. Slice 4c -- the program's closing slice -- is the single stateful entry point
that runs the whole arc and unifies the two controllers over the shared resolved-graph substrate.

## Goal

A `GoldenGraph` facade with three integrated pieces (all approved for this slice):

1. **Unified entry point.** `GoldenGraph.build(docs, workload, *, llm, embedder, ...) -> GoldenGraph`
   then `gg.ask(query, *, valid_t, tx_t, mode="auto")`. One object holds the store + chosen resolver +
   plan + budget; `build` profiles the workload (4a/4b), builds with the chosen tier-resolver, and
   `ask` routes via the slice-1/2/3 auto controller. Mirrors LightRAG's object API (the head-to-head
   framing).
2. **ExecutionPlan surface.** `gg.execution_plan` exposes the goldenmatch controller-v3 `ExecutionPlan`
   the ER controller would pick for the aggregate ER workload at this corpus scale (introspection of
   "delegate scale to the ER controller", made honest -- see Non-goals for why it is ADVISORY).
3. **Cross-controller budget.** One `Budget` ceiling threaded through BOTH the build-LLM (extraction)
   and the answer-LLM (classification + synthesis) via a shared `_BudgetedLLM` wrapper; exhaustion
   raises `BudgetExhausted`. Build and answer draw from one pool.

Slice 4c of the meta-kernel (4a join / 4b wire-into-build / 4c unified entry point -- THIS). Closes the
KG/RAG query-routing controller program.

## Non-goals (honest scope, stated up front like 4a/4b)

- **The surfaced ExecutionPlan is ADVISORY; the build path does NOT consume it.** The resolver runs
  INTRA-document inside `ingest` (each `_prepare_doc` resolves one doc's handful of mentions), so every
  actual `dedupe_df` call is on a tiny frame and would always plan "single-box/simple" -- that per-doc
  plan is uninteresting. The MEANINGFUL scale decision is over the AGGREGATE corpus ER workload, which
  the per-doc build never sees at once. So `plan_er_execution` asks the ER controller's planner "what
  plan WOULD you pick at this total scale" and surfaces it as the signal to flip to a batched/
  distributed resolution path at scale. Wiring the build to consume it (batched whole-corpus
  resolution) is FUTURE work, not 4c. Mirrors 4b's "nothing forces ingest to consult the planner".
- **Budget is a `len//4` token ESTIMATE, not a real tokenizer.** Deterministic + provider-agnostic
  (the same reason the evidence harness's `_CountingLLM` uses `len//4`; goldengraph `OpenAIClient`
  takes no budget object -- the wrapper sits OUTSIDE it). A real-token budget is a future refinement.
- **No incremental/streaming budget refund or per-mode sub-budgets.** One flat ceiling across the run.
- **No new query-routing or resolver-tier logic.** 4c COMPOSES slices 1/2/3 (ask auto) + 4a/4b
  (plan_resolver) unchanged; it adds the facade, the plan surface, and the budget seam only.
- **`GoldenGraph` does not own store lifecycle/persistence.** It accepts/creates a `PyStore` for the
  run; snapshotting/reopening is the existing store API's job.

## Architecture

### 1. Budget seam (`goldengraph/budget.py`, NEW)

```
@dataclass
class Budget:
    total_tokens: int | None = None      # None -> unbounded
    spent_tokens: int = 0
    def remaining(self) -> float:        # inf when unbounded
    def would_exceed(self, n: int) -> bool:   # False when unbounded
    def record(self, n: int) -> None:

class BudgetExhausted(RuntimeError): ...

class _BudgetedLLM:                       # wraps an LLMClient; ONE budget across build+answer
    def __init__(self, llm, budget: Budget): ...
    def complete(self, prompt: str) -> str:
        est_in = max(1, len(prompt) // 4)
        if self._budget.would_exceed(est_in):
            raise BudgetExhausted(f"budget exhausted: {self._budget.spent_tokens}/{self._budget.total_tokens}")
        out = self._llm.complete(prompt)
        self._budget.record(est_in + max(1, len(out) // 4))
        return out
```

`_BudgetedLLM` satisfies the `LLMClient` protocol (`complete(str) -> str`) so it drops into BOTH
`ingest_corpus(llm=...)` and `ask(llm=...)`. The estimate is charged BEFORE the call (so an over-budget
call raises without spending); the output's tokens are recorded after. Unbounded budget (`total_tokens
is None`) is a pure pass-through (`would_exceed` always False), so the default facade behavior is
unchanged from calling the bare LLM.

### 2. ExecutionPlan surface (`goldengraph/graph.py`, NEW -- helper)

```
def plan_er_execution(docs, *, corpus_records: int | None = None) -> ExecutionPlan:
    """Surface the ER controller's scale plan for the aggregate ER workload (ADVISORY)."""
    n_rows_full = corpus_records if corpus_records is not None else _estimate_records(docs)
    from goldenmatch.core.runtime_profile import capture_runtime_profile
    from goldenmatch.core.autoconfig_planner import apply_planner_rules, _default_rules
    from goldenmatch.core.complexity_profile import ComplexityProfile
    runtime = capture_runtime_profile()
    profile = _representative_complexity(n_rows_full)
    return apply_planner_rules(profile, runtime, n_rows_full, _default_rules())
```

- `_estimate_records(docs)`: a deterministic heuristic for total ER records when the caller gives no
  `corpus_records` -- e.g. `sum over docs of an entity-token estimate` or simply `len(docs) *
  _AVG_ENTITIES_PER_DOC`. The number only feeds the planner's scale rules; document the heuristic.
- `_representative_complexity(n_rows_full)`: build the `ComplexityProfile` the planner needs.
  **KNOWN-UNKNOWN (resolve in implementation):** if constructing a full `ComplexityProfile` is heavy,
  use a MINIMAL/representative profile -- the scale rules key off `n_rows_full` + `runtime.
  available_ram_gb` (the driver-RAM routing), so a minimal profile still yields a meaningful scale
  plan. The gate's "plan scales with corpus size" assertion is what proves the planner is really
  consulted (a constant fails it).
- Returns goldenmatch's real `ExecutionPlan` (carries `rule_name`, backend/mode). Needs goldenmatch ->
  the helper + its tests run in the goldengraph-pipeline lane (goldenmatch installed there).

### 3. GoldenGraph facade (`goldengraph/graph.py`, NEW)

```
class GoldenGraph:
    @classmethod
    def build(cls, docs, workload, *, llm, embedder, predicates=None, llm_classifier=None,
              budget: Budget | None = None, resolver=None, corpus_records=None,
              store=None) -> "GoldenGraph":
        budget = budget or Budget()
        plan, planned_resolver = plan_resolver(workload, predicates=predicates,
                                               llm_classifier=llm_classifier)        # 4a/4b
        execution_plan = plan_er_execution(docs, corpus_records=corpus_records)      # piece 2
        store = store if store is not None else _new_store()
        bllm = _BudgetedLLM(llm, budget)
        ingest_corpus(docs, store, llm=bllm, resolver=resolver or planned_resolver, embedder=embedder)
        return cls(store=store, plan=plan, execution_plan=execution_plan, budget=budget,
                   llm=bllm, embedder=embedder, llm_classifier=llm_classifier)

    def ask(self, query, *, valid_t, tx_t, mode="auto", **ask_kwargs) -> str:
        return ask(query, self._store, llm=self._llm, embedder=self._embedder,
                   valid_t=valid_t, tx_t=tx_t, mode=mode,
                   query_classifier=self._llm_classifier, **ask_kwargs)              # 1/2/3

    # read-only properties: plan, execution_plan, budget, store
```

- `_new_store()`: `from goldengraph_native import _native as ggn; return ggn.PyStore()` (the native
  wheel). Importing it makes `build` (with the default store) need the wheel -> e2e gate runs in the
  goldengraph-pipeline lane; callers can inject `store=` for wheel-free composition tests.
- `resolver` injectable (default = the planned tier-resolver); `store` injectable; `budget` injectable
  (default unbounded). The SAME `bllm` is held and reused by `ask`, so build + every ask share `budget`.
- `ask` forwards `mode` + `ask_kwargs` (k/hops/passages/passage_k/...) straight to the slice-1/2/3
  `ask`; `mode="auto"` is the default (the whole point -- one entry, auto-routed answers).

## Components / file structure

- `packages/python/goldengraph/goldengraph/budget.py` (NEW): `Budget`, `BudgetExhausted`, `_BudgetedLLM`.
- `packages/python/goldengraph/goldengraph/graph.py` (NEW): `plan_er_execution`, `_estimate_records`,
  `_representative_complexity`, `_new_store`, `GoldenGraph`.
- `packages/python/goldengraph/tests/test_budget.py` (NEW): wheel-free Budget + _BudgetedLLM.
- `packages/python/goldengraph/tests/test_graph_facade.py` (NEW): facade composition (injected store +
  stub LLM/embedder + injected resolver, runs the REAL PyStore via the wheel where present; the
  budget-sharing assertions are wheel-free with an injected store stub OR run in the lane).
- `erkgbench/qa_e2e/unified_entry_eval.py` (NEW): the three-verdict gate + `TierResult`-style result.
- `erkgbench/qa_e2e/run_unified_entry_eval.py` (NEW): CLI -> UNIFIED_ENTRY.md.
- `erkgbench/qa_e2e/.../tests/test_qa_unified_entry.py` (NEW): wheel-free gate-shape.
- `.github/workflows/goldengraph-pipeline.yml` (MODIFY): gate step + upload (after Upload TIER.md).
- `.github/workflows/bench-er-kg.yml` (MODIFY): add `tests/test_qa_unified_entry.py` to the pure-Python
  list.

## Gate (free, deterministic, key-free)

Three verdicts (HARD where noted), `run_unified_entry_eval.py` -> `UNIFIED_ENTRY.md` + `gate_exit_code`:

1. **End-to-end routing (HARD; real PyStore, stub LLM/embedder, injected resolver):** `GoldenGraph.
   build(docs, capability_workload, ...)` selects `plan.resolution_tier == FUZZY`; an AGGREGATE query
   through `gg.ask(mode="auto")` returns the EXACT gold member set. Reuses the slice-1 setup
   (engineered universe, `ambiguity=0.0` so `seeds_by_name(canonical)` resolves, NAME-space comparison
   `{by_id[m].canonical for m in gold}`). Proves the facade composes profile->build->auto-answer.
2. **Cross-controller budget sharing (HARD; wheel-free, deterministic):** with one `Budget`, after
   `build` (using an injected store stub) `budget.spent_tokens > 0`; after an `ask` it is STRICTLY
   greater (answer drew from the SAME pool). With a tiny `total_tokens`, `build`/`ask` raises
   `BudgetExhausted`. No real LLM (StubLLM + len//4).
3. **Plan scales with corpus size (HARD; needs goldenmatch planner):** `plan_er_execution(docs,
   corpus_records=SMALL).rule_name != plan_er_execution(docs, corpus_records=HUGE).rule_name` (or the
   plan's backend/mode differs) -- a CONSTANT fails this, so it proves the ER controller's planner is
   really consulted at scale. SMALL/HUGE frozen from a measured run (verify-then-freeze); if the two
   plans do NOT differ at any reachable scale, STOP-and-surface (the planner isn't scale-sensitive on
   this box, or the profile construction is wrong).

## Error handling

- `Budget.record`/`would_exceed` total over `total_tokens is None` (unbounded -> never exceeds).
- `_BudgetedLLM` raises `BudgetExhausted` BEFORE delegating when the input estimate would exceed -- so
  an over-budget run fails fast without a partial LLM call; build raising mid-corpus leaves an
  incomplete store (documented; the caller sizes the budget).
- `plan_er_execution` never raises on a well-formed corpus; `corpus_records<=0` or empty docs ->
  `n_rows_full = 0` -> the planner's default/smallest-scale rule.
- `GoldenGraph.build` with `store=None` needs the native wheel; a clear ImportError if absent (e2e
  lane has it; wheel-free tests inject `store=`).

## Testing strategy (TDD)

Per-task failing-test-first, ruff-clean per commit. `budget.py` + `_BudgetedLLM` + the budget-sharing
gate are WHEEL-FREE (StubLLM, injected store stub) and run anywhere. `plan_er_execution` needs
goldenmatch -> goldengraph-pipeline lane. The end-to-end routing verdict needs the real PyStore (native
wheel) -> goldengraph-pipeline lane (which already builds the wheel + installs goldenmatch + the
standalone goldengraph pkg). `test_qa_unified_entry.py` is the wheel-free gate-SHAPE test (hand-built
result), added to the bench-er-kg pure-Python list. Verify the plan-scales SMALL/HUGE rule_names on a
real run before freezing them; if the planner doesn't separate, surface to Ben.

## Open risks

- **`ComplexityProfile` construction (the known-unknown).** A full profile may be heavy to synthesize;
  fall back to a minimal/representative one (scale rules key off n_rows_full + available_ram_gb). The
  "plan scales" gate is the guard -- if a minimal profile makes the plan constant across scale, that
  verdict goes RED and forces a real profile (or surfaces that the box can't separate the plans).
- **ExecutionPlan ADVISORY honesty.** The surfaced plan does not change the per-doc build; it is the
  scale SIGNAL. Stated in Non-goals + the facade docstring so it is not over-claimed.
- **Budget estimate fidelity (`len//4`).** Deterministic + provider-agnostic by design; a real-token
  budget is future. The gate asserts SHARING + exhaustion semantics, not token accuracy.
- **Plan-scales determinism across runners.** `rule_name` at a fixed (n_rows_full, profile) should be
  deterministic given DEFAULT_RULES; `available_ram_gb` varies by box but the SMALL-vs-HUGE separation
  (not the absolute rule) is what is asserted, robust to RAM differences. Freeze the corpus_records
  endpoints far apart (e.g. 1k vs 500M) for a stable separation.
