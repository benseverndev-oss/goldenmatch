# GoldenGraph crossover bench (slice C) -- ambiguity x passage_k

**Status:** design
**Date:** 2026-06-27
**Owner:** Ben Severn
**Worktree:** `.worktrees/gg-crossover` (branch `feat/goldengraph-crossover-bench`)

## Problem

The ER-KG-Bench capability program has proven, with measured numbers, the structured
queries RAG structurally *can't* do: per-stage ER quality (slice A ablation), set/count
aggregation (slice B1), temporal as-of after a correction (slice B2). What it has NOT
done is honestly characterize the one place RAG is strong -- multi-hop QA over prose --
and say where, if anywhere, the KG overtakes it there.

The original head-to-head already measured the *easy* corner: at ambiguity 0.0 and a
generous `passage_k=10`, the GoldenGraph hybrid (passages + graph) scored answer-match
0.420 versus pure passage-RAG's 0.520. The graph LOST. A follow-up path-filter
(#1273) showed the graph contributes ~nothing to hybrid synthesis at that corner (it
dilutes rather than helps).

The honest open question slice C answers: **as you turn up entity ambiguity AND starve
retrieval (shrink `passage_k`), does the KG decay slower and cross over -- or does it
never overtake RAG on prose multi-hop, even under stress?** A measured negative is a
valid, publishable result: it would confirm the strategic reframe (the KG's moat is
structured/temporal/aggregation, not prose multi-hop) rather than leave it asserted.

## Goal

A two-axis sweep -- `ambiguity in {0, .25, .5, .75, 1}` x `passage_k in {10, 5, 3, 1}` --
over the engineered multi-hop corpus, producing:

1. A **free, deterministic, CI-gated** recall-crossover surface that proves the
   *mechanism*: graph reachability is `passage_k`-invariant while lexical passage-recall
   decays under starvation, so they cross at small `passage_k`.
2. An **opt-in, real-LLM, ungated** answer-match crossover table -- the headline verdict
   on whether that recall advantage flows to answers (it may not: the graph-dilution
   finding above is a real risk, and a no-crossover result is reported honestly).

This mirrors the established A/B1/B2 pattern: a key-free deterministic gate in
`goldengraph-pipeline.yml` plus an opt-in real-LLM row in `bench-graphrag-qa.yml`.

## Non-goals

- No dependency on the still-open #1270 hybrid engine. The opt-in answer-match arm builds
  its own minimal retrieval (the same lexical retriever as the deterministic core feeds
  the RAG LLM; the same resolved subgraph feeds the graph LLM), so slice C is
  self-contained and lands independently.
- No embeddings. The deterministic RAG retriever is lexical (term-overlap) so the gate is
  reproducible and key-free. Embedding retrieval is explicitly out of scope (it would make
  the gate non-deterministic and is not needed to demonstrate the starvation decay).
- No claim that the KG *wins* prose multi-hop. The slice is built to report whichever way
  the answer-match crossover lands.

## Architecture

New module `erkgbench/qa_e2e/crossover.py` + CLI `run_crossover.py`. Reuses:

- `engineered.generate_engineered(seed, n_questions, ambiguity, max_hops)` -- the
  ambiguity-dialed multi-hop corpus. Each `QAItem` carries `start_entity_id`,
  `relation_chain`, `gold_supporting_fact_ids` (the chain edge-doc ids), `hop_count`.
- `gold.GoldGraph.from_corpus` + `gold.gold_chain` -- the gold answer chain.
- `ablation._build_store`, `ablation._typ_of`, `ablation._KEYFN["goldengraph"]`,
  `scorecard.bridge_recall`, `dials.*` -- the resolution-dialed store + bridge-recall
  (the graph reachability surface; needs the `goldengraph_native` wheel).
- `scorecard_llm._BudgetedLLM` + `BudgetTracker` -- the budget-capped LLM wrapper for the
  opt-in arm.

### Data flow

```
generate_engineered(ambiguity=a) -> corpus, GoldGraph
  for each passage_k in grid:
    graph_recall[a]   = bridge-recall over the goldengraph-dialed store   (passage_k-INVARIANT)
    rag_recall[a][k]  = lexical top-k passage-recall vs gold_supporting_fact_ids (DECAYS in k)
  -> deterministic crossover grid + gate

opt-in real LLM:
  for each (a, k):
    rag_answer  = LLM(top-k lexical passages)        -> answer_match vs gold
    graph_answer= LLM(resolved subgraph triples)     -> answer_match vs gold
  -> answer-match crossover table (headline)
```

