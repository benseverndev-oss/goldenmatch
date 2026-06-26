# GoldenGraph per-stage scorecard + ER-quality ablation

## Context

The QA-e2e head-to-head measured goldengraph's hybrid mode at **0.420** answer-match
on musique, below pure passage RAG (**0.520**), and two retrieval/synthesis levers
(free-form synthesis, path-filter) couldn't move it — because the binding
constraint is upstream (graph-only = **0.22**; the extracted triples are a lossy
compression of the source text). The deeper problem is **measurement**: a single
end-to-end answer-match number conflates four stages (extraction → resolution →
retrieval → synthesis), so when it reads 0.420 you cannot tell which stage cost the
points, and you cannot tell whether improving the resolver (goldengraph's actual
differentiator, **+13pp ER F1** over the best framework) makes downstream answers
better. QA-over-prose is also RAG's home turf — the answer is in the text — so
"beat RAG end-to-end" is the wrong target for iterating a KG/ER engine.

This spec builds the instrument that makes goldengraph **measurable per-stage and
iterable**: a per-stage scorecard plus an **ER-quality ablation** that proves or
refutes the engine's core thesis — `(ER_accuracy)^hops`: better entity resolution
strands fewer multi-hop chains, so downstream answers decay slower as hops grow.

This is **slice A** of a four-part benchmark program. Out of scope (each its own
spec): **B** capability tasks RAG can't do (aggregation/set/count, temporal
`as_of`); **C** ambiguity × `passage_k` crossover sweep; **D** KG-vs-KG competitive
(reuse the LightRAG/MS-GraphRAG/Graphiti adapters). This slice targets the
**engineered** corpus only.

## The key enabler

The engineered corpus (`erkgbench/qa_e2e/engineered.py`) already encodes the gold
graph with **no new annotation**:

- Each traversed-edge `Document.id` is `src_id::rel::dst_id` with **canonical**
  entity ids (never variant surfaces) — so a pure-Python oracle rebuilds the full
  typed gold graph by parsing document ids.
- Each `QAItem` carries `start_entity_id`, `relation_chain` (ordered relation
  names), `hop_count`, `ambiguity_level`, and `gold_answer`. Because each
  `(entity, relation)` pair has a unique edge, walking `relation_chain` from
  `start_entity_id` over the gold graph deterministically yields the gold bridge
  entities and the gold answer entity.
- Concepts (`dataset/concepts.jsonl` via `concepts_loader.load_concepts`) provide
  `canonical_id`, `entity_type`, and `variants[].surface` — the surface-form
  universe the resolution dial operates over.

## Design

### Two measurement layers

The thesis is measurable at two layers, and the split is what makes the instrument
iterable:

- **Retrieval layer — bridge-recall.** Does the retrieved subgraph *contain* the
  gold answer-chain? A pure graph operation: **no LLM, deterministic, free,
  CI-gateable.** This is the primary iteration metric.
- **Answer layer — answer-match.** Does the LLM produce the right answer? Needs a
  real LLM: costs money, non-deterministic, cannot gate. This is a **periodic
  confirmation** that the free proxy is faithful, not the iteration loop.

### The ER-quality ablation (deterministic core)

Hold **extraction fixed at oracle** (build the store from the gold triples, no LLM
extraction) so the resolution stage is isolated, and sweep a resolution-quality
dial at ingest:

| dial setting | resolver behaviour |
|---|---|
| `oracle` | every mention → its true canonical entity (perfect ER) |
| `goldengraph` | the real resolver (`goldenmatch.dedupe_df` on name + type + context, mirroring `goldengraph.resolve`) |
| `name_only` | merge only mentions whose surface strings are exactly equal |
| `none` | every mention is its own entity (no merge — maximal under-merge) |

