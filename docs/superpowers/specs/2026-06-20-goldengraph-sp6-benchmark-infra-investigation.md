# goldengraph SP6 — benchmark-infra investigation (the real foundation)

SP6 is the head-to-head eval that proves goldengraph's thesis. This document is
the *investigation* that precedes the SP6 spec: what benchmark infrastructure
already exists, what's reusable, what's genuinely missing, and the shape SP6
should take. It replaces an earlier mistaken assumption (below).

## Correction: ER-KG-Bench EXISTS
An earlier note claimed "ER-KG-Bench doesn't exist at the SP1-spec-claimed path."
That was a **wrong-branch check** — the main checkout `D:\show_case\goldenmatch`
sits on `claude/goldenmatch-kg`, which lacks it. **On `main` it is real and
committed**: `packages/python/goldenmatch/benchmarks/er-kg-bench/` (48 tracked
files, `results/RESULTS.md` checked in). It is the right foundation for SP6 — we
extend it, we don't rebuild it.

## What exists — three harnesses

### 1. ER-KG-Bench — KG-framework ER quality (THE relevant one)
`packages/python/goldenmatch/benchmarks/er-kg-bench/`
- **Question it answers:** how well does each KG system *resolve entities* (collapse
  duplicate surface forms across documents into one node) — the thing that, per its
  own README, "poisons every downstream answer (accuracy decays as
  `(ER_accuracy)^hops`)."
- **Corpus:** 206 records / 48 entities / **9 stratified failure classes** — abbr,
  nick, synm, coll(ision)*, xling, typo, suffix, temp(oral)*, xdoc (`*` =
  precision-critical negative classes). Real corpus from Wikidata + RxNorm
  (`dataset/build_real.py`, `records.csv` committed) plus a `ghsuite` dogfood
  corpus (`records_ghsuite.csv`).
- **Systems benchmarked (11 configs):** MS-GraphRAG, Cognee, mem0, Neo4j-KGBuilder
  (±emb), neo4j-graphrag (fuzzy / spaCy / exact), LlamaIndex-PGI (±emb), LightRAG,
  graphiti — plus goldenmatch (auto / auto+fields / emb-ann). Fidelity tiers:
  `real-inproc` (runs the library's real decision code with storage mocked, via
  `erkgbench/real_resolvers.py`), `modeled` (source-validated rule reproduction),
  `validated` (deterministic exact-match floor).
- **Headline (committed `results/RESULTS.md`):** goldenmatch(auto+fields)
  **F1 0.602** vs best framework Neo4j-KGBuilder(emb) **0.471** — **+13pp**, on the
  same corpus + the same scorer.
- **Code:** `erkgbench/run.py` (CLI orchestrator → RESULTS.md), `adapters/`
  (`goldenmatch_adapter.py`, `modeled.py`, `real/`), `real_resolvers.py`,
  `metrics.py` (pair-level P/R/F1 **by failure class**), `sweep.py`.
- **Already has a retrieval+answer DEMO:** `demo/kg.py::retrieve(kg, query) ->
  Subgraph`, `demo/agent.py::answer(question, sub, llm_fn) -> AgentAnswer`,
  `demo/run_demo.py` builds a **before/after** KG (under-merged vs resolved) and a
  COUNT question that gets the wrong answer when entities are under-merged. This is
  the seed of the QA eval (see The Gap).

### 2. `scripts/bench_er_headtohead/` — ER dedup at scale (GM vs Splink)
- GoldenMatch vs Splink on **dedup throughput + accuracy**, 100k → 100M rows,
  subprocess-per-datapoint orchestrator (`orchestrate.py`), `run_panel.py`,
  `run_bakeoff.py`. **Engine-agnostic evaluator** `evaluate.py` scores
  `{record_id, pred_cluster_id}` vs truth via a DuckDB contingency table →
  **pairwise + B-cubed** P/R/F1 (no pair materialization, bounded memory).
- CI: `bench-er-headtohead.yml` (scale sweep, `large-new-64GB`), `bench-probabilistic.yml`
  (FS v1-vs-v2 panel + regression gate). Reusable scaffolding for SP6's *scale/perf*
  dimension; the evaluator is directly reusable.

### 3. `D:\show_case\golden-showcase\comparison_bench\` — classic ER libs
- GoldenMatch vs Splink / dedupe / RecordLinkage on Febrl / DBLP-ACM / NC Voter /
  NPPES; per-tool runner scripts → results JSON, `collect_results.py` aggregates.
  Separate repo. A reusable JSON-results comparison pattern; not KG-specific.