### 1. Deterministic core (free, gated)

Two recall surfaces over the engineered multi-hop questions, swept on the 5x4 grid.

**Graph reachability** (reuse slice A). For a fixed ambiguity, build the resolution-dialed
store via `ablation._build_store` under the `goldengraph` dial, oracle-seed retrieval, and
take whole-chain `bridge_recall`. This number is **`passage_k`-invariant** (the graph never
reads passages) and ambiguity-sensitive (ER quality degrades as variant surfaces rise). It
is literally slice A's `goldengraph`-dial mean, broadcast across the `passage_k` columns.

**Lexical passage-recall** (new, pure-Python, no wheel, no embeddings). A deterministic
bag-of-words retriever:
- Query terms = tokenized `start_entity` surface + the relation-chain relation tokens.
- Score each `Document` by overlap count of query terms in `doc.text` (a tiny, fixed
  term-overlap rank; ties broken by `doc.id` for determinism).
- Take the top-`passage_k` docs; `passage_recall = |topk ∩ gold_supporting_fact_ids| /
  |gold_supporting_fact_ids|`, averaged over questions.

As `passage_k` shrinks the window can't hold the whole chain, so recall decays from ~1.0
(k=10) toward `~1/hop_count` (k=1). As ambiguity rises, variant surfaces reduce the lexical
overlap between the query mention and the chain docs, decaying recall further.

**Gate (HARD)** -- asserts the robustly-true mechanism, NOT the uncertain answer-match
outcome:
1. **Graph flat in `passage_k`:** for each ambiguity row, `max - min` of graph_recall across
   `passage_k` <= 1e-9 (it does not read `passage_k`, so exactly flat).
2. **RAG decays in `passage_k`:** for each ambiguity row, rag_recall is monotone
   non-increasing as `passage_k` shrinks (within a small tolerance for ties).
3. **Crossover exists:** at the smallest `passage_k`, graph_recall >= rag_recall by a margin
   in the high-ambiguity cell(s) -- one surface is flat, the other decays toward `1/hops`,
   so they must cross at sufficient starvation. (`gate_exit_code` returns 1 on any HARD
   failure; soft assertions only WARN, mirroring `ablation.evaluate_assertions`.)

