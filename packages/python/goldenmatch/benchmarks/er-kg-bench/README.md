# ER-KG-Bench

A neutral, reproducible scoreboard for **entity-resolution quality in
knowledge-graph and agent-memory frameworks** — the benchmark this category has
never had.

It runs each framework's *documented default* dedup rule (Microsoft GraphRAG,
LightRAG, Cognee, mem0, Graphiti's deterministic floor, the Neo4j LLM
Knowledge-Graph Builder, neo4j-graphrag-python, LlamaIndex PropertyGraphIndex)
against **goldenmatch** over a labelled record set stratified by *failure
class*, and reports pairwise precision / recall / F1 per class.

Why it exists: GraphRAG/agent-memory pipelines silently merge entities during
ingestion, and unresolved or wrongly-merged entities poison every downstream
answer (accuracy decays as `(ER_accuracy)^hops`). Yet there is no shared
benchmark for how good that built-in dedup actually is. This is one.

## Quick start

```bash
cd packages/python/goldenmatch/benchmarks/er-kg-bench
python dataset/generate.py          # seeds.jsonl -> records.csv (committed; regenerable)
python erkgbench/run.py              # -> results/RESULTS.md + results/results.json
python erkgbench/run.py --embedder st   # also activate the cosine OR-terms (MiniLM)
```

No API keys, no framework installs. Only deps: `polars`, `rapidfuzz`,
`goldenmatch` (all already in this repo). `--embedder st` additionally needs
`sentence-transformers`.

## How it's fair

* **Documented defaults, not strawmen.** Every modelled adapter reproduces the
  framework's real matching rule and constants, with the source file / issue
  cited inline (`adapters/modeled.py`). E.g. Neo4j builder = `cosine>0.97 OR
  edit-dist<3 OR substring`; neo4j-graphrag = `rapidfuzz WRatio/100 ≥ 0.8`;
  LlamaIndex = `KNN-10 + word-dist<5 + cosine>0.9`.
* **Models, on purpose.** Re-implementing the published rule (vs installing 8
  frameworks + keys + LLMs) keeps the bench reproducible and removes
  LLM-nondeterminism as a confound. The adapters are small and auditable;
  correct them against source by PR if a default has moved.
* **LLM-judge layers are scoped out, not faked.** Graphiti and mem0 add an LLM
  "same?" prompt over a thin deterministic guard. We model the deterministic
  *floor* each ships (Graphiti MinHash/Jaccard≥0.9 + exact; mem0 MD5-exact) and
  flag the LLM layer's known costs (non-determinism; O(n) LLM calls →
  token-overflow/dropped episodes, Graphiti #1275; ~$0.80/40-chats #467) rather
  than simulate it.
* **goldenmatch runs at a sensible default too** — name-only for the
  apples-to-apples string comparison, and name+context to show the multi-field
  capability. No special-casing.

## What the first run shows (and what it doesn't)

The committed `results/RESULTS.md` is an honest baseline, not a victory lap:

* **Exact-match family** (GraphRAG/LightRAG/Cognee/mem0) gets near-zero recall
  on everything except identical strings, **and precision 0.0 on
  `same_name_collision`** — it merges the two distinct "First National Bank"s
  (#1133 reproduced).
* **Fuzzy resolvers** (neo4j variants) trade that for recall but score **0.37–
  0.44 precision** — they over-merge collisions *and* BTC-2020-vs-2024.
* **The semantic classes (`abbreviation`, `synonym_brand`, `cross_lingual`)
  defeat every string method here, including goldenmatch's string-only config**
  — "IBM"→"International Business Machines" has no string signal. Winning these
  needs embedding/LLM evidence, which is the next adapter (see below).
* **goldenmatch(+ctx) is the only row that keeps precision 1.0 on both negative
  classes** — multi-field disambiguation working exactly where a single score
  can't. Its recall is conservative at the default threshold; tuning + the
  semantic scorer are the open work.

In other words, the bench already localises goldenmatch's differentiation
(multi-field precision; and, once wired, semantic recall) and its current gap
(string-only recall ties the fuzzy resolvers). That's the point of having it.

## Layout

```
seeds.jsonl            ground-truth entities + tagged surface-form mentions
dataset/generate.py    seeds -> records.csv (record_id, mention, type, context, entity_id, failure_class)
erkgbench/metrics.py   pairwise P/R/F1, per failure class; determinism check
erkgbench/adapters/    base contract + goldenmatch adapter + modelled defaults
erkgbench/run.py       runner -> results/{RESULTS.md,results.json}
TAXONOMY.md            the nine failure classes, with framework citations
```

## Extending

* **Add a failure class / more entities:** edit `seeds.jsonl`, re-run
  `generate.py`. Negative classes (distinct entities, colliding strings) are
  the precision tests — keep adding them.
* **Add the semantic adapter:** a `goldenmatch(embed)` / `goldenmatch(llm)`
  configuration to attack abbreviation/synonym/cross-lingual — the classes no
  string default touches. Highest-value next step.
* **Add a *live* adapter:** for systems that resolve deterministically without
  an LLM (neo4j-graphrag rapidfuzz/spaCy resolvers, LlamaIndex Cypher), a real
  adapter behind an optional extra can corroborate the model.
* **Correct a default:** if you find a modelled constant has drifted from
  source, fix it in `adapters/modeled.py` — the citation is right there.

> Companion artifact: the before/after GraphRAG demo (build a KG → wrong agent
> answer from fragmented/over-merged entities → resolve → correct answer) draws
> its numbers from this harness. See the project roadmap.
