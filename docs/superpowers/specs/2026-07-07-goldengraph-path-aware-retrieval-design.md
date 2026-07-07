# GoldenGraph path-aware retrieval — design

**Status:** design draft 2026-07-07. Awaiting approval → plan.
**Owner:** ER platform.
**Related:** `2026-07-07-goldengraph-er-answer-ablation-design.md` (+ its plan + `results/RESULTS_ER_ANSWER_ABLATION.md`), the `_retrieve_local` docstring in `goldengraph/answer.py` (the 2026-06-22 predicate-focus revert), `goldengraph/subgraph_filter.py`, `trace_chain` in `answer.py`.

## 1. The evidence that motivates this

The ER→answer ablation + two follow-up probes (`RESULTS_ER_ANSWER_ABLATION.md`, amb=0.5,
n=40, `gpt-4o-mini`) localized the end-to-end accuracy gap precisely:

- **Synthesis reasoning is NOT the bottleneck.** Handed the isolated gold-chain subgraph,
  the synthesizer answers **1.000 across every hop** (`build_gold_subgraph → synthesize_local`).
- **Retrieval RECALL is fine.** The `oracle` dial's ball *contains* the answer chain
  (bridge-recall 1.0) yet answers only ~0.275.
- **Ball SIZE is the wrong knob.** Tightening `node_budget`/`hops` makes it WORSE
  (0.275 → 0.10) — it drops the answer before the distractors.
- **The gap is path-SELECTION.** The model can WALK the chain in isolation but can't FIND
  it inside a realistic ~45-entity neighborhood of real sibling edges.

So the lever is **retrieval that hands synthesis the query-relevant PATH, not the whole
neighborhood** — while never stranding the answer (recall-safe).

## 2. Prior art in this repo (read before proposing anything new)

Path-aware retrieval is **not greenfield here**. Three existing pieces + one negative result:

1. **`trace_chain` (`answer.py`)** — a relation-guided, **LLM-free** multi-hop walk: seed the
   anchor by name, follow the named relation chain hop-by-hop (lenient `_rel_match`:
   normalize + substring-either-way; direction-tolerant; bridges the store's under-merge via
   `_bridge_surfaces`), return the final node. It "hands synthesis nothing to drown in." Used
   in `ask(mode="auto")` when `plan_query` yields a `chain` plan (needs `profile.anchor_surface`
   + `profile.relation_chain` from `resolve_profile`/`route.py`).
2. **`filter_subgraph_to_paths` (`subgraph_filter.py`)** — a **topology-based** path-preserving
   prune (keeps seeds + anchor-to-anchor shortest paths + a `halo`-hop neighbourhood; predicate-
   blind, so it dodges the 2026-06-22 failure). Currently wired ONLY to hybrid mode, gated by
   `GOLDENGRAPH_HYBRID_FILTER=path`.
3. **The 2026-06-22 negative result** (documented in the `_retrieve_local` docstring): a
   relation-aware focus that pruned the ball to the query's named predicates was measured
   **worse** — real LLM-extracted predicates rarely match the query's relation words verbatim,
   so it dropped the true chain. **Any predicate-word-matching prune is a known trap.**

