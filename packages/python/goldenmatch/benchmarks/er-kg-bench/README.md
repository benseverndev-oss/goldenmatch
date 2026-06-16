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
python dataset/build_real.py         # sources.jsonl -> records.csv (real data; committed, regenerable)
python erkgbench/run.py              # -> results/RESULTS.md + results/results.json
python erkgbench/run.py --embedder st   # also activate the cosine OR-terms (MiniLM)
```

`records.csv` is committed, so `run.py` works offline with no deps beyond `polars`,
`rapidfuzz`, `goldenmatch`. Rebuilding it (`build_real.py`) fetches real surface-form
variants from **Wikidata** (`wbgetentities`) + **RxNorm** (RxNav REST) over HTTP —
stdlib only, no key. `--embedder st` additionally needs `sentence-transformers`.

## How it's fair

* **Real data, external ground truth.** Surface-form variants come from
  **Wikidata** (`altLabel` aliases, multilingual labels, distinct QIDs for
  same-name collisions) and **RxNorm** (ingredient ↔ brand). Two records match iff
  they share a **QID / RxCUI** — the ground truth is the public reference, not the
  author. `dataset/sources.jsonl` lists the curated entities; the three inherently
  synthetic classes (typo / org-suffix / cross-document-exact) are derived from
  real base names and marked in a `source` column.
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

## What the run shows

The committed `results/RESULTS.md` runs on the **real corpus** (Wikidata + RxNorm,
QID/RxCUI ground truth), so the numbers are defensible rather than invented:

* **The exact-match family collapses.** GraphRAG / LightRAG / Cognee / mem0 score
  **F1 0.089** — they match only byte-identical strings, so on real surface-form
  variation (IBM vs International Business Machines, München vs Monaco di Baviera)
  they recall almost nothing (R 0.047); their one non-zero class is
  `cross_document_exact` (the same string repeated). Four popular KG/agent-memory
  stacks effectively **cannot resolve real entity variants** — the "built-in dedup
  is shallow" thesis made concrete.
* **Fuzzy resolvers do better but over-merge.** neo4j-graphrag (0.448) and
  LlamaIndex (0.315) buy recall with a single similarity threshold and pay in
  precision (0.35 / 0.22) — they wrongly merge the two distinct "Georgia"s and
  consecutive World-Cup editions. Neo4j's `cosine>0.97 OR edit-dist<3 OR substring`
  lands at **0.554, the best framework default**.
* **goldenmatch(auto+fields) leads at F1 0.721** — **+16.7pp over the best framework
  default** — because zero-config multi-field ER (name + type + context) is a
  different mechanism from one threshold: abbreviation 0.77, cross-lingual 0.77,
  typo / org-suffix 1.0, nickname 0.85. `context` is the real Wikidata one-line
  description — discriminating, but not a hidden label.
* **The honest gaps are real and visible.** `synonym_brand` stays hard (0.14 — even
  multi-field, "Coumadin = warfarin" needs world knowledge), and the
  precision-critical negatives cost everyone (`coll_P` ~0.47): a single score can't
  separate "Apple"/"Apple Inc" (merge) from the country/state "Georgia" (don't).
* **`emb-ann` (offline char-n-gram, no key) = 0.492** — catches transliteration /
  typos the string blocker misses but over-merges short names, and **abbreviation
  (0.21) / synonym (0.12) stay unsolved** (char overlap has no world knowledge).
  The keyed semantic + LLM extensions below attack exactly those.

So on real data the differentiator is clear and defensible: goldenmatch's
multi-field probabilistic ER, run zero-config, beats every framework's built-in
default — while the bench keeps goldenmatch's own weak spots (synonym recall,
collision precision) in plain view.

## The LLM scorer on real data (measured, key-dependent — not in the committed table)

With `OPENAI_API_KEY` set the runner adds `goldenmatch(auto+llm)` (zero-config +
the per-pair `llm_scorer`, gpt-4o-mini). On real data it **earns its keep — on
precision:**

| config | F1 | coll&nbsp;P* | synm | note |
|---|---|---|---|---|
| `goldenmatch(auto+fields)` (committed) | 0.721 | 0.471 | 0.141 | multi-field, no key |
| `goldenmatch(auto+llm)` (with key) | 0.661 | **1.000** | 0.116 | LLM confirms/rejects borderline pairs |

The LLM drives **same-name-collision precision to 1.0** — it correctly refuses to
merge Georgia-the-country with Georgia-the-state, and Michael Jordan the athlete
with the scientist, which the deterministic scorer over-merges (`coll_P` 0.47). But
it **still does not crack `synonym` (0.12)**: `llm_scorer` is a precision filter on
borderline candidate pairs blocking already produced — it confirms or rejects a
pair, it cannot create the "IBM ↔ International Business Machines" pair blocking
never generated. So the LLM is a **precision tool, not a recall/semantic one** (the
synthetic run, with no genuine collisions, missed this). With a key, auto-config
also auto-enables LLM extraction on low-confidence records, lifting `auto+fields`
itself to ~0.79 — also key-dependent, also out of the committed table.

## Semantic embedding-ANN on real data (measured, key-dependent — not in the committed table)

Swapping a semantic embedder into the `emb-ann` candidate-generation path —
`goldenmatch(emb-openai)`, OpenAI `text-embedding-3-small` (stdlib HTTP, no torch),
name only, cosine ≥ 0.55:

| config | abbr | synm | xling | P | R | F1 |
|---|---|---|---|---|---|---|
| `emb-ann` (offline char-n-gram) | 0.214 | 0.116 | 0.400 | 0.447 | 0.547 | **0.492** |
| `emb-openai` (with key, name only) | 0.898 | 0.304 | 0.884 | 0.408 | 0.720 | **0.521** |
| `auto+fields` (committed, multi-field) | 0.773 | 0.141 | 0.769 | 0.869 | 0.617 | **0.721** |

World knowledge in the vectors **cracks abbreviation** (0.21 → 0.90) and lifts
cross-lingual to 0.88 — the name-only semantic win the char-n-gram path can't reach.
But on real multi-field entities it **does not beat `auto+fields`** (0.52 vs 0.72):
name-only embedding over-merges (precision 0.41), and goldenmatch's multi-field
context carries more signal than the name embedding alone. So the honest takeaway
**flips from the synthetic run** — on real data the lever is **multi-field
probabilistic ER**, with semantic embedding a useful name-only complement (best when
all you have is a name), not the headline. Reproduce the keyed rows with
`OPENAI_API_KEY=... python erkgbench/run.py` (they stay out of the committed table).

## Layout

```
dataset/sources.jsonl  curated real entities (Wikidata QIDs / RxNorm ingredients) tagged by failure class
dataset/build_real.py  sources.jsonl -> records.csv via Wikidata + RxNorm (QID/RxCUI = ground truth)
dataset/records.csv    committed corpus (record_id, mention, type, context, entity_id, failure_class, source)
erkgbench/metrics.py   pairwise P/R/F1, per failure class; determinism check
erkgbench/adapters/    base contract + goldenmatch adapter + modelled defaults
erkgbench/run.py       runner -> results/{RESULTS.md,results.json}
TAXONOMY.md            the nine failure classes, with framework citations
```

## Extending

* **Add a failure class / more entities:** add curated rows to `sources.jsonl`
  (Wikidata QIDs / RxNorm ingredients), re-run `build_real.py`. Dry-run first
  (`--dry-run`) to confirm a QID actually carries the surface forms you want.
  Negative classes (distinct entities, colliding strings) are the precision
  tests — keep adding them.
* **Crack abbreviation + synonym (done, key-gated):** the `emb-openai` mode swaps a
  semantic embedder (`text-embedding-3-small`, no torch) into the `emb-ann` path and
  cracks both classes (abbr 0.98, synm 0.73; overall F1 0.721) — see "The
  semantic-embedding result". `GoldenMatchEmbAnnAdapter(provider=...)` is the seam;
  pass `provider="local"` for a torch-free-of-cloud sentence-transformers run, or any
  `goldenmatch.embeddings.providers` name. Reproduce with `OPENAI_API_KEY` set. The
  offline char-n-gram `emb-ann` still ships as the no-key proof of the *mechanism*.
  (`auto+llm` via `OPENAI_API_KEY` is the contrast showing an LLM *pair filter* is
  the wrong tool — it can't generate the pair.)
* **Add a *live* adapter:** for systems that resolve deterministically without
  an LLM (neo4j-graphrag rapidfuzz/spaCy resolvers, LlamaIndex Cypher), a real
  adapter behind an optional extra can corroborate the model.
* **Correct a default:** if you find a modelled constant has drifted from
  source, fix it in `adapters/modeled.py` — the citation is right there.

> Companion artifact: the before/after GraphRAG demo lives in `demo/`. It shows a
> wrong agent answer from a fragmented entity (IBM split across nodes by an
> exact-match KG), resolved with zero-config goldenmatch, then correct. The
> committed before/after narrative is [`demo/DEMO.md`](demo/DEMO.md); regenerate it
> with `python demo/run_demo.py` (the key-gated over-merge tier prints when
> `OPENAI_API_KEY` is set). Its cited exact-match-family F1 is read live from
> `results/results.json`, so it never drifts from the scoreboard above.
