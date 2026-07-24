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

> **Companion board:** [`../clear-kg`](../clear-kg) (CLEAR-KG) measures the *doc→KG
> construction* axes on controlled + real-Wikipedia corpora — corpus-level ER
> (the homograph **split-rate**, a metric now first-class here too via
> `erkgbench/metrics.py::homograph_split_rate`), span-grounded **faithfulness**,
> and an end-to-end **CLEAR composite**. This board scores the real frameworks'
> ER on real reference data; CLEAR-KG proves the mechanisms and supplies the
> neighborhood-structured corpus needed to actually *separate* real homographs
> (see `../clear-kg/RESULTS.md`).

## What the graph does that RAG can't (the three axes)

The differentiated value of a *resolved* knowledge graph is **not** answering
questions over documents. On multi-hop QA (`answer_match`) a hybrid graph+passage
engine converges to plain text-RAG — the passages carry the answer and the graph
is a lossy intermediate (we measured that, and stopped optimizing it). The graph's
edge is the two things a passage-window retriever *structurally cannot do*: resolve
entities correctly, and answer **structured queries** — exact **aggregation** and
**temporal as-of** — over those resolved entities. All three are benchmarked below
on real, recognizable Wikidata data.

**1. Entity-resolution quality — leads the field.** The headline board (below):
goldenmatch's built-in dedup scores **F1 0.602** on the labelled record set, ahead
of every named framework's documented default — Neo4j-KGBuilder 0.456,
neo4j-graphrag 0.403, MS-GraphRAG / LightRAG / Cognee / mem0 at 0.066 (exact-title
floors that recover almost no fuzzy variant). Each row runs the framework's *real*
decision code (see the `fid` fidelity tier), not a strawman.

**2. Exact aggregation — size-invariant where RAG collapses.** "How many
subsidiaries of X?" on a real Wikidata company fixture (`wikidata_companies_v1.json`,
15.5k entities). GoldenGraph traverses the resolved graph exactly, so its set-F1
stays **flat (~1.0) across set sizes** while a passage-window RAG's recall collapses
past the window (**0.99 → 0.88 → 0.64** for size buckets 2-4 / 5-10 / 11-20). With
real (imperfect) resolution in play, the **count** query compounds both edges:
goldenmatch merges alias variants a naive RAG counts twice, so count-accuracy is
**1.00 / 0.88 / 0.88** vs an ER-blind RAG's **0.38 / 0.38 / 0.00** — a **+0.63 →
+0.88** gap that *widens* with set size. Reproduce:
`scripts/run_realworld_phase15_e2e.py` + `run_realworld_phase2_e2e.py`.

**3. Temporal as-of — a valid-time axis RAG doesn't have.** "Who was CEO of X **as
of a past year**?" on **550 real Wikidata CEO successions** (P169 + P580 start
dates). `store.as_of(D)` returns the person valid at D: **accuracy 1.000 on both
past and current**. A temporal-blind RAG returns the most-recent CEO — right 0.945
on current, but **0.002 on past** (2 of 550): no valid-time axis, so it always
answers with today's value. Reproduce: `scripts/run_realworld_temporal_e2e.py`.

**The honest framing:** we don't lead with QA `answer_match`, because there the
graph ≈ text-RAG. We lead with ER + aggregation + temporal — where a resolved graph
does something a passage retriever cannot. (The capability runs live in the
`goldengraph-pipeline` CI lane and write `RESULTS_REALWORLD_*.md` artifacts.)

## Quick start

