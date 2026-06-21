# GoldenGraph Evidence Program -- design

**Status:** design (approved in brainstorming 2026-06-20)
**Supersedes nothing.** Extends the SP1-SP6 goldengraph program with the proof
layer it is currently missing.
**Foundation PRs (must land first):** #1146 (SP6 QA infra) and #1148
(context-aware resolution -> ER F1 0.602). See *Dependencies*.

---

## 1. Motivation -- the proof gap

GoldenGraph is feature-complete (SP1 core, SP2 bi-temporal store, SP3
communities, SP4 host pipeline, SP5 WASM/C bindings, SP6 eval) but **proof-thin
as a GraphRAG engine.** SP6 measured two things:

1. **ER quality** -- pairwise F1 0.602 on a 206-record / 48-entity corpus,
   +13pp over the best KG framework (`results/RESULTS.md`).
2. **Fact co-location** -- a *synthetic* QA score of 0.733 vs an exact-match
   floor of 0.333 (`results/RESULTS_QA.md`).

`RESULTS_QA.md` states its own limit plainly: the metric is "NOT real-world QA
accuracy, and NOT the hop-exponent (the KG model has no edges to traverse)."

So the thesis the entire program rests on -- *better entity resolution
compounds into better multi-hop answers, the `(ER_accuracy)^hops` decay* -- is
**asserted and demoed once, never measured end-to-end.** Every competitor we
target (LightRAG, MS-GraphRAG, Graphiti) publishes real multi-hop QA numbers on
real corpora. GoldenGraph has none. "Best KG engine" is currently unfalsifiable.

This program closes that gap. It is the "prove it" increment: turn a
best-in-class ER *sub-component* into a demonstrated best-in-class *GraphRAG
engine*, and ship it so people can use it.

## 2. Goals / non-goals

**Goals**

- A reproducible, real-LLM, end-to-end multi-hop QA head-to-head: goldengraph
  vs LightRAG vs MS-GraphRAG vs Graphiti, on a credible standard corpus AND an
  ambiguity-rich corpus, producing a published answer-accuracy table.
- A measured `(ER_accuracy)^hops` decay curve on the ambiguity-rich corpus --
  the thesis, quantified.
- Measured determinism, point-in-time (`as_of`) answering, extraction quality,
  and index cost/latency for goldengraph relative to the field.
- GoldenGraph installable (PyPI) with a LightRAG-shaped drop-in facade.

**Non-goals**

- Beating LightRAG/MS-GraphRAG on *retrieval breadth* features (dual-level
  retrieval, query expansion, etc.). We compete on correctness, determinism,
  and temporality, not feature parity.
- Distributed / scale work. This is a quality-and-proof program, not a scale
  program.
- Product-matching or non-KG ER domains.

## 3. Program decomposition

Five slices. **#1 is the keystone** -- it builds the corpora + the end-to-end
head-to-head harness that #2-#4 reuse. #5 is independent.

| # | Slice | Reuses | LLM? | Own spec/plan/PR |
|---|---|---|---|---|
| 1 | End-to-end multi-hop QA head-to-head | -- | yes (capped) | this spec drives it; impl likely 3 PRs |
| 2 | Differentiator proofs (determinism, `as_of`, ER->answer delta) | #1 harness | partly (det/as_of need none) | own spec |
| 3 | Extraction quality (entity/relation F1) | #1 corpora | yes | own spec |
| 4 | Index cost + latency | #1 run instrumentation | -- (reads #1) | captured during #1; formalized into a cost/quality report in its own slice |
| 5 | Ship: PyPI + LightRAG-shaped drop-in | -- | no | own spec |

Each slice is its own spec -> plan -> PR cycle, mirroring SP1-SP6. This document
fully specifies **#1** and sketches **#2-#5** for sequencing; #2-#5 get their own
design docs when reached.

## 4. Slice #1 -- end-to-end multi-hop QA head-to-head (detailed)

### 4.1 Home and structure