**Verification before gating** (lesson from B1): the gate is designed around the hypothesized
mechanism but MUST be verified against the measured curve on the real corpus locally (the
lexical retriever needs no wheel, so the RAG surface runs on this box) before the gate
thresholds are frozen. If the measured crossover margin differs from the hypothesis, the
gate is reframed to the measured shape (as B1's "widening gap" became "large consistent gap").

### 2. Opt-in real-LLM headline (ungated)

The answer-match 2D sweep -- the verdict to headline. For each `(ambiguity, passage_k)` cell,
the SAME retrieval that produced the recall surface feeds the LLM:
- **RAG arm:** top-`passage_k` lexical passages -> prompt -> answer; answer-match vs gold.
- **Graph arm:** the resolved-subgraph triples (from `ablation._build_store` + local
  retrieval) -> prompt -> answer; answer-match vs gold.

Produces `answer_match[arm][a][k]` and the crossover read: the `(a, k)` cells (if any) where
graph answer-match >= RAG answer-match. Budgeted via `_BudgetedLLM` (each cell checks
`llm.exhausted` and short-circuits, like the scorecard rows). Self-contained -- no #1270
dependency. **A no-crossover result is reported, not gated.**

## Components / file structure

- `erkgbench/qa_e2e/crossover.py`
  - `AMBIGUITY_GRID`, `PASSAGE_K_GRID` constants.
  - `lexical_retrieve(docs, query_terms, passage_k) -> list[doc_id]` (deterministic term-overlap).
  - `query_terms_for(qa, g) -> list[str]` (start surface + relation tokens).
  - `passage_recall(qa, topk_ids) -> float`.
  - `graph_recall_at(corpus, g, ambiguity) -> float` (wraps the ablation store-build +
    bridge-recall under the `goldengraph` dial).
  - `recall_crossover_grid(*, seed, n_questions, max_hops) -> CrossoverResult` (the 5x4
    surfaces; graph col is constant per row).
  - `CrossoverResult` dataclass (graph[a], rag[a][k]).
  - `evaluate_assertions(res) -> list[(label, passed, is_hard)]`, `gate_exit_code(res)`,
    `render_crossover_md(res)` -- mirror the ablation/temporal shapes.
  - `run_crossover_deterministic(*, seed, n_questions, max_hops) -> CrossoverResult`.
  - Opt-in: `llm_answer_rag(docs, topk_ids, qa, llm) -> str|None`,
    `llm_answer_graph(subgraph, qa, llm) -> str|None`,
    `answer_match_grid(*, seed, n_questions, max_hops, inner_llm, budget_usd) -> AnswerMatchResult`,
    `render_answer_match_md(res)`.
- `erkgbench/qa_e2e/run_crossover.py` -- CLI: `--seed --n-questions --max-hops --out-md`
  (deterministic) and `--with-llm --budget-usd` (opt-in answer-match).
- `tests/test_qa_crossover.py` -- wheel-free: lexical retriever determinism, passage-recall
  decay monotonicity, gate verdicts on a synthesized surface. (The graph-reachability path
  is exercised by the in-pipeline gate run, which has the wheel.)
- `tests/test_qa_crossover_llm.py` -- stub-LLM: `llm_answer_rag`/`llm_answer_graph` map
  model output to a gold answer + see the right passages/triples; budget short-circuit.

## CI wiring

- `goldengraph-pipeline.yml`: new "Crossover capability gate (deterministic, key-free)"
  step after the wheel build (alongside ablation/aggregation/temporal). Runs
  `pytest tests/test_qa_crossover.py` then `python -m erkgbench.qa_e2e.run_crossover
  --seed 7 --n-questions 80 --out-md CROSSOVER.md`; uploads `CROSSOVER.md` (`if: always()`).
- `bench-er-kg.yml`: add `tests/test_qa_crossover.py` to the wheel-free pure-Python list
  (the lexical-recall + gate-shape tests need no wheel).
- `bench-graphrag-qa.yml`: add a `run_crossover_llm` workflow_dispatch input; the existing
  `scorecard` job's `if:` ORs it in (`run_scorecard == 'true' || run_aggregation_llm ==
  'true' || run_temporal_llm == 'true' || run_crossover_llm == 'true'`); a guarded step runs
  `run_crossover --with-llm` and uploads the answer-match markdown. Non-gating (`|| true`),
  hard cost cap via `--budget-usd`.

## Error handling

- Deterministic core is offline + fail-closed on the gate (HARD assertion failure -> exit 1).
  The lexical retriever and passage-recall never raise on well-formed corpus input; an empty
  `gold_supporting_fact_ids` (degenerate 0-hop) is excluded from the recall average.
- Graph-reachability path reuses ablation's store-build; a missing wheel is a pipeline-level
  failure (the step runs only after the wheel build, like the other gates).
- Opt-in LLM arm is budget-capped and `|| true` in CI: budget exhaustion short-circuits
  remaining cells (recorded in the markdown), never fails the lane. An LLM answer that maps
  to no known canonical is scored as a miss (None), not an error.

## Testing strategy (TDD)

Per-task failing-test-first, ruff-clean per commit (the slice's established discipline).
Deterministic tests run wheel-free on this box; the graph-reachability + full gate run in
`goldengraph-pipeline` (has the wheel). Verify the measured recall curve on the real corpus
before freezing gate thresholds.

## Open risks

- **The recall crossover may not flow to answer-match.** Expected and acceptable: the
  deterministic gate proves the recall mechanism; the opt-in arm reports honestly whether
  answers follow. A measured "graph never overtakes RAG on prose multi-hop" is a valid
  finding that reinforces the capability-program reframe.
- **Lexical retriever fidelity.** A bag-of-words retriever is a deliberate, reproducible
  stand-in for a real retriever; it is sufficient to demonstrate the `passage_k`-starvation
  decay (the mechanism), which is all the gate asserts. The opt-in arm uses the same
  retrieval feeding a real LLM, so the headline number is consistent with the gated surface.