```bash
cd packages/python/goldenmatch/benchmarks/er-kg-bench
python dataset/build_real.py         # sources.jsonl -> records.csv (real data; committed, regenerable)
python erkgbench/run.py              # -> results/RESULTS.md + results/results.json
python erkgbench/run.py --embedder st   # also activate the cosine OR-terms (MiniLM)
python erkgbench/run.py --dataset ghsuite   # the SECOND corpus: the suite dogfooding itself (see below)
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
* **Real execution where it's honest; models where it isn't.** Each row carries a
  `fid` tier (see `adapters/FIDELITY.md`). Many framework rows now run the
  library's REAL decision code in-process (`real-inproc`: neo4j-graphrag
  fuzzy/spaCy, LightRAG, Graphiti's deterministic floor) or reproduce its exact
  rule confirmed verbatim vs source (`validated`: neo4j-graphrag exact, GraphRAG,
  Cognee, mem0's MD5 floor). Only LlamaIndex (blog-sourced rule, unconfirmable) and
  Neo4j-KGBuilder (an `elementId`-sided guard no commutative predicate can
  reproduce) remain `modeled`. The optional real-framework deps install behind
  `requirements-real.txt`; a missing dep degrades the row to "skipped", never an
  error.
* **mem0's LLM merge layer is scoped out, not faked.** mem0 adds an LLM ADD/UPDATE
  "same?" prompt over its MD5-exact floor; we run the deterministic floor and flag
  the LLM layer's cost/non-determinism (Phase 3) rather than simulate it. Graphiti's
  floor (MinHash/Jaccard≥0.9 + exact) IS run for real; its LLM fallback is the
  out-of-scope part.
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
  **F1 0.066** — they match only byte-identical strings, so on real surface-form
  variation (IBM vs International Business Machines, München vs Monaco di Baviera)
  they recall almost nothing (R 0.034); their one non-zero class is
  `cross_document_exact` (the same string repeated). Four popular KG/agent-memory
  stacks effectively **cannot resolve real entity variants** — the "built-in dedup
  is shallow" thesis made concrete.
* **Fuzzy / semantic resolvers do better but over-merge.** neo4j-graphrag fuzzy
  (model 0.403) and LlamaIndex (0.221) buy recall with a single similarity
  threshold and pay in precision (0.35 / 0.14) — they wrongly merge the two distinct
  "Georgia"s and consecutive World-Cup editions. Neo4j's
  `cosine>0.97 OR edit-dist<3 OR substring` lands at **0.456** (0.471 with the
  embedder on — see below), the best framework row.
* **The framework rows are now mostly REAL runs, with a visible fidelity tier.**
  `neo4j-graphrag(fuzzy)*` (`real-inproc`, the library's own decision code)
  measures **0.469**, +6.6pp over its model (0.403) — proof the modeled numbers
  diverge from reality. `neo4j-graphrag(spacy)*` (`real-inproc`, real spaCy
  doc-vector resolver) measures **0.401**. See `adapters/FIDELITY.md` for the
  per-row `real` / `real-inproc` / `validated` / `modeled` audit.
* **goldenmatch(auto+fields) leads at F1 0.602** — **+13.1pp over the best framework
  row** (Neo4j-KGBuilder(emb) 0.471) — because zero-config multi-field ER (name +
  type + context) is a different mechanism from one threshold: abbreviation 0.77,
  cross-lingual 0.77, typo / org-suffix 1.0, nickname 0.85. `context` is the real
  Wikidata one-line description — discriminating, but not a hidden label.
* **The honest gaps are real and visible.** `synonym_brand` stays hard (0.17 — even
  multi-field, "Coumadin = warfarin" needs world knowledge), and the
  precision-critical negatives cost everyone (`coll_P` ~0.47): a single score can't
  separate "Apple"/"Apple Inc" (merge) from the country/state "Georgia" (don't).
* **`emb-ann` (offline char-n-gram, no key) = 0.44** — catches transliteration /
  typos the string blocker misses but over-merges short names, and **abbreviation
  (0.21) / synonym (0.14) stay unsolved** (char overlap has no world knowledge).
  The keyed semantic + LLM extensions below attack exactly those.
* **An embedder barely moves the framework rows — measured, not assumed.**
  `--embedder st` (MiniLM) adds additive `(emb)` rows: Neo4j-KGBuilder 0.456 → 0.471
  (+1.5pp), LlamaIndex 0.221 → 0.234 (+1.3pp). But the per-class F1 of the dominant
  classes is **byte-identical** with vs without the embedder (abbreviation, synonym,
  cross-lingual all flat); the small gain is only `temporal_version` / `nickname`.
  Those classes sit below a 0.9/0.97 cosine cutoff by construction, so the cosine
  OR-term can't generate the pairs string blocking misses. Both `(emb)` rows stay
  `modeled` (see FIDELITY.md).

So on real data the differentiator is clear and defensible: goldenmatch's
multi-field probabilistic ER, run zero-config, beats every framework's built-in
default — now measured against the frameworks' REAL resolution code, not just
models — while the bench keeps goldenmatch's own weak spots (synonym recall,
collision precision) in plain view.

## A second corpus: the suite dogfooding itself (`--dataset ghsuite`)

The bench carries TWO real corpora. The first (everything above) is sourced
externally from Wikidata + RxNorm. The second is **self-sourced from the Golden
Suite's own committed GitHub content** and asks a sharper question: the suite's
own ER concepts appear under many surface forms across its code, docs, and PRs
(`Fellegi-Sunter`, `F-S`, `FS`, `probabilistic matching`; `union-find`,
`UnionFind`, `disjoint-set`), so can a resolver re-unify them into one entity?

```bash
python dataset/build_ghsuite.py            # concepts.jsonl -> records_ghsuite.csv (git grep + gh)
python erkgbench/run.py --dataset ghsuite  # -> results/RESULTS_ghsuite.md + results_ghsuite.json
```

How it stays honest (the same external-ground-truth bar as the Wikidata corpus):

* **Curated, externally-anchored ground truth.** `dataset/concepts.jsonl` is a
  hand-curated dictionary of 45 ER concepts. Each carries a verified **Wikidata
  QID** (Levenshtein distance `Q496939`, Soundex `Q1502023`, record linkage
  `Q1266546`) where one exists, or a namespaced `gm:` id
  (`gm:auto_config_controller`, `gm:negative_evidence`) for suite-coined
  concepts with no clean Wikidata entry. The identity is the public reference or
  an explicit suite id, never inferred from the prose.
* **Real verbatim mentions, never invented.** The builder searches only
  **committed** suite content (`git grep`, whole-word) across the monorepo and
  the extensions repo, with a GitHub issue/PR fallback. A curated surface form
  that does not actually occur is **dropped** (drop-absent): of 143 candidate
  variants the build kept 129 and dropped 14 that no committed file uses. Each
  kept record carries its provenance file or PR in the `source` column.
* **The bench cannot self-match.** The bench's own directory (which lists every
  surface verbatim in `concepts.jsonl`) is excluded from the search, and only
  tracked files are searched (so untracked local design docs never leak), so a
  concept is found via independent suite content, not the corpus restating
  itself. 40 of the 45 concepts ended up with two or more real surface forms.
* **Snapshot-dated, selection bias visible.** Built 2026-06-19 against the suite
  at this branch. The corpus reflects what the suite happens to mention, so
  coverage is uneven by construction (a heavily-documented concept fragments
  into more surfaces than a niche one). That bias is the point: it is the real
  terminology drift a resolver faces, not a flaw to hide.

The board lands in `results/RESULTS_ghsuite.md` (bootstrapped by the
`bench-er-kg` CI lane, which self-activates once `records_ghsuite.csv` is
committed). Same adapters, metrics, fidelity tiers, and cost axis as the
Wikidata board, so the two corpora are directly comparable.

## The LLM scorer on real data (measured, key-dependent — not in the committed table)

With `OPENAI_API_KEY` set the runner adds `goldenmatch(auto+llm)` (zero-config +
the per-pair `llm_scorer`, gpt-4o-mini). On real data it **earns its keep — on
precision:**

| config | F1 | coll&nbsp;P* | synm | note |
|---|---|---|---|---|
| `goldenmatch(auto+fields)` (committed) | 0.602 | 0.471 | 0.167 | multi-field, no key |
| `goldenmatch(auto+llm)` (with key) | 0.661 | **1.000** | 0.116 | LLM confirms/rejects borderline pairs |

> The committed `auto+fields` row is current; the keyed `auto+llm` row is from a
> keyed run on an earlier corpus snapshot (before the #1039 scaling the committed
> table reflects), pending a keyed refresh. The qualitative finding (the LLM is a
> precision tool) holds regardless of the exact F1.

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
| `emb-ann` (offline char-n-gram, committed) | 0.214 | 0.138 | 0.400 | 0.455 | 0.426 | **0.44** |
| `emb-openai` (with key, name only) | 0.898 | 0.304 | 0.884 | 0.408 | 0.720 | **0.521** |
| `auto+fields` (committed, multi-field) | 0.773 | 0.167 | 0.769 | 0.786 | 0.488 | **0.602** |

> The committed rows (`emb-ann`, `auto+fields`) are current; the keyed `emb-openai`
> row is from a keyed run on an earlier corpus snapshot (pre-#1039), pending a keyed
> refresh. The qualitative finding holds regardless of the exact F1.

World knowledge in the vectors **cracks abbreviation** (0.21 → 0.90) and lifts
cross-lingual to 0.88 — the name-only semantic win the char-n-gram path can't reach.
But on real multi-field entities it **does not beat `auto+fields`** (0.52 vs 0.60):
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
dataset/concepts.jsonl      curated ER-concept dict (Wikidata QID / gm: id) for the self-sourced corpus
dataset/build_ghsuite.py    concepts.jsonl -> records_ghsuite.csv via git grep (committed content) + gh fallback
dataset/records_ghsuite.csv committed self-sourced corpus (same schema as records.csv)
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
  cracks abbreviation (abbr 0.90 keyed) but still does not beat the committed
  multi-field `auto+fields` (emb-openai 0.52 vs 0.60) — see "Semantic embedding-ANN
  on real data" (keyed rows pending a corpus refresh). `GoldenMatchEmbAnnAdapter(provider=...)` is the seam;
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
> wrong agent answer from a fragmented entity (split across nodes by an exact-match
> KG), resolved with zero-config goldenmatch, then correct.
>
> - **Shareable visual:** [`demo/demo.html`](demo/demo.html) — a self-contained page
>   rendering a REAL recorded `gpt-4o-mini` round-trip (the agent answers wrong on the
>   fragmented KG, right on the goldenmatch-resolved KG), with the before/after graphs
>   side by side. The transcript is pinned with model + date and regenerated by the
>   keyed `bench-er-kg` lane; the deterministic scaffolding (graph, retrieval, cited
>   numbers) is gated offline via `python demo/run_demo.py --check`.
> - **No-key text companion:** [`demo/DEMO.md`](demo/DEMO.md) — the deterministic
>   before/after narrative; regenerate with `python demo/run_demo.py` (the key-gated
>   over-merge tier prints when `OPENAI_API_KEY` is set).
>
> Both cite the exact-match-family F1 read live from `results/results.json`, so they
> never drift from the scoreboard above.
