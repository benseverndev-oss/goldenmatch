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
* **goldenmatch is dogfooded** — the rows call zero-config `dedupe_df(df)` and
  let auto-config pick the strategy, the same posture as every framework at its
  default. No hand-tuned threshold. `auto` = name only; `auto+fields` = name +
  type + context; `emb-ann` = candidate generation via goldenmatch's offline
  in-house embedder (no key/torch); `auto+llm` (runner adds it only with
  `OPENAI_API_KEY`) turns on the per-pair LLM scorer auto-config reaches for.

## What the first run shows (and what it doesn't)

The committed `results/RESULTS.md` is an honest baseline, not a victory lap:

* **Exact-match family** (GraphRAG/LightRAG/Cognee/mem0) gets near-zero recall
  on everything except identical strings, **and precision 0.0 on
  `same_name_collision`** — it merges the two distinct "First National Bank"s
  (#1133 reproduced). Its `temporal_version` precision of 1.0 is trivial: it
  merges so little that it never wrongly merges anything.
* **Fuzzy resolvers** (neo4j variants) trade that for recall but score **0.37–
  0.44 precision** — they over-merge collisions *and* BTC-2020-vs-2024.
* **goldenmatch(auto+fields) leads overall F1 and is the only system scoring
  non-zero on `synonym_brand`**, with the top `cross_lingual` and
  `abbreviation` — because multi-field auto-config exploits the `context` field
  the frameworks' name-only dedup ignores. **Read the magnitude as optimistic:**
  this synthetic dataset's per-entity context is cleanly separable, so context
  acts almost like a hidden label; real extracted context is noisier. The
  honest control is `goldenmatch(auto)` name-only, which **also scores 0.0 on
  synonyms** — name strings alone don't carry "Coumadin = warfarin".
* **The negative classes are still goldenmatch's weak spot here:** zero-config
  over-merges collisions/temporal versions (`coll_P` ~0.37). A real probability
  threshold + the quality-gated review path are the fix.
* **`goldenmatch(emb-ann)` shows the offline embedding lever** — candidate
  generation via goldenmatch's in-house char-n-gram embedder (no key, no torch),
  name only. It beats the string-only `auto` (F1 0.529 vs 0.418) by catching
  cross-lingual transliteration, typos and org-suffixes the string blocker
  misses — but **abbreviation (~0.18) and synonym (0.0) stay unsolved**, because
  a char-n-gram embedding has no world knowledge (IBM↔International Business
  Machines cosine ~0.05; Coumadin↔warfarin ~0.02). Cracking those two classes
  needs a *semantic* embedding model (sentence-transformers / cloud), i.e. torch
  or a key — the bench says so plainly rather than implying the offline path
  closes the gap.

So the bench localises goldenmatch's differentiation (multi-field evidence the
name-only frameworks can't use) honestly, alongside its current gaps
(negative-class precision; name-only semantic recall). That's the point of
having it — and the dogfood makes the comparison the one a user would actually
get, not a hand-picked threshold.

## The LLM experiment (measured, key-dependent — not in the committed table)

It is tempting to assume the semantic classes just need an LLM. We measured it
(`OPENAI_API_KEY` set, gpt-4o-mini via `llm_scorer=True`). The result is the
opposite of the intuition, and it is the most useful finding here:

| config | abbr | synm | xling | P | R | overall F1 |
|---|---|---|---|---|---|---|
| `goldenmatch(auto+fields)` (no key) | 0.667 | 0.20 | 0.839 | 0.624 | 0.732 | **0.674** |
| `goldenmatch(auto+llm)` (with key)  | 0.462 | 0.00 | 0.500 | 0.762 | 0.504 | **0.607** |

The LLM made the semantic classes **worse** and lowered overall F1. Reason:
goldenmatch's `llm_scorer` is a **precision filter on borderline candidate
pairs (0.75–0.95) that blocking already produced** — it can confirm or reject a
candidate, never create one. It never saw "IBM" / "International Business
Machines" as a pair (blocking didn't generate it), so it could not merge them;
and it *pruned* some context-bridged matches `auto+fields` had kept (precision
up, recall down). The lever for the semantic classes is therefore semantic
**candidate generation** (embedding ANN blocking / `emb+ANN`), not an LLM pair
scorer. The committed table stays the offline, reproducible-by-anyone run; the
`auto+llm` row only appears when the runner sees a key.

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
* **Crack abbreviation + synonym (the last offline gap):** swap a *semantic*
  embedding into `emb-ann` in place of the char-n-gram in-house model — a
  sentence-transformers model (needs torch) or a cloud embedding (needs creds).
  The shipped `emb-ann` proves the candidate-generation *mechanism* offline and
  isolates exactly the two classes a semantic model is required for; this is the
  one remaining lever for them. (`auto+llm` via `OPENAI_API_KEY` is the contrast
  showing an LLM *pair filter* is the wrong tool — it can't generate the pair.)
* **Add a *live* adapter:** for systems that resolve deterministically without
  an LLM (neo4j-graphrag rapidfuzz/spaCy resolvers, LlamaIndex Cypher), a real
  adapter behind an optional extra can corroborate the model.
* **Correct a default:** if you find a modelled constant has drifted from
  source, fix it in `adapters/modeled.py` — the citation is right there.

> Companion artifact: the before/after GraphRAG demo (build a KG → wrong agent
> answer from fragmented/over-merged entities → resolve → correct answer) draws
> its numbers from this harness. See the project roadmap.
