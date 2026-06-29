# Argument-context relation-resolution experiment — design

**Status:** Designed (approved 2026-06-29); ready for implementation plan.
**Owner:** Ben Severn
**Context:** Phase-2 of schema discovery ([[2026-06-29-goldengraph-schema-discovery-design]]) proved
that open relational synonymy is NOT resolvable from the predicate phrase pair — by any method, at any
model size (deterministic string clustering fragments, nomic embedding over-merges, goldenmatch dedupe
over strings = all singletons, a 32B LLM judge over-merges worse than a 7B). The disambiguating signal
is **argument structure** — the entities a predicate connects, their types, co-occurrence — which the
Phase-2 corpus deliberately withheld (homogeneous entities, one disjoint edge per phrasing). This is a
cheap, falsifiable experiment to test whether argument-context resolution crosses that boundary BEFORE
committing to a build or a real-corpus benchmark. It is also the next layer of the suite's core
competency: relation resolution = entity resolution applied to predicates.

## Goal

Answer one falsifiable question, locally and cheaply: **given argument-context signal and clean
structure, can we cluster relational synonyms correctly** — `works at` ≡ `is on staff at` merged while
`acquired` ≠ `authored` stay apart — the two cases every phrase-level method failed?

If yes: argument-context is the lever; open-vocab is crossable; proceed to test with real (noisy)
extraction, then a real-corpus benchmark with GoldenGraph's edge intact. If no: argument-context is
insufficient even in the best case, learned for seconds of local compute.

## The key move: local, LLM-free, Modal-free

The experiment tests RESOLUTION, not extraction. So it uses the corpus's **gold structure** — the
engineered generator already knows each doc's `(subj_id, rel, dst_id)` and the phrasing it rendered.
That yields, per surface phrasing, exact argument-context features (the `(subj,obj)` pairs it connects,
the type signature) with **zero extraction noise** — isolating the one question (is the signal
sufficient?) from extraction, the LLM, and Modal. Costs seconds, not GPU runs.

## Components

### 1. Corpus extension (`engineered.py`, gated `GOLDENGRAPH_BENCH_ARGCTX=1`)

- **Entity types.** Assign each entity a coarse type (e.g. person / org / place / concept / method) so
  each relation has a canonical `(subj_type → obj_type)` signature (`works_at`: person→org; `located_in`:
  thing→place; `authored`: person→work). Deterministic per seed.
- **Co-occurrence.** Render edges with MULTIPLE phrasings (the same `(subj,obj)` stated two ways across
  docs), so synonyms share argument distributions while distinct relations do not. Builds on the
  Phase-2 `_REL_PHRASINGS` (`GOLDENGRAPH_BENCH_REL_PARAPHRASE`). **Default for the headline best-case
  run: every edge gets all available phrasings** (co-occurrence fraction = 1.0); the ablation sweeps it.
- **Disjoint pairs across relations (clean best-case).** The current generator (`edges[e.id][rel] =
  dst`) can connect the SAME `(subj,dst)` pair via *distinct* relations, which would create `pair_set`
  overlap between distinct relations and falsely depress the Jaccard resolver's precision. Under
  `GOLDENGRAPH_BENCH_ARGCTX=1`, forbid duplicate `(subj,obj)` pairs across relations (each pair carries
  at most one relation), so distinct relations have disjoint pair-sets — the clean signal test. (The
  realistic with-collisions variant is a later ablation, not the de-risk gate.)
- Reuses the `_edge_doc_id` oracle (canonical ids encode structure); seed-stable.

### 2. Feature builder (`argctx_features`)

From the gold edges, per surface phrasing P → `{pair_set: set[(subj_id, obj_id)], type_sig:
Counter[(subj_type, obj_type)]}`. Pure; derived from corpus gold, no LLM.

### 3. Two resolvers, compared

- **Distributional + type (deterministic).** Cluster phrasings by `pair_set` **Jaccard overlap**
  (synonyms connect the same pairs), with the type signature as a blocker (only consider pairs whose
  dominant type-signatures are compatible). Free; the clean signal test.
- **goldenmatch-with-context-features.** Feed phrasings-as-records to `dedupe_df` with argument-context
  FEATURES instead of the bare phrase string — fixing the impoverished-features problem that made the
  earlier gm-over-strings prototype produce all singletons. **Record schema (one row per phrasing):**
  `type_sig` = the dominant `(subj_type→obj_type)` as a string (e.g. `"person>org"`); `neighbors` =
  sorted distinct connected entity canonical-names joined (e.g. `"Acme | Globex | Initech"`); `phrase`
  = the surface (kept as a weak feature). dedupe over (`type_sig` exact + `neighbors` fuzzy) so two
  phrasings sharing a type-signature AND overlapping neighbor sets cluster. Fails-open (dedupe error →
  singletons). Opt-in / lazy-imported so the local harness stays light.

### 4. Validation — B-cubed synonym-recovery precision/recall

Score each resolver's clusters against the known `_REL_PHRASINGS → 5 relations` ground truth using
**B-cubed precision/recall** (the standard per-element ER/clustering metric the suite already uses for
match quality). Pinning the exact metric matters: with only ~15 phrasings, one misplacement is ~7%, so
an unspecified metric family (pairwise-F vs B-cubed vs purity) could flip the 0.9 gate. B-cubed:
per-phrasing, precision = fraction of its predicted cluster that shares its true relation, recall =
fraction of its true relation captured in its cluster; report the means. Computed locally from gold.

## Success threshold (the de-risk gate)

PASS = **B-cubed precision ≥ 0.9 AND recall ≥ 0.9**, with the two must-pass cases explicit (binary,
metric-independent): `works at` ≡ `is on staff at` merged, AND `acquired` ≠ `authored` kept apart (the
cases every prior method failed). Below that, argument-context is insufficient in the best case.

## Ablation (only if the best case passes)

Re-run with **co-occurrence only** (homogeneous types) and **types only** (one phrasing per edge) to
attribute the win — distributional pair-overlap vs type signature vs only-both. Tells us what a REAL
corpus must provide for the method to work; directly informs the eventual real-corpus benchmark.

## Downstream (out of scope for the de-risk phase)

Only if recovery passes: a single gated Modal e2e run with REAL (noisy 7B) extraction + the pipeline,
to confirm the win survives downstream; then production wiring into `schema_discovery` as a new
`GOLDENGRAPH_DISCOVER_RESOLVE` backend. No LLM / no Modal in the de-risk phase itself.

## Error handling / determinism

- Seed-stable corpus + clustering (same seed → identical recovery numbers; reproducible).
- Deterministic resolver is pure; the gm resolver fails-open (error → singletons) so the comparison
  still reports.
- Phrasings a method can't place stay **singletons** (not dropped).

## Files

- Modify: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/engineered.py`
  (entity types + co-occurrence rendering, gated).
- Create: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/argctx_resolve.py`
  (`argctx_features`, the two resolvers, the recovery metric).
- Create: a small local runner / test that builds the corpus gold, runs both resolvers, prints
  precision/recall + the two must-pass cases.
