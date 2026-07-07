# Plan — GoldenGraph path-aware retrieval

**Spec:** `docs/superpowers/specs/2026-07-07-goldengraph-path-aware-retrieval-design.md`.
**Product code:** `packages/python/goldengraph/goldengraph/{answer.py,subgraph_filter.py,route.py}`
(+ its own test suite). **Bench:** `packages/python/goldenmatch/benchmarks/er-kg-bench/`.

## Phase 0 — findings that shape this plan (resolved while planning)

- **The head-to-head runs `mode="local"`.** `engines/goldengraph.py::_QA_MODE` defaults to
  `local` and `bench-graphrag-qa.yml` does not override it. So `trace_chain` / the `auto`
  planner is NOT in play in the measured numbers (spec open-Q1 → resolved: **local**).
  ⇒ Lever B (route local→auto) is a real candidate, not a no-op.
- **The ablation bypasses `ask`.** `scorecard_llm.answer_match_ablation` calls `_retrieve_local`
  + `synthesize_local` directly (deterministic coverage-seeding, NO embedder). So a lever that
  lives in `ask` is not automatically exercised by the ablation — the measurement harness must
  apply the lever on the SAME deterministically-seeded local path, and the lever logic must be a
  reusable function both `ask` and the bench call (no logic buried inside `ask`).
- **Recall guard is free.** `bridge_recall(chain, ball, coverage)` is LLM-free — every lever is
  first checked for "does the pruned/walked subgraph still contain the chain?" before spending
  on the answer-match LLM run.

## Phase 1 — Lever A: wire the EXISTING topology filter into `local` (gated), measure

`filter_subgraph_to_paths` is already reusable + predicate-blind (dodges the 2026-06-22 trap).
Cheapest lever; expected to help only when the answer entity is among the `k` seeds (single-
anchor multi-hop blind spot) — the recall guard quantifies that.

- **Task 1.1 (product, TDD).** `goldengraph/answer.py`: in the `local` branch of `ask`, add a
  `GOLDENGRAPH_LOCAL_FILTER` gate (`""`/`none`=off default, `path`=on) applying
  `filter_subgraph_to_paths(subgraph, seeds, halo=_local_filter_halo())` before
  `synthesize_local` — mirror the existing hybrid wiring (lines ~333-337). `halo` from
  `GOLDENGRAPH_LOCAL_FILTER_HALO` (default 1).
  - **Test (goldengraph suite):** `tests/test_answer_local_filter.py` — (a) gate OFF ⇒ `ask`
    byte-identical to today (mock LLM records the subgraph it received; assert unchanged);
    (b) gate ON ⇒ the subgraph handed to synthesis == `filter_subgraph_to_paths(ball, seeds)`.
    No network (stub LLM + a hand-built store slice).
  - **Run:** `cd packages/python/goldengraph && python -m pytest tests/test_answer_local_filter.py -q`.
  - **Commit:** `feat(goldengraph): gated path-preserving filter on the local retrieval ball`.
- **Task 1.2 (bench measurement + recall guard).** `erkgbench/qa_e2e/`: add
  `retrieval_levers.py::measure_lever(corpus, g, typ_of, llm, *, lever)` that reruns the
  `oracle`/`goldengraph` dials' local path with the lever applied between `_retrieve_local` and
  `synthesize_local`, returning per-dial answer-match AND per-dial bridge-recall of the
  post-lever subgraph. `lever="filter_path"` calls `filter_subgraph_to_paths`.
  - **Test:** `tests/test_qa_retrieval_levers.py` — offline stub LLM; assert (a) the recall
    guard is computed on the POST-lever subgraph, (b) `lever="none"` reproduces
    `answer_match_ablation`'s numbers on a tiny fixture. Pure, no wheel needed if the store bits
    are stubbed; else `importorskip("goldengraph_native")`.
  - **Commit:** `test+feat(erkgbench): lever measurement harness + bridge-recall guard`.
- **Task 1.3 (measure — PAID, opt-in).** Run `measure_lever(lever="filter_path")` at
  amb ∈ {0, 0.5, 1.0}, n=40, `gpt-4o-mini`. **Gate on the recall guard first** (LLM-free): if the
  filtered subgraph's bridge-recall drops vs the ball, STOP — the lever strands answers (that's
  the single-anchor blind spot); do not spend on answer-match. Record the answer-match delta vs
  the 0.275/0.372 baseline in `results/RESULTS_PATH_AWARE_RETRIEVAL.md`.
  - **Decision gate:** answer-match up AND recall not down ⇒ Lever A is a win → Phase 4. Else →
    Phase 2.

