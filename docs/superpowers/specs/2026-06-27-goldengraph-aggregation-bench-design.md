# GoldenGraph aggregation/set/count capability bench (slice B1)

## Context

The goldengraph benchmark program proved `(ER)^hops` at the retrieval layer (#1274)
and built the per-stage scorecard (#1276). But every measurement so far is on
**multi-hop QA over prose** -- RAG's home turf, where the KG keeps losing because
the answer is *in the text* and a passage retriever already has it.

The KG's real moat is the class of queries RAG **structurally cannot do**. This
slice measures the first such class: **aggregation / set / count** -- "list all
entities that X acquired", "how many subsidiaries does Y have". The answer is a
*set* scattered across many documents. A graph does an exact traversal; a passage
retriever sees a `passage_k` window and its recall collapses as the set outgrows it.

This is **slice B1** of the capability program. **B2** (temporal `as_of`) is its own
slice. C (ambiguity x passage_k crossover) and D (KG-vs-KG) remain separate.

## The capability mechanic

- **goldengraph** answers by **exact graph traversal**: seed the anchor entity,
  walk the stated relation's edges, return the member set. Because the resolved
  graph holds *every* `X-rel-member` edge (when ER merged X across documents), this
  is **size-invariant** -- a set of 3 and a set of 50 are equally exact.
- A **passage-window floor** can only see the `passage_k` retrieved docs. As the
  gold set grows past what those docs hold, recall **collapses**. A window cannot
  aggregate a scattered set -- that is the structural gap.

The divergence -- goldengraph flat-and-high, the floor falling with set size -- *is*
"the KG does what RAG can't", and it is measurable **free, deterministically, as a
CI gate**.

## Design

### The corpus (`qa_e2e/aggregation.py`)

A focused new generator reusing the ER-KG-Bench entity universe
(`engineered._load_entities`), the `_render_mention` ambiguity dial, and the
`src::rel::dst` doc-id convention. Unlike the engineered corpus (single object per
relation), this builds a **fan-out graph**: one source entity has *many* objects for
a relation (`X acquired A`, `X acquired B`, `X acquired C`, ...), each rendered as
its own edge document `X::acquired::A` with the anchor possibly under a variant
surface (so ER matters). Fan-out is parameterized so **gold-set-size sweeps** across
buckets (e.g. 2-4 / 5-10 / 11-30).

Two question types, each carrying gold metadata a pure oracle can verify:
- **list/set:** "List all entities that <X> <relation>." -> gold member set
  (canonical names).
- **count:** "How many entities does <X> <relation>?" -> gold = `|set|`.

The relation is **stated** (one of the 5-relation schema), so no fuzzy
relation-parse is needed. The corpus is deterministic for a seed.

### goldengraph answer -- exact traversal (free, no LLM)

`aggregation.py::goldengraph_aggregate(slice_graph, anchor_id, relation, coverage)`:
oracle-seed the anchor (the gold entity id, bypassing the embedder -- the same
isolation bridge-recall uses), `slice_graph.query([anchor_node], 1)`, filter edges
to the stated `predicate` with `subj == anchor_node`, map the `obj` nodes to their
covered canonical ids -> the member set. Count = `|set|`. No LLM, no embedder.

The store is built from the fan-out corpus the same way the #1274 ablation builds it
(`ablation._build_store` reused, or the same per-doc `Extraction -> resolver ->
build_batch -> store.append` path) with an oracle resolver so the anchor merges
across its edge-docs.

### Deterministic passage-window floor (free, no LLM)

`aggregation.py::passage_window_floor(corpus, anchor_surface, relation, passage_k)`:
retrieve the `passage_k` documents whose text mentions the anchor surface
(deterministic substring match -- no embedder), extract the entity-universe surfaces
present in those docs -> set. The floor has no structure, so it over-includes
(low precision) and, crucially, can only surface members whose edge-doc landed in
the `passage_k` window -> recall falls as the set grows. This is "RAG without a
graph", deterministic and free.

### Metrics (`qa_e2e/aggregation.py`, pure)

- **set-F1** -- precision/recall/F1 of the predicted member set vs the gold set
  (normalized canonical names). Primary, **bucketed by gold-set-size**.
- **count-accuracy** -- exact `|predicted| == gold_count` (0/1), bucketed by size.

### The gate (free, deterministic, key-free, builds the wheel)

In `goldengraph-pipeline.yml` (already builds the native wheel), mirroring #1274:
1. **goldengraph size-invariant & high:** set-F1 >= threshold in *every* size bucket.
2. **floor collapses:** passage-floor set-F1 in the largest size bucket is materially
   below the smallest bucket.
3. **the gap widens (the capability signature, HARD):** `(goldengraph - floor)` gap
   in the largest bucket exceeds the smallest by a margin -- the assertion that *is*
   "KG does what RAG can't".

Margins chosen from an observed run with slack.

### Opt-in real-LLM RAG confirmation (non-gating)

In the `bench-graphrag-qa` lane, budget-capped: retrieve top-`passage_k` docs +
LLM "list all entities that <X> <relation>" -> set-F1, bucketed by size. Confirms
the deterministic floor's collapse holds with a real LLM. Reuses the Phase-2
`scorecard_llm._BudgetedLLM` + `metrics`. Renders into `AGGREGATION.md`.

## Components / files

New, under `erkgbench/qa_e2e/`:
- **`aggregation.py`** -- corpus generator (`generate_aggregation`) + gold accessor +
  `goldengraph_aggregate` (traversal) + `passage_window_floor` + `set_f1` /
  `count_accuracy` + the gate-assertion helper + `render_aggregation_md`.
- **`run_aggregation.py`** -- CLI: runs the deterministic core (-> `AGGREGATION.md`,
  exits non-zero on a HARD gate failure); `--with-llm` adds the real-LLM RAG row when
  a key is present.

CI: a new key-free `aggregation` gate step in `goldengraph-pipeline.yml` (wheel +
deterministic run + the 3 assertions); the opt-in real-LLM RAG row in
`bench-graphrag-qa.yml`.

## Testing

Pure offline (no LLM/network; the goldengraph traversal row `importorskip`s the
wheel):
1. **Generator/oracle** -- a small fan-out corpus: gold sets match emitted edges;
   size buckets populated; relation stated in the question text.
2. **set-F1 / count-accuracy** -- synthetic predicted vs gold: perfect=1.0; missing
   member drops recall; extra drops precision; count exact match.
3. **Passage-floor collapse** -- synthetic docs + a large gold set: floor recall
   falls as set size exceeds `passage_k` (deterministic, no embedder).
4. **Gate assertions** -- synthetic per-size-bucket set-F1 dicts: PASS when the gap
   widens, FAIL when flat.
5. **goldengraph traversal** -- `importorskip("goldengraph_native")`; validates in
   the gate lane.

## Scope guard (YAGNI)

In: aggregation/set/count on the new fan-out corpus, deterministic gate +
opt-in real-LLM RAG confirmation. Out (separate slices/deferred): B2 temporal
`as_of`; the ER-dial tie-in (under-merge degrading goldengraph's set-F1 -- a natural
extension); embedding seed-recall (oracle-seed here); NL relation-parsing (relations
are stated); no new entity universe. The #1274 / #1276 gates are untouched; the new
gate is additive.
