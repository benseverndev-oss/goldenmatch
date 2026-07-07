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
- **Seed-SHAPE dilemma (review finding #2 — load-bearing).** `answer_match_ablation` seeds with
  exactly ONE node (`_retrieve_local(slice_graph, [seed_node], …)`), whereas the product `ask`
  local path seeds `k=5` via `seed_by_query`. `filter_subgraph_to_paths`'s main mechanism is
  anchor-to-anchor shortest paths — which **never executes with a single seed** (it degenerates
  to halo-only, dropping every k≥2 answer by construction). So the single-seed ablation CANNOT
  validly measure Lever A, and the only multi-seed path is the embedder path Phase 0 otherwise
  avoids. **Resolution:** Lever A must be measured in a MULTI-SEED regime (see Task 1.2), not on
  the stock single-seed ablation.

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
- **Task 1.2 (bench measurement + recall guard — MULTI-SEED).** `erkgbench/qa_e2e/`: add
  `retrieval_levers.py::measure_lever(corpus, g, typ_of, llm, *, lever, seeds_fn)` that reruns the
  `oracle`/`goldengraph` dials' local path with the lever applied between retrieval and
  `synthesize_local`, returning per-dial answer-match AND per-dial bridge-recall of the
  post-lever subgraph. **`seeds_fn` must produce a MULTI-seed set** (finding #2): the ablation's
  single seed makes `filter_subgraph_to_paths` inert. Use the SAME `k=5` seed shape the product
  `ask` path uses — reuse `goldengraph.embed.seed_by_query(slice_graph, question, embedder, k=5)`
  so the bench regime matches production. (This accepts the embedder for Lever A specifically —
  the multi-anchor regime IS the lever's point; keep it a cheap embedding model, budget-capped.)
  `lever="filter_path"` calls `filter_subgraph_to_paths(subgraph, seeds, halo=…)`.
  - **Test:** `tests/test_qa_retrieval_levers.py` — offline stub LLM + stub `seeds_fn` returning a
    fixed multi-seed set; assert (a) the recall guard is computed on the POST-lever subgraph, (b)
    `lever="none"` reproduces the baseline on a tiny fixture, (c) with ≥2 seeds the anchor-to-
    anchor bridge actually fires (a chain between two seeds survives the filter). `importorskip("goldengraph_native")`.
  - **Commit:** `test+feat(erkgbench): multi-seed lever harness + bridge-recall guard`.
- **Task 1.3 (measure — PAID, opt-in).** Run `measure_lever(lever="filter_path")` at
  amb ∈ {0, 0.5, 1.0}, n=40, `gpt-4o-mini`, multi-seed. **Gate on the recall guard first**
  (LLM-free): if the filtered subgraph's bridge-recall drops vs the ball, STOP — the lever
  strands answers; do not spend on answer-match. **Baseline = the freshly-run `lever="none"`
  numbers from the SAME multi-seed harness** (finding #3 — do NOT anchor to the old single-seed
  0.275/0.372; those are a different-n, different-seed-shape regime and omit goldengraph=0.077).
  Record the delta in `results/RESULTS_PATH_AWARE_RETRIEVAL.md`.
  - **Decision gate:** answer-match up AND recall not down ⇒ Lever A is a win → Phase 4. Else →
    Phase 2.

## Phase 2 — Lever B: route `local` multi-hop through the existing `trace_chain` (measure)

`trace_chain` already solves path-finding LLM-free, but needs a query PLAN
(`anchor_surface` + `relation_chain`). **Task 2.1 is already resolved from `route.py`
(review findings #1/#4):** a `chain` plan's `relation_chain` is populated ONLY by
`_extract_chain_slots` / `_CHAIN_RE` — a regex **hard-matched to the engineered question
template** ("Starting from X, follow the relation R1, then R2. What entity…"). It is LLM-free,
but `LLMQueryClassifier.classify` **never sets `relation_chain`**, and the general heuristic
routes any other phrasing to `hybrid`. **Consequence — Lever B is TEMPLATE-BOUND:**

- On the **engineered corpus** it fires for free (regex matches the template) → cheap to measure.
- On a **real corpus (2WikiMultiHopQA)** it CANNOT fire — there is no chain decomposition for
  natural-language multi-hop questions. So Lever B **cannot pass the Phase-4 real-corpus gate as
  designed**; generalizing it means BUILDING chain decomposition the classifier lacks today — new
  work that competes with Lever C, NOT a config win. The spec's "B1 = one-line config win" is
  scoped to the engineered corpus only.

- **Task 2.2 (engineered-only measurement — PAID, opt-in).** Drive `trace_chain` **directly on
  the dial store** (the ER-isolated path — NOT the full head-to-head, which would add extraction/
  resolver/embedder confounds), using the regex-`_extract_chain_slots` planner-produced
  `relation_chain` (LLM-free here; NOT the gold chain — the question text legitimately states the
  relations, so this is not an oracle leak, per the review). amb ∈ {0, 0.5, 1.0}, small n.
  Compare answer-match vs the Task-1.3 `none` baseline.
  - **Decision gate:** even if `trace_chain` closes the engineered gap, it only proves the
    *mechanism* (a relation-guided walk beats ball-dump); it does NOT license a product default,
    because it can't fire on real questions. So a Lever-B win ⇒ the real deliverable is **chain
    decomposition** (build), tracked into Phase 3/4 alongside Lever C — not "flip `auto` on".
    Else → Phase 3.

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