## Phase 2 — Lever B: route `local` multi-hop through the existing `trace_chain` (measure)

`trace_chain` already solves path-finding LLM-free, but needs a query PLAN
(`anchor_surface` + `relation_chain`) from `resolve_profile`/`plan_query` — which `local`
doesn't build. First a config test, then (only if needed) real work.

- **Task 2.1 (scout, no code).** Read `route.py::resolve_profile`/`plan_query`: does producing a
  `chain` plan require an LLM query-classifier, or is it heuristic/regex? Record the answer in
  the plan-of-record. (If heuristic → B is nearly free; if it needs a classifier → that IS the
  work, and it competes with Lever C.)
- **Task 2.2 (config measurement — PAID, opt-in).** Run the head-to-head QA-e2e (or the ablation
  routed through `ask(mode="auto")` on the dial store) with `GOLDENGRAPH_QA_MODE=auto`,
  amb ∈ {0, 0.5, 1.0}, small n. `auto` reaches `trace_chain` (with fall-through to local on a
  missing/mislabeled edge). Compare answer-match vs the local baseline.
  - **Confound to control:** `ask(mode="auto")` seeds via `seed_by_query` (needs an embedder) and
    the store's `seeds_by_name` for `trace_chain`. For the CLEAN ER-isolated test, drive
    `trace_chain` directly on the dial store with the gold *anchor surface* but a *planner-
    produced* relation_chain (NOT the gold chain — using gold relations would be oracle-cheating);
    if the planner is LLM-based, budget-cap it.
  - **Decision gate:** if `auto`/`trace_chain` closes most of the gap ⇒ the fix is largely
    "make `auto` the default (or route multi-hop through it) on the local head-to-head path" +
    harden `resolve_profile`'s chain extraction → Phase 4. Else → Phase 3.

## Phase 3 — Lever C: recall-safe answer-candidate prune (BUILD only if A+B insufficient)

Detailed tasks intentionally deferred until Phase 1/2 measurements land (don't build before the
measurement says A/B fail). Sketch, to be expanded into TDD tasks then:
- New `goldengraph/retrieve_paths.py::prune_to_candidate_paths(subgraph, seeds, question,
  embedder, *, k_hops, top_c)`: enumerate seed-rooted paths ≤ `k_hops`; score END nodes by
  EMBEDDING relevance to the question (reuse `seed_by_query`'s embedder — **NOT edge predicates**,
  per the 2026-06-22 lesson); keep the union of seed→top-`c`-candidate paths + halo. Recall-safe
  by construction. Gate `GOLDENGRAPH_LOCAL_FILTER=candidate`. Same measurement + recall guard as
  Phase 1.

## Phase 4 — land the winning lever

- The winning lever ships **gated, default OFF, byte-identical when off** (parity test in the
  goldengraph suite). A default flip requires BOTH: (a) the engineered-corpus ablation win +
  recall guard, AND (b) the real-corpus (2WikiMultiHopQA) confirmation — the engineered corpus is
  a diagnosis instrument, not a headline (carried from the ablation spec).
- Update `results/RESULTS_PATH_AWARE_RETRIEVAL.md` + a note in `RESULTS_ER_ANSWER_ABLATION.md`
  ("the path-selection gap → closed by lever X, +Δ").

## Cross-cutting rules
- **No verbatim predicate-word focus** (proven worse 2026-06-22). Prunes are topology- or
  embedding-scored only.
- Every lever env-gated, default OFF, off-path byte-identical (parity-tested in `goldengraph`).
- Recall guard (LLM-free) precedes every paid answer-match run; a lever that raises answer-match
  by dropping the answer is a REGRESSION.
- Cost: each paid measurement is amb ∈ {0,0.5,1.0} × n≈40 × 2 dials, hard `--max-cost-usd` cap;
  recall guard first so dead levers cost ~$0.

## Definition of done
Lever A and B measured (Phases 1-2) with the recall guard; the gap either closed by A/B (→ land,
Phase 4) or shown to need C (→ build, Phase 3 then Phase 4). One committed
`RESULTS_PATH_AWARE_RETRIEVAL.md` with the per-lever answer-match Δ + recall guard, on the
engineered corpus; real-corpus confirmation flagged as the gate for any default flip.

## Reviewer pass
`spec-document-reviewer` on the spec + this plan; fold corrections. Then `writing-plans` reviewer.
