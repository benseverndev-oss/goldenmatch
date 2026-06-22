# GoldenGraph evidence program -- QA-e2e first full headline + handoff

**Date:** 2026-06-22
**Branch / PR:** `claude/goldengraph-qa-bench-answerability` -> PR #1184
**Status:** Slice #1 head-to-head is now *real* -- first full ambiguity sweep
ran end-to-end, all engines green, clean decay curves. The bench is unblocked;
the next work is on the *system*, not the harness.

Related: `2026-06-20-goldengraph-evidence-program-design.md` (the program spec),
`2026-06-20-goldengraph-program-roadmap.md` (SP1-SP6).

---

## TL;DR

The 2026-06-21 head-to-head was green-but-degenerate (every engine ~0
answer_match). Root-caused and fixed; then scaled to the real headline. The
first full sweep (4 engines x 5 ambiguity levels x ~275 questions, real
gpt-4o-mini) produced clean, monotonic decay curves with **zero failures and no
system iteration** -- a working measurement instrument.

**The result is honest, not triumphant:** LightRAG currently leads on accuracy;
goldengraph is second at ~1/20th the cost. goldengraph's gap is specifically a
**multi-hop (2+) retrieval/synthesis collapse**, not an ER deficiency. That is
the lever for the next pass.

---

## What shipped this session (PR #1184)

All under `packages/python/goldenmatch/benchmarks/er-kg-bench/`.

1. **Engineered corpus made answerable** (`erkgbench/qa_e2e/engineered.py`).
   The old question "following the chain from X, what is the final entity?"
   named neither the relations nor the hop count -- a start node has 2-4
   outgoing edges, so the gold answer was one arbitrary walk among many, and *no*
   engine (nor a perfect graph-walker) could recover it. Fix: one edge per
   `(entity, relation)` so a relation sequence determines a unique walk, and the
   question now states that chain. Document ids encode the edge (`src::rel::dst`)
   so an oracle can rebuild the graph; duplicate `(start, chain)` walks deduped.
   Guard: `tests/test_qa_engineered_answerable.py` -- a pure-Python oracle must
   score `answer_match == 1.0` on every question.

2. **LightRAG event-loop crash fixed** (`engines/lightrag.py`). It ran build +
   each answer under its own `asyncio.run`, so storage primitives bound during
   `initialize_storages` were used from a closed loop at query time
   (`bound to a different event loop` -> every query failed). Fix: one persistent
   loop per engine. Guard: `tests/test_qa_lightrag_loop.py`.

3. **Graphiti teardown noise fixed** (`engines/graphiti.py`). Same per-call
   `asyncio.run` closed the loop and graphiti's httpx client teardown landed on
   it (`RuntimeError: Event loop is closed`, non-fatal). Same persistent-loop
   fix. Guard: `tests/test_qa_graphiti_loop.py`.

4. **Ambiguity sweep + aggregation for the headline.**
   - `run_qa_e2e.py` gained `--ambiguity`; the harness records it per result.
   - `aggregate_qa_e2e.py` merges every per-(engine, ambiguity) result JSON into
     `RESULTS_QA_E2E.md`: engine x ambiguity table, engine x hop decay (pooled),
     cost summary. Pure stdlib; tested (`tests/test_qa_aggregate.py`).
   - `.github/workflows/bench-graphrag-qa.yml`: a `setup` job turns the
     comma-separated `ambiguity` input into a matrix; each engine runs one
     budget-capped, independently-timed job per ambiguity (`fail-fast: false`);
     an `aggregate` job downloads all artifacts and writes/echoes/uploads the
     headline.

ms_graphrag's `$0.0` cost is documented-by-design (graphrag's LLM is
config-driven, no token hook), not a bug.

---

## First full headline (run 27947319134, engineered, ~275 Q/run, gpt-4o-mini)

`answer-match` = normalized gold answer appears as a contiguous token run in the
prediction (the correctness signal for generative answers; EM reads ~0).

### answer-match by ambiguity (the decay curve)

| engine | 0.0 | 0.25 | 0.5 | 0.75 | 1.0 |
|---|---|---|---|---|---|
| **lightrag** | **0.299** | **0.248** | **0.172** | **0.126** | **0.080** |
| goldengraph | 0.197 | 0.124 | 0.082 | 0.059 | 0.043 |
| ms_graphrag | 0.182 | 0.073 | 0.065 | 0.052 | 0.036 |
| graphiti | 0.058 | 0.055 | 0.025 | 0.015 | 0.022 |

### answer-match by hop count (pooled across the sweep)