**Extend ER-KG-Bench; do not start a new harness.** ER-KG-Bench
(`packages/python/goldenmatch/benchmarks/er-kg-bench/`) already owns: the adapter
registry (`erkgbench/adapters/`), fidelity tiers (`adapters/FIDELITY.md`), the
results infrastructure (`erkgbench/run.py`, `results/`), and -- via SP6 (#1146) --
the goldengraph adapter, `qa_eval.py`, and `dataset/qa.jsonl`. It is also the
home of the isolated-venv-per-framework CI pattern (inherited from
`goldenmatch-kg`).

Add a new sub-mode, **`qa_e2e/`**, alongside the existing ER and SP6
fact-completeness modes. The ER board and the QA board stay one story.

*Rejected alternative:* a standalone `graphrag-qa-bench` package. Cleaner in
isolation but duplicates the adapter/results/CI infra and splits the benchmark
narrative across two homes. Not worth it.

New components under `benchmarks/er-kg-bench/`:

- `erkgbench/qa_e2e/harness.py` -- orchestrates `build_kg(corpus) -> handle`
  then `answer(handle, question) -> AnswerResult` per engine per corpus, records
  cost, writes results. One clear job: drive the matrix and collect.
- `erkgbench/qa_e2e/corpora.py` -- loads the two corpora into a single
  `QACorpus` shape (documents + questions + gold answers + gold supporting
  facts + per-question hop count + per-entity ambiguity tag). One job: corpus
  normalization.
- `erkgbench/qa_e2e/engines/` -- one adapter module per engine, each
  implementing the `QAEngine` protocol (4.3). Self-contained; importable only
  inside that engine's venv.
- `erkgbench/qa_e2e/metrics.py` -- EM, token-F1, supporting-fact recall, the
  decay-curve aggregation, and the LLM-judge harness. One job: scoring.
- `erkgbench/qa_e2e/run_qa_e2e.py` -- CLI entry: `--engine`, `--corpus`,
  `--max-questions`, `--budget-usd`, `--out`.

### 4.2 Corpora (the approved dual)

A single `QACorpus` dataclass normalizes both:

```
QACorpus:
  documents: list[Document]            # id, text (chunks the engines ingest)
  questions: list[QAItem]              # id, question, gold_answer,
                                       #   gold_supporting_fact_ids,
                                       #   hop_count, ambiguity_level
  name: str                            # "musique" | "engineered"
```

**Standard / credible: MuSiQue-Ans.** 2-4 hop, Wikipedia-sourced, ships gold
answer + question decomposition + supporting paragraphs. Chosen over HotpotQA
because it is constructed to defeat single-hop shortcuts (genuine multi-hop), and
over 2WikiMultiHopQA for cleaner supporting-fact gold. Entities are already
canonical here -- **that is the point**: it proves goldengraph is a *competent*
GraphRAG engine where ER gives no edge. We expect parity here; parity is a pass.
We load a fixed, seeded subset (default 300 questions) for cost control;
**only the subset id list is committed** (not the corpus), so the run is
reproducible without redistributing the dataset. MuSiQue is CC-BY-4.0; the loader
fetches it on demand (HuggingFace `datasets`) and caches locally, so the in-repo
footprint is the id list plus an attribution line -- no licensing/redistribution
question blocks PR A.

**Ambiguity-rich / thesis: an engineered corpus.** This is the load-bearing
instrument -- it produces the entire `(ER_accuracy)^hops` decay curve -- so it is
specified concretely here; the exact counts are tuned against the budget in
slice #1's plan, but the *generation contract* is fixed:

- **Typed edge set.** A small relation schema (~5 types, e.g. `works_at`,
  `located_in`, `acquired`, `authored`, `part_of`) over the existing ER-KG-Bench
  48-entity universe (`dataset/`), ~2-4 edges/entity, yielding a connected graph
  that supports paths of length 1-4. The SP6 QA model had no edges; this adds
  them. Edges are committed as data, not regenerated per run.
- **Programmatic multi-hop questions.** For hop count `k in 1..4`, sample a
  length-`k` entity path from the edge graph and template a question whose answer
  is a terminal-entity attribute. Each `QAItem` records `gold_answer`, the
  `gold entity path` (ordered entity ids traversed), and `hop_count=k`. Answers
  are short, controlled values (normalized exact match scores them).
- **Ambiguity dial (the isolating variable).** For a configurable fraction of the
  entities mentioned along each path, the supporting document refers to the
  entity by a *variant* surface form drawn from the existing failure-class
  taxonomy (`TAXONOMY.md`: abbreviation / nickname / synonym) instead of its
  canonical name. `ambiguity_level` is recorded per question. This is what lets us
  isolate the effect: hold hops fixed and sweep ambiguity, or hold ambiguity
  fixed and sweep hops.
- **Determinism.** The generator is seeded; the same seed yields byte-identical
  documents + questions (asserted in §8). The realized ambiguity fraction matches
  the requested fraction within tolerance.

The engineered corpus is synthetic -- the acknowledged trade-off for *control*.
MuSiQue is the real-world credibility anchor; the engineered corpus is the clean
thesis instrument. (A future real-world ambiguity bridge -- MultiHop-RAG, real
news with cross-document entity recurrence -- is noted in *Future*, out of scope
for #1.)

### 4.3 Engine adapters (full pipeline, real libraries)

Per the approved scope: **all four engines run their real, full pipeline**
end-to-end (index + retrieve + synthesize), not just their resolver. Each
implements one protocol:

```
class QAEngine(Protocol):
    name: str
    fidelity: str                 # "real-e2e"
    def build_kg(self, corpus: QACorpus) -> Handle: ...   # real indexing
    def answer(self, handle: Handle, question: str) -> AnswerResult: ...
    # AnswerResult: text, retrieved_fact_ids, input_tokens, output_tokens, latency_s
```

Engines: `goldengraph`, `lightrag`, `ms_graphrag` (Microsoft `graphrag`),
`graphiti` (`graphiti-core`). Each gets an **isolated venv** (their dep trees
conflict -- same reason `goldenmatch-kg` uses a venv-per-framework matrix); each
records `fidelity="real-e2e"`. The shared LLM (model + key) is injected so every
engine uses the same model -- the comparison is the KG construction + retrieval,
not the base LLM.

This is the heavy lift. Sequencing: harness + corpora + `goldengraph` adapter
land first (one PR); then **one PR per competitor adapter** (LightRAG,
MS-GraphRAG, Graphiti), each gated by its own venv lane. MS-GraphRAG is the long
pole (expensive indexing, many LLM calls per chunk).

### 4.4 Metrics

- **Standard corpus (MuSiQue):** answer **Exact Match** + **token-F1** (standard
  MuSiQue scoring), plus **supporting-fact recall** (fraction of gold supporting
  facts the engine retrieved). These are the canonical, citable numbers.
- **Engineered corpus:** **answer correctness as a function of (hop_count,
  ambiguity_level)** -> the decay curve, plotted per engine. Correctness uses
  normalized exact match against the gold answer (answers are short, controlled).
  The headline thesis claim is: goldengraph's accuracy decays *slower* in
  ambiguity and hops because its ER strands fewer facts.
- **Secondary (both corpora):** **LLM-judge win-rate** on comprehensiveness /
  diversity, the method LightRAG and MS-GraphRAG report -- so we are also
  comparable on their own turf. Judged by a fixed model with a published rubric;
  positions randomized to control order bias. Flagged as the softer metric.

`metrics.py` exposes each as a pure function over `list[AnswerResult]` + gold, so
they are independently testable on fixtures.

### 4.5 Cost and infra

- **Hard budget:** every `build_kg` + `answer` sequence runs under a
  `goldenmatch.core.llm_budget.BudgetTracker` cap, per engine per corpus
  (default `$25`, overridable via `--budget-usd`). Exceeding the cap stops that
  engine's run and records a partial result rather than silently overspending.
- **Key:** `OPENAI_API_KEY` from `.testing/.env` (gitignored); the lane reads it
  from a CI secret.
- **Where it runs:** isolated-venv matrix in a `workflow_dispatch` opt-in lane
  (`bench-graphrag-qa.yml`) on a 64GB runner; **never gates required CI**
  (real-LLM + heavy installs + cost). Mirrors the `goldenmatch-kg` lane posture.
- **Index cost capture:** the harness logs per-corpus indexing token cost +
  wall per engine. This *is* slice #4's number -- captured for free here, then
  formalized in #4.

### 4.6 Output

`results/RESULTS_QA_E2E.md` -- the headline: a 4-engine x 2-corpus answer-accuracy
table (EM / F1 / supporting-fact recall on MuSiQue; correctness on engineered),
the decay curve (accuracy vs hops, and vs ambiguity), the LLM-judge win-rate, and
a cost-per-corpus column. `results/results_qa_e2e.json` holds the raw run for
reproducibility. Honest-by-default: partial/over-budget runs are marked, and the
MuSiQue parity-expected caveat is stated inline.

## 5. Slices #2-#5 (sketch; each gets its own spec)

- **#2 Differentiator proofs.** On the #1 harness/corpora: (a) **determinism** --
  same corpus indexed twice yields a byte-identical goldengraph graph (snapshot
  JSON equality), vs measured nondeterminism for LightRAG/Graphiti; needs no LLM
  beyond fixed-temperature extraction. (b) **`as_of`** -- ingest a corpus with
  dated facts, then answer a point-in-time question correctly via the SP2 store,
  which the others cannot. (c) **ER->answer delta** -- the under-merged-vs-resolved
  answer-accuracy gap, the demo promoted to a measured number on the engineered
  corpus.
- **#3 Extraction quality.** Entity + relation extraction F1 vs gold on the #1
  corpora, goldengraph vs MS-GraphRAG/LightRAG extractors. Extraction caps
  everything downstream; this isolates it.
- **#4 Index cost + latency.** Formalize the cost column captured in 4.5 into a
  standalone cost/quality report (the GraphRAG battleground -- a cheaper index at
  equal quality is a real wedge).
- **#5 Ship.** Release the standalone `goldengraph` Python package to PyPI
  (currently uv-workspace-excluded, unreleased) and add a LightRAG-shaped drop-in
  facade (`build`/`query`) so users can swap it in. Mirrors the
  `goldenmatch-kg` drop-in pattern.

## 6. Dependencies / prerequisites

- **#1146 (SP6 QA infra) must land on main first.** It provides
  `adapters/goldengraph_adapter.py`, `qa_eval.py`, `dataset/qa.jsonl`, and
  `results/RESULTS_QA.md` -- the foundation this slice extends. As of 2026-06-20
  #1146 is OPEN (auto-merge armed).
- **#1148 (context-aware resolution) strongly wanted.** It is what makes
  goldengraph's resolver score 0.602 (the ER number this program showcases);
  stacked on #1146, also OPEN.
- The implementation slice should **branch off main after #1146/#1148 merge**, or
  stack on the #1148 branch if it starts sooner.
- External: `OPENAI_API_KEY`; pinned versions of `lightrag-hku` / Microsoft
  `graphrag` / `graphiti-core` (their APIs move fast -- pin and record the
  versions in the results header).

## 7. Risks and honest caveats

- **Cost + setup is the long pole.** Four real frameworks, MS-GraphRAG indexing
  is expensive. Mitigation: per-engine PRs, hard budget caps, seeded corpus
  subsets, opt-in lane.
- **Framework version churn.** LightRAG/graphrag/graphiti APIs change fast; a
  green lane can rot. Mitigation: pin versions, record them in results, accept
  the lane is a point-in-time snapshot.
- **MuSiQue parity is expected, not a failure.** Clean entities -> little ER
  signal -> goldengraph likely ties there. The result table must frame MuSiQue as
  the *competence* proof and the engineered corpus as the *moat* proof, or a
  reader misreads parity as weakness.
- **The engineered corpus is synthetic.** It is the thesis instrument, not a
  real-world claim; MuSiQue is the real-world anchor. State this in the results.
- **LLM-judge is soft + gameable.** Kept as a clearly-labelled secondary, with a
  published rubric and randomized positions; never the headline.
- **Adapter fairness.** Every engine must use the same base LLM and the same
  corpus chunks, or the comparison measures the wrong thing. The shared-LLM
  injection (4.3) is load-bearing; a fairness checklist ships in `FIDELITY.md`.

## 8. Testing strategy

- `metrics.py` functions unit-tested on tiny fixtures (known EM/F1/recall, a
  hand-computed decay curve) -- no LLM, runs in normal CI.
- The `QACorpus` loaders tested for shape + determinism on committed sample
  rows -- no network.
- Each engine adapter has a **mock-LLM smoke test** (stub `build_kg`/`answer`)
  validating the protocol + cost accounting -- runs in normal CI; the
  real-library integration runs only in the opt-in venv lane.
- The engineered-corpus generator is deterministic (seeded) and tested for the
  ambiguity dial (requested fraction == realized fraction within tolerance).

## 9. Sequencing / PR plan for #1

1. **PR A** -- `qa_e2e/` harness + `QACorpus` + MuSiQue loader + engineered-corpus
   generator + `metrics.py` + goldengraph adapter + the mock-LLM smoke tests +
   normal-CI metric tests. No competitor, no required-CI LLM. Produces a
   goldengraph-only `RESULTS_QA_E2E.md`.
2. **PR B** -- LightRAG adapter + its venv lane.
3. **PR C** -- MS-GraphRAG adapter + its venv lane.
4. **PR D** -- Graphiti adapter + its venv lane + the full headline results
   refresh + LLM-judge harness.

(#2-#5 follow as their own specs.)

## 10. Open questions / future

- Exact MuSiQue subset size vs budget -- start 300, tune against the `$25` cap.
- Whether to add **MultiHop-RAG** (real news, cross-doc entity recurrence) as a
  third corpus -- a real-world ambiguity bridge between MuSiQue and the
  engineered set. Deferred from #1; revisit after the first headline lands.
- A native zero-config controller port (brings zero-config to the WASM/C
  bindings) is unrelated to proof and stays out.