## Eval APIs + datasets already in hand
- **Metric code:** `erkgbench/metrics.py` (per-class pair P/R/F1) · `bench_er_headtohead/evaluate.py`
  (pairwise + B-cubed via DuckDB) · `goldenmatch/core/evaluate.py` (`evaluate_pairs`/
  `evaluate_clusters`/`threshold_sweep`/`recommend_threshold`) · `core/compare_clusters.py`
  (CCMS unchanged/merged/partitioned/overlapping + Talburt-Wang Index).
- **Datasets:** er-kg-bench real + ghsuite corpora (committed); DBLP-ACM (committed),
  NCVR sample (committed), Febrl3/4 (recordlinkage runtime), historical_50k (Splink),
  synthetic person generators (`bench_er_headtohead/generate_fixture.py`,
  `tests/generate_synthetic.py`). No new dataset acquisition needed for SP6.

## The gap (what SP6 must actually build)
ER-KG-Bench measures **entity-resolution pairwise quality only**. The downstream
thesis — *because entities are resolved, one k-hop query retrieves ALL related
facts; an unresolved/exact-match KG splits facts across duplicate nodes, so
retrieval is incomplete and answers degrade as `(ER_accuracy)^hops`* — is **stated**
(README:15) and **demonstrated once** (the `demo/` before/after COUNT example), but
**never measured** across the corpus or across engines. No QA/retrieval-completeness
metric exists in either repo. That measurement is the genuinely new SP6 contribution
and the thing that makes goldengraph's case beyond "our ER F1 is higher."

Also missing: goldengraph itself is not yet an engine row in ER-KG-Bench (the table
has goldenmatch and the frameworks, but not the new native KG engine).

## Recommended SP6 shape (two halves)
1. **Add goldengraph as an ER-KG-Bench engine (reuse, near-free).** A
   `goldengraph` adapter in `erkgbench/adapters/` that runs the real pipeline
   (extract → resolve → store) over the 206-record corpus and scores via the same
   `metrics.py`. Since goldengraph resolves via the goldenmatch resolver (Provided
   mode) or the native resolver, expect it to land at/near goldenmatch(auto+fields)
   — i.e., inherit the +13pp ER lead, now demonstrated from the **native engine**.
   Places goldengraph directly in the committed headline table.
2. **Promote the demo into a measured QA/retrieval-completeness eval (new).**
   Generalize `demo/kg.py::retrieve` + `demo/agent.py::answer` from the single
   crafted COUNT example into a corpus-wide question set whose answers require facts
   attached to entities that appear under multiple surface forms. Metric =
   **answer/subgraph completeness**: fraction of ground-truth facts reachable within
   a k-hop query, for goldengraph (resolved → 1 node, all facts in 1 hop) vs an
   **exact-match-KG baseline** (facts split across duplicate nodes → partial
   retrieval), swept over hops. This **measures** `(ER_accuracy)^hops` instead of
   asserting it — the proof SP6 exists to deliver. Deterministic where possible
   (stub LLM / template answers) so it gates in CI like the other binding lanes.

## What NOT to do
- Don't build a new ER benchmark or re-derive ER metrics — three harnesses + four
  metric modules already exist; reuse `erkgbench/metrics.py` and the engine-agnostic
  `bench_er_headtohead/evaluate.py`.
- Don't acquire new datasets — the real + ghsuite corpora already stratify the
  failure classes goldengraph's resolver targets.
- Don't claim the downstream win until the QA-completeness metric is measured across
  the corpus; the single demo example is an illustration, not evidence.

## Open questions for the SP6 spec (to brainstorm)
- QA-completeness metric exactly: fact-recall@k-hops vs LLM-judged answer
  correctness vs both? (Deterministic fact-recall is CI-gateable; LLM-judged is
  richer but flaky.)
- The exact-match-KG baseline: reuse er-kg-bench's `validated` exact-match floor as
  the "naive KG," or stand up a real neo4j-graphrag/LlamaIndex KG via the
  goldenmatch-kg shims for a real-framework head-to-head?
- Where the QA corpus lives: extend `dataset/` with question/answer/expected-facts,
  or a separate `qa/` corpus keyed to the same 48 entities?
- CI: a new `bench-er-kg` lane (dispatch + informational), or fold into the existing
  `goldengraph.yml` informational lane?