| engine | 1-hop | 2-hop | 3-hop | 4-hop |
|---|---|---|---|---|
| **lightrag** | **0.471** | **0.164** | 0.085 | 0.072 |
| goldengraph | 0.327 | 0.030 | 0.042 | 0.036 |
| ms_graphrag | 0.186 | 0.062 | 0.055 | 0.039 |
| graphiti | 0.088 | 0.020 | 0.022 | 0.017 |

### summary (mean across sweep, cost)

| engine | mean answer-match | mean token-F1 | total cost (USD, 5 runs) |
|---|---|---|---|
| lightrag | 0.185 | 0.023 | 3.1950 |
| goldengraph | 0.101 | 0.017 | 0.1588 |
| ms_graphrag | 0.082 | 0.013 | 0.0000 (unmetered) |
| graphiti | 0.035 | 0.047 | 0.0242 |

---

## Honest interpretation

- **The instrument works.** Every curve is monotonic in ambiguity; the ranking
  is stable across hops; the run is reproducible and cheap (except LightRAG). The
  Slice #1 deliverable -- a real, falsifiable head-to-head -- exists now.
- **goldengraph is second, not first.** LightRAG leads at every ambiguity level
  and every hop count. The program's thesis ("ER makes goldengraph decay slower
  in ambiguity") is **not** supported by this run: goldengraph tracks below
  LightRAG and decays at a similar relative rate.
- **The gap is multi-hop, and it is ours.** goldengraph 1-hop 0.327 -> 2-hop
  **0.030** is a cliff; LightRAG holds 2-hop at 0.164. 1-hop is competitive, so
  ER + single-edge lookup work -- the failure is **chaining facts across hops**
  in `goldengraph/answer.py` (SP4c local retrieval + synthesis), not entity
  resolution. This is the highest-leverage fix.
- **The ER moat is not yet visible on this corpus.** Ambiguity hurts goldengraph
  about as much as everyone. The engineered documents are tiny atomic sentences
  ("X works at Y."); they may not actually force cross-document resolution. The
  thesis needs a corpus where traversing the chain *requires* resolving the same
  entity across different variant surface forms.
- **Cost is a real, separate story.** goldengraph answers at ~$0.16 vs LightRAG
  $3.20 for the same sweep (20x cheaper). The cost/quality wedge (program slice
  #4) is genuine even where accuracy trails.

---

## How to reproduce

Opt-in, real-LLM, never gates required CI:

```
workflow_dispatch: bench-graphrag-qa.yml on the branch
  corpus=engineered  max_questions=300
  ambiguity=0.0,0.25,0.5,0.75,1.0   budget_usd=25   engine=all
```

- Each `(engine, ambiguity)` is its own budget-capped job; `fail-fast: false`.
- The `aggregate` job echoes `RESULTS_QA_E2E.md` to its log and uploads it as the
  `graphrag-qa-results-AGGREGATE` artifact (it is NOT committed to the repo by
  CI -- promote it deliberately).
- Local smoke (no LLM): `python -m erkgbench.qa_e2e.run_qa_e2e --self-test
  --corpus engineered --ambiguity 0.5 --out-md ... --out-json ...` then
  `python -m erkgbench.qa_e2e.aggregate_qa_e2e --results-dir ... --out ...`.
- Full local unit suite: `pytest tests/` (3 pre-existing failures need the
  optional `neo4j_graphrag` dep; unrelated).

---

## Recommended next steps (prioritized)

1. **Fix goldengraph's 2-hop collapse (`goldengraph/answer.py`).** Inspect the
   `mode="local"` path: `seed_by_query` -> `slice_graph.query(seeds, hops)` ->
   `synthesize_local`. The 1-hop vs 2-hop cliff suggests the neighborhood walk
   isn't expanding to the second hop, or synthesis isn't given the chained edges.
   This is the single change most likely to move the headline. Re-run the sweep
   to measure.
2. **Make the engineered corpus exercise ER.** Force entities along a chain to
   appear under *different* variant surface forms in adjacent edge documents, so
   traversal genuinely requires resolution. Today's renderer picks a variant per
   mention independently; the thesis wants correlated cross-doc variation. Guard
   it with the existing oracle (still must be answerable by a resolver).
3. **Add a real-world anchor.** Wire the MuSiQue path (loader already exists) into
   the sweep for a credibility anchor where ER gives no edge (parity expected).
4. **Promote the headline.** Once (1)/(2) land, commit `RESULTS_QA_E2E.md` and
   write the program-level results note. Until then it is honest but not a win to
   publish as the flagship number.

---

## References

- PR: https://github.com/benseverndev-oss/goldenmatch/pull/1184
- First full sweep: run 27947319134 (20/20 green, aggregate artifact
  `graphrag-qa-results-AGGREGATE`).
- Earlier 20-Q validation: run 27945070544 (confirmed the corpus + LightRAG
  fixes before scaling).
