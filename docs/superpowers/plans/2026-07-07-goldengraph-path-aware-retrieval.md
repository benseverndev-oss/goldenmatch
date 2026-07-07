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

## Phase 3 — Lever C: answer-candidate-scored prune (BUILD — chosen 2026-07-07 after A refuted, B skipped)

**Why now:** Lever A refuted (`results/RESULTS_PATH_AWARE_RETRIEVAL.md`) — anchor-to-anchor
topology has no operating point that keeps the single-anchor chain AND shrinks the ball
(recall-safe ⟺ ~no pruning). The user chose to skip Lever B's engineered-only confirmation and
build C directly. C's distinction from A: the prune target is chosen by the QUERY signal A
ignored (embedding similarity of candidate END nodes to the question), so pruning power comes
from `top_c ≪ |ball|`, not from a halo radius that must shrink to prune (A's trap). It scores
NODES, never edge predicates — dodging the 2026-06-22 revert.

**Honest recall claim:** "recall-safe by construction" means every KEPT candidate is on a real
seed-rooted path (no topology artifacts / stranded fragments), NOT that the true answer is
guaranteed kept — the answer survives only if its end-node lands in the top-`c` embedding
candidates (or within `halo` of a seed). Whether that holds at a pruning-meaningful `top_c` is
exactly what the recall guard measures. If it doesn't hold, C is refuted and the real deliverable
is chain-decomposition (Lever B generalized), not a cheap prune.

- **Task 3.1 (product primitive, TDD).** New `goldengraph/retrieve_paths.py::prune_to_candidate_paths(
  subgraph, seeds, question, embedder, *, k_hops=4, top_c=3, halo=1)`. Pure over the
  `{entities, edges}` dict + `Embedder`. Algorithm: (1) no seeds / no entities → return unchanged
  (mirror `filter_subgraph_to_paths`); (2) build UNDIRECTED adjacency; (3) candidates = non-seed
  entities reachable from any seed within `k_hops` with a non-empty `canonical_name` (mirror
  `seed_by_query`'s empty-name drop that avoids the provider 400; literals with a real string name
  stay eligible — some answers are literal values); (4) score each candidate by cosine(question,
  candidate_name) using `embedder.embed([question] + names)` — the EXACT `seed_by_query` math;
  (5) keep = seeds ∪ (for each of the top-`c` candidates, the shortest path from its NEAREST seed)
  ∪ `halo`-hop neighbourhood of the seeds; (6) filter entities/edges to `keep`. Deterministic
  tie-break: ascending `entity_id` (as `seed_by_query`).
  - **Test:** `tests/test_retrieve_paths.py` (offline, stub embedder). (a) no-seeds / empty →
    identity; (b) seeds always kept; (c) a ball with a gold chain seed→A→ANSWER + an off-topic
    branch seed→X→Y, stub embedder scoring ANSWER top → the chain to ANSWER is kept and {X,Y}
    pruned; (d) `top_c` larger than #candidates keeps everything reachable (no crash); (e) every
    kept non-seed node lies on a seed-rooted path (the by-construction invariant).
  - **Commit:** `feat(goldengraph): answer-candidate-scored path prune (Lever C primitive)`.
- **Task 3.2 (product wiring, TDD).** Extend the local gate to `candidate`: `_local_filter_mode`
  already returns the raw string; add `_local_filter_topc` (`GOLDENGRAPH_LOCAL_FILTER_TOPC`,
  default 3) + `_local_filter_khops` (`GOLDENGRAPH_LOCAL_FILTER_KHOPS`, default 4) env readers;
  extend `_apply_local_filter(subgraph, seeds, *, question=None, embedder=None)` so `mode ==
  "candidate"` routes through `prune_to_candidate_paths` (needs question+embedder; if either is
  None it no-ops safely). Thread `query`/`embedder` from `ask`'s local branch into the call.
  `path` + off modes stay byte-identical.
  - **Test (goldengraph suite):** extend `tests/test_answer_local_filter.py` — (a) `candidate`
    gate routes the ball through `prune_to_candidate_paths` with the query + embedder (spy);
    (b) off / `path` unchanged (regression); (c) `candidate` with no embedder in scope no-ops.
  - **Commit:** `feat(goldengraph): wire candidate prune into the gated local path`.
- **Task 3.3 (bench harness).** `retrieval_levers.py`: add `lever="candidate"` to `_apply`
  (threads `question`+`embedder`), and give `measure_lever` an optional `embedder=` so the
  candidate lever can score. Keep `filter_path`/`none` untouched.
  - **Test:** `tests/test_qa_retrieval_levers.py` — pure `_apply("candidate", …)` on a fixture
    with a stub embedder returns the same as a direct `prune_to_candidate_paths` call.
  - **Commit:** `test+feat(erkgbench): candidate lever in the measurement harness`.
- **Task 3.4 (measure — recall guard first, sweep `top_c`).** LLM-free guard at amb ∈ {0,0.5,1.0},
  n=40, multi-seed k=5, `top_c ∈ {2,3,5}`: report post-lever bridge-recall AND node retention vs
  `none` and vs Lever A's recall-safe halo=3 (~95% retained). **Decision gate:** candidate recall
  ≥ ~none AND retention meaningfully below halo=3's 95% (i.e. it actually prunes) ⇒ run the PAID
  answer-match (baseline = freshly-run `none`, hard `--max-cost-usd`), Δ into
  `RESULTS_PATH_AWARE_RETRIEVAL.md` → Phase 4. Else → C refuted; record it; the path-focus family
  is exhausted on cheap signals and the deliverable becomes chain-decomposition (build).
  - **OUTCOME (2026-07-07): C REFUTED.** Candidate recall pinned to Lever A's halo=1 floor
    (0.667/0.600/0.538 on oracle), `top_c` sweep flat — the query-name embedding doesn't rank the
    multi-hop answer end-node (it isn't named in the question). Both cheap signals (topology +
    node embedding) fail; no prune of a recall-1.0 ball recovers the ~0.275. Deliverable → chain
    decomposition. `results/RESULTS_PATH_AWARE_RETRIEVAL.md`.

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
