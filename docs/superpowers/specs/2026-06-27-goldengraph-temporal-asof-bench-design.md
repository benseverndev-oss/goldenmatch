# GoldenGraph temporal `as_of` capability bench (slice B2)

## Context

Slice B1 (#1278) measured the first capability RAG structurally can't do --
set/count aggregation -- with a free, deterministic gate (goldengraph exact
traversal set-F1 1.000 at every size; the structure-blind passage floor 0.3-0.5).

This slice measures the second, and the starkest: **temporal `as_of`** -- "what
was true about X *as of date D*". A graph with a bi-temporal store can answer it
exactly; a passage retriever **has no respected temporal axis at all**, so when a
fact is later corrected it cannot tell which version was current as of a *past*
date. This is a capability gap, not a quality gap -- RAG literally cannot attempt
the past-date query correctly.

This is **slice B2** of the capability program. C (ambiguity x passage_k crossover)
and D (KG-vs-KG) remain separate.

## The capability mechanic + the store constraint

`goldengraph-core`'s store is bi-temporal on EDGES (and identity), NOT on entity
attributes (canonical_name is latest-state -- SP2 scope line). So a temporal change
is modeled as an **edge whose object changes over valid-time**. From `store.rs`:
`as_of(valid_t, tx_t)` keeps the latest edge version per `(subj, predicate, obj)`
known by `tx_t`, then filters to those whose valid window contains `valid_t`
(`valid_from <= valid_t < valid_to`, open `valid_to` = current). The JSON batch
edge honors a set `valid_to`; `build_batch` only ever emits `None`, so the
Python->JSON->`PyStore.append`->`as_of` valid-time path is UNEXERCISED -- the
load-bearing risk this spec calls out (see Testing).

## Design

### The corpus (`qa_e2e/temporal.py`)

A set of `(anchor X, relation)` facts, each **corrected once** at a random
valid-time `Tc`. Reuses the entity universe (`engineered._load_entities`) + the
ambiguity dial (`_render_mention`). Per fact, two edges go into the store with
explicit valid windows:

- `X -rel-> A` : `valid_from = T1, valid_to = Tc`
- `X -rel-> B` : `valid_from = Tc, valid_to = None` (current)

…plus source passages for a real RAG to read (`"As of <T1>, X rel A."` /
`"From <Tc>, X rel B."` -- the text *states* the dates, but nothing enforces a
temporal slice). The anchor renders under variant surfaces across its docs (ER
still matters); an oracle resolver merges X. Anchors cycle the universe with the
B1 outer-cycle relation assignment so `(X, rel)` is unique per fact. Deterministic
for a seed.

Questions: per fact, sample query dates `D` in BOTH regimes --
**past** (`T1 <= D < Tc` -> gold = A) and **current** (`D >= Tc` -> gold = B):
*"As of `<D>`, what does X `<relation>`?"* (D is passed as the integer `valid_t`;
no NL date parsing.)

### Store build (B2-specific -- NOT `ablation._build_store`)

`_build_temporal_store(facts)` hand-builds the `StoreBatch` JSON directly (the shape
`build_batch` emits: `entities=[{local_id, canonical_name, typ, surface_names,
record_keys}]`, `edges=[{subj_local, predicate, obj_local, valid_from, valid_to,
source_refs}]`, `ingested_at`), setting **explicit `valid_to`** per edge. Oracle
record_keys (= canonical id) so X merges across its edge-docs. One batch per fact;
`store.append(json.dumps(batch))`.

### goldengraph answer -- exact `as_of` traversal (free, no LLM)

`goldengraph_asof(store, anchor_id, relation, D)`: `slice = store.as_of(valid_t=D,
tx_t=BIG)`; build coverage (`entity_id -> canonical` from the slice's
`surface_names` + `dials.surface_to_canon`); oracle-seed the anchor (invert
coverage); `slice.query([seed], 1)`; filter edges to `predicate == relation` with
`subj == seed` -> the single object whose valid window contains `D`. Right in BOTH
regimes by construction. No LLM, no embedder.

### Deterministic temporal-blind floor (free, no LLM)

`temporal_blind_floor(docs, anchor_surfaces, relation, D)`: return the object of the
**latest-appended** doc for `(X, rel)` (doc order; corrections appended after
originals), ignoring `D`. Correct on **current** queries, **wrong on past** queries
(returns B when asked about the pre-`Tc` state). Passages have no respected
valid-time -- that is the structural gap.

### Metric

`as_of_accuracy(predicted_obj_canonical, gold_obj_canonical) -> 1.0|0.0`, bucketed by
**regime** (`past` / `current`).

### The gate (free, deterministic, key-free, builds the wheel)

In `goldengraph-pipeline.yml`, mirroring B1:
1. **goldengraph as_of-accuracy >= 0.9 in BOTH regimes (HARD)** -- it respects
   valid-time.
2. **goldengraph beats the temporal-blind floor by >= margin on PAST-regime queries
   (HARD)** -- the floor is ~0 there; the starkest "RAG can't": it cannot answer
   "as of a past date" correctly.
3. **floor is fine on the current regime (SOFT)** -- shows the floor is specifically
   *temporal-blind*, not just broken.

Margins chosen from an observed run with slack.

### Opt-in real-LLM RAG confirmation (non-gating)

In `bench-graphrag-qa`, budget-capped: retrieve both dated passages + "As of <D>,
what does X <relation>?" -> the LLM has the valid-time TEXT but no enforced slice;
measure past-regime accuracy (confirmation it collapses). Reuses #1276's
`scorecard_llm._BudgetedLLM`. Renders into `TEMPORAL.md`.

## Components / files

New, under `erkgbench/qa_e2e/`:
- **`temporal.py`** -- `generate_temporal`, `_build_temporal_store`,
  `goldengraph_asof`, `temporal_blind_floor`, `as_of_accuracy`, the gate-assertion
  helper, `TemporalResult`, `render_temporal_md`, `run_temporal_deterministic`.
- **`run_temporal.py`** -- CLI -> `TEMPORAL.md`, exits non-zero on a HARD gate
  failure; `--with-llm` adds the real-LLM RAG row when a key is present.

CI: a new key-free `temporal` gate step in `goldengraph-pipeline.yml` (wheel +
deterministic run + the 3 assertions); the opt-in real-LLM RAG row in
`bench-graphrag-qa.yml`.

## Testing

Pure offline (the `as_of` traversal + the valid_to round-trip `importorskip` the
wheel; gold/floor/metric/gate logic is wheel-free):

1. **valid_to round-trip (wheel, FIRST)** -- append `X-rel-A [vf=1, vt=5]` +
   `X-rel-B [vf=5, vt=None]`; assert `as_of(valid_t=3)` returns ONLY A and
   `as_of(valid_t=7)` returns ONLY B. This pins the unexercised Python->store
   valid-time path; if the store ignores JSON `valid_to`, this fails first and
   localizes it.
2. **Corpus/gold** -- each fact has A-edge `[T1,Tc]` + B-edge `[Tc,None]`; a
   past-regime Q has gold=A, a current-regime Q gold=B; dates sampled in both
   regimes; the relation is stated in the question.
3. **Temporal-blind floor** -- deterministic: returns the latest doc's object in
   BOTH regimes (so it is wrong on past). Wheel-free.
4. **as_of-accuracy + regime bucketing** -- `pred==gold` -> 1.0 else 0.0.
5. **Gate verdicts** -- synthetic past/current accuracy dicts: PASS when goldengraph
   is high in both regimes and >> floor on past; FAIL when the floor matches
   goldengraph on past.
6. **goldengraph `as_of` traversal (wheel)** -- build the temporal store, `as_of(D)`
   per question, assert the gold object in both regimes.

Note: goldengraph's whole answer path is wheel-gated (it needs the store), so the
*real* numbers validate only in the gate lane -- the wheel-free tests cover the
gold/floor/metric/gate logic, and the valid_to round-trip (1) is the critical
early wheel check.

## Scope guard (YAGNI)

In: valid-time **single** correction per fact + the deterministic gate + opt-in
real-LLM RAG. Out (separate/deferred): transaction-time audit semantics (the
rejected Model B), attribute history (SP2 doesn't support it), multi-correction
chains, the ER-dial tie-in, NL date parsing (D is the integer `valid_t`), no new
entity universe. The #1274 / #1276 / B1 gates are untouched; the new gate is
additive.