Each resolver is a `Callable[[list[Mention]], list[ResolvedEntity]]` — exactly
goldengraph's existing `ingest.Resolver` seam (`ResolvedEntity` = `local_id,
canonical_name, typ, surface_names, record_keys, member_idx`). `oracle` uses the
concept `variants[].surface → canonical_id` map from `concepts_loader`; `name_only`
groups by exact surface string; `none` returns one entity per mention; `goldengraph`
runs `goldenmatch.dedupe_df` (fuzzy string scoring, not embedding-based).

**Store-build path (do NOT route the dial through `ingest_corpus`).** `ingest_corpus`
/ `_prepare_doc` unconditionally call `_extract(text, llm)`, so they cannot hold
extraction at oracle offline (extraction needs an LLM or a downloaded local model).
The ablation builds the store **directly** from the gold edges: construct an
`Extraction(mentions, relationships)` per document from the oracle's parsed gold
triples, call `resolver(mentions)`, then `build_batch(...)` → `store.append(...)`
(all real `ingest.py` functions). This bypasses `_extract` entirely — no LLM, no
model download — and is the only path that isolates resolution.

**Offline-gate footgun — force rerank off.** `dedupe_df` on a 3-field weighted
matchkey (name + type + context) triggers auto-config's cross-encoder **rerank**,
which downloads a HuggingFace model and **fails in the network-free gate** (package
CLAUDE.md footgun). The deterministic gate therefore runs the `goldengraph` resolver
on **name + type only** (two fields — stays under the rerank trigger; still a real
ER edge over `name_only`'s exact match). The full **name + type + context** resolver
is exercised only in the network-available real-LLM lane. (The plan pins the exact
mechanism; name+type-only is the robust default that needs no flag.)

**Bridge-recall metric (no LLM):** for each question,

1. **Oracle-seed** at the resolved node containing `start_entity_id`'s mentions
   (bypass the embedding seeder — that isolates resolution from seed quality, which
   is a separate retrieval axis deferred to slice C's seed-recall).
2. Expand the ball (`PyGraph.query`) under the engine's normal retrieval.
3. Test whether `relation_chain` is **walkable end-to-end** in the resolved +
   retrieved subgraph, from the seed to a node carrying the `gold_answer` mention.

The gold chain lives in **canonical-id space** (parsed from doc ids); the resolved
subgraph lives in **resolved-entity-id space**. Bridge-recall maps each gold
canonical edge to the resolved node(s) *carrying that entity's mentions* and checks
reachability there — never comparing ids across the two spaces directly. This
mapping is the crux: under `none`, a bridge entity's mentions scatter across
distinct resolved nodes, so no single walk connects them and the chain breaks.

Report **whole-chain hit** (binary: full chain reachable) and **per-edge recall**
(fraction of gold chain edges present), each **bucketed by `hop_count`**.
Under-merge (`none`) splits a bridge entity across mention-nodes and breaks the
walk → low recall; `oracle` keeps it whole → high recall. That mechanic *is* the
thesis.

### The gate (deterministic, key-free, builds the wheel)

Mirrors the existing SP6 `qa_eval` gate (`bench-er-kg.yml`): builds the native
goldengraph wheel (maturin), runs the ablation on a small engineered corpus with no
API key, and asserts:

1. **Monotonic in ER quality:** bridge-recall `oracle ≥ goldengraph ≥ name_only ≥
   none` (within a small tolerance).
2. **`^hops` signature:** the `oracle − none` bridge-recall gap **widens with
   hops** — assert `gap(max_hop) − gap(min_hop) ≥ margin`.
3. **Resolver earns its keep:** `goldengraph` bridge-recall is materially above
   `name_only` by a margin. **This is the number that moves when the resolver
   improves — the iteration signal.**

Margins are chosen from an observed run with slack (the SP6 lesson: 0.10 not 0.20).
The gate's small engineered corpus is generated at **`ambiguity > 0`**: at
`ambiguity = 0` there are no variant surfaces, so `name_only ≡ none` and the dial
collapses — the gate corpus must inject variants for the ER quality levels to
separate.

### The full scorecard (one page)

| stage | metric | lane | isolates |
|---|---|---|---|
| extraction | entity-F1, relation-F1 of extracted vs gold triples | real-LLM (build) | the 0.22 ceiling |
| resolution | ER-F1 (reuse `er-kg-bench` metrics) | deterministic | the +13pp moat |
| retrieval | **bridge-recall × ER-dial × hop** | **deterministic (gate)** | does ER flow to reachability |
| synthesis | answer-match given the **gold** subgraph | real-LLM | can the LLM read a correct graph (synthesis ceiling) |

The two real-LLM rows run in the existing opt-in `bench-graphrag-qa` lane:

- **extraction-F1** — during a real build, capture extracted triples, score against
  the gold triples (localizes where the 0.22 is lost).
- **synthesis-given-gold-subgraph** — feed the synthesizer the gold chain subgraph
  and measure answer-match (the upper bound retrieval+resolution work toward).

### Real-LLM confirmation of the ablation

Same ER dial, measuring **answer-match** instead of bridge-recall, run periodically
(opt-in, ~$0.3/setting). Assertion: answer-match **tracks** bridge-recall across the
dial (monotone in the same order). Divergence is itself a finding — synthesis isn't
using what retrieval surfaced.

## Components / files

New, under `erkgbench/qa_e2e/`:

- **`gold.py`** — the oracle: parse engineered `Document.id`s → typed gold graph;
  `gold_chain(qa_item)` walks `relation_chain` from `start_entity_id` → ordered
  (entity, relation, entity) edges + gold answer entity. Pure stdlib.
- **`resolvers.py`** — the four ablation resolvers as injectable `resolver=`
  callables (`oracle`, `goldengraph`, `name_only`, `none`).
- **`scorecard.py`** — the metrics: `bridge_recall(gold_chain, resolved_subgraph)`
  (whole-chain + per-edge), `extraction_f1(extracted, gold)`,
  `synthesis_given_gold(...)`, and reuse of `er-kg-bench` ER-F1.
- **`ablation.py`** — the runner: for each dial setting, build the store **directly**
  from the oracle's gold triples (per-doc `Extraction` → `resolver(mentions)` →
  `build_batch` → `store.append`, bypassing `ingest_corpus`/`_extract`), then sweep
  questions → bridge-recall matrix (by hop), the 3 gate assertions, optional
  `--with-llm` answer-match confirmation.
- **`run_ablation.py`** — CLI → writes `ABLATION.md` (matrix + assertions) always;
  `--with-llm` adds answer-match + the extraction/synthesis scorecard rows when an
  API key is present.

CI: a new **key-free `qa-ablation` gate job** (builds the wheel, runs the
deterministic ablation on a small engineered corpus, asserts the 3 properties). The
real-LLM rows wire into the existing opt-in `bench-graphrag-qa` lane.

## Testing

Pure offline tests (no LLM, no network; wheel-free where possible — the metric math
operates on dicts, only the end-to-end gate job builds the wheel):

1. **Oracle** — tiny engineered corpus → assert reconstructed gold triples; walk a
   known 3-hop `relation_chain` and assert it lands on `gold_answer`.
2. **Resolvers** — `oracle` merges all variant surfaces of one entity into one
   node; `none` merges nothing; `name_only` merges only exact-string dups;
   `goldengraph` runs `dedupe_df` deterministically.
3. **Bridge-recall** — synthetic resolved ball *containing* the gold chain →
   recall 1.0; with a stranded (under-merged) bridge entity → recall < 1.0; per-edge
   vs whole-chain both asserted.
4. **Ablation** — on a small fixture: monotonic `oracle ≥ goldengraph ≥ name_only ≥
   none` and the `oracle − none` gap widens from low-hop to high-hop.
5. **Determinism** — repeated ablation runs produce identical matrices.

## Scope guard (YAGNI)

In: scorecard + ER ablation on the **engineered** corpus. Out (separate specs):
aggregation/temporal capability corpora (B), ambiguity × `passage_k` crossover (C),
KG-vs-KG competitive (D), and the embedding **seed-recall** sub-metric (here we
oracle-seed to isolate resolution; real seed quality is a distinct retrieval axis).
No new corpus generator, no ANN index, no persisted embeddings.