**The precise gap:** the `local` synthesis path (`ask(mode="local")` → `_retrieve_local` →
`synthesize_local`) — which is the DEFAULT (`GOLDENGRAPH_QA_MODE=local`) and is exactly what the
ablation + head-to-head exercised — does **no** path extraction at all. `trace_chain` isn't
reached (that's `auto`); `filter_subgraph_to_paths` isn't wired to it (that's hybrid).

## 3. Design — measurement-first, reuse before build

Because strong primitives already exist and a naive approach already failed, this is
**measure-the-existing-levers first, build only what's missing**. Three candidate levers,
in ascending cost:

### Lever A — wire `filter_subgraph_to_paths` into `local` (gated), measure
Add a `GOLDENGRAPH_LOCAL_FILTER=path` gate in `ask`'s local branch, applying the EXISTING
`filter_subgraph_to_paths(subgraph, seeds, halo=…)` before `synthesize_local` (mirrors the
hybrid wiring). Cheapest possible test.
- **Known blind spot:** it keeps *anchor-to-anchor* shortest paths. For a SINGLE-anchor
  multi-hop question the answer sits at the *end* of a chain from one seed (not between two
  anchors), so only `halo` saves it — and a big `halo` re-imports distractors. So Lever A is
  expected to help only when the answer entity is itself among the `k` seeds. **Measure how
  often that holds** (it's cheap: no LLM — recompute bridge-recall on the pruned subgraph vs
  the ball; the recall guard).

### Lever B — route `local` multi-hop through `trace_chain` (reuse the existing walk)
The relation-guided walk already solves "find the path" and returns the answer directly,
LLM-free. The blocker is that `local` mode doesn't build a query PLAN (`anchor_surface` +
`relation_chain`). Options for the plan on the `local` path:
- (B1) Run the `auto` planner (`resolve_profile`/`plan_query`) opportunistically inside
  `local`: if it yields a `chain` plan, try `trace_chain` first; on `None` (missing/mislabeled
  edge) fall through to today's ball+`synthesize_local`. This is **exactly what `mode="auto"`
  already does** — so B1 largely reduces to "does the head-to-head / ablation benefit from
  `mode=auto` instead of `mode=local`?" — a **config test, not new code**.
- (B2) If `resolve_profile` needs a signal `local` lacks (an LLM query-classifier or a
  heuristic decomposition), that's the only real new work — and it should be gated + measured
  against B1.

### Lever C — a recall-safe structural prune keyed on *answer candidates*, not predicates
If A and B underperform, design a new prune that (a) enumerates seed-rooted paths up to `k`
hops, (b) scores candidate END nodes by *embedding* relevance to the question (reusing the
`seed_by_query` embedder — NOT edge predicates, dodging the 2026-06-22 trap), (c) keeps the
union of seed→top-candidate paths + halo. Recall-safe by construction (every kept set is a
real path from a seed). This is the genuinely-new build, justified only if A/B don't close it.

### Recommendation
**Start with B1 (config: does `mode=auto`/`trace_chain` close the ablation gap?) and A (wire
the existing filter to local), both cheap and reusing shipped code. Build C only if the
measured gap survives.** Heed the negative result: **no verbatim predicate-word focus.**

## 4. Validation (the harness already exists)

- **Ceiling:** `synthesis-given-gold` = 1.000 is the target the pruned ball should approach.
- **Metric:** re-run the ER→answer ablation (`run_answer_ablation_sweep`) with each lever gated
  on, at amb ∈ {0, 0.5, 1.0}; report `oracle`/`goldengraph` answer-match vs the current
  0.275/0.372 baseline.
- **Recall guard (LLM-free, cheap):** the pruned subgraph's bridge-recall must NOT drop vs the
  unpruned ball — a lever that raises answer-match by *stranding* answers is a regression, not a
  win. `bridge_recall(chain, pruned_ball, coverage)` on the ablation questions, gated on == the
  ball's.
- **A/B discipline:** each lever behind an env gate, default OFF, byte-identical when off;
  measured delta on the same seed/corpus before any default flip (the repo's standard).

## 5. Scope + caveats

- **Engineered corpus** — a diagnosis instrument; a real default flip needs the real-corpus run
  (2WikiMultiHopQA) too. This spec targets the *mechanism*, not the headline number.
- **Product-code change:** unlike the ablation (bench-only), the winning lever touches
  `goldengraph/answer.py` (+ maybe `route.py`), which has its own test suite — new behavior is
  gated + parity-tested there.
- **Out of scope:** synthesizer prompt work (proven not the bottleneck); ball-size tuning
  (proven the wrong knob); any predicate-verbatim focus (proven worse).

## 6. Open questions (resolve before the plan)
1. Does the head-to-head (`RESULTS_QA_E2E`) run `local` or `auto` today? If `local`, B1 may be a
   one-line config win; if `auto`, `trace_chain` is already in play and the gap is its
   plan-extraction accuracy (→ Lever C).
2. `halo` for Lever A: 1 (tight, single-anchor blind spot) vs 2 (more recall, more distractors)?
3. Lever C embedding-scored candidates: reuse `seed_by_query`'s embedder + top-k, or a cheaper
   lexical fallback for the offline lane?
