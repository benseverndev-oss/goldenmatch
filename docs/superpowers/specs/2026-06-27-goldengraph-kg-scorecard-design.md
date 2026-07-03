# GoldenGraph KG-vs-KG capability scorecard (slice D)

**Status:** design
**Date:** 2026-06-27
**Owner:** Ben Severn
**Worktree:** `gg-kg-scorecard` (branch `feat/goldengraph-kg-scorecard`)

## Problem

The ER-KG-Bench capability program has shown, with measured numbers, that pure RAG can't do
the structured queries goldengraph can: per-stage ER (slice A), set/count aggregation (B1),
temporal as-of (B2), and that graph reachability dominates a lexical floor under retrieval
starvation (C). What it has NOT done is compare goldengraph against the **other KG
frameworks** (LightRAG, MS-GraphRAG, Graphiti) which are the actual competitors. The
program's competitive thesis is that those frameworks are weakest at *entity resolution*
(LightRAG merges on exact name only; MS-GraphRAG on exact title+type; Graphiti is
nondeterministic LLM+embedding), and that goldengraph's fuzzy ER (+13pp ER F1 over the best
framework, measured in SP6/#1148) is the moat.

Slice D closes the loop: it measures whether each framework's documented ER strategy *costs
it* on the capabilities goldengraph wins, i.e. does weak ER, by under-merging entities,
degrade aggregation completeness and multi-hop reachability? This turns the ER-quality lead
into a measured *capability* lead, KG-vs-KG.

## Goal

A single KG-vs-KG capability scorecard plus a gate, in the established two-tier shape:

1. **Free, deterministic, CI-gated** scorecard: model each framework's documented ER strategy
   as a `record_key` dial and run it through two ER-driven capability metrics (whole-chain
   bridge-recall from slice A, aggregation set-F1 from slice B1), producing one table: rows = ER
   tiers, columns = capabilities. Gates that goldengraph (fuzzy ER) beats the exact-match
   framework tier on every capability.
2. **Opt-in, real-framework, ungated** confirmation: run the real LightRAG/MS-GraphRAG/
   Graphiti adapters on the aggregation corpus to confirm the dial models are faithful
   (real exact-match frameworks under-aggregate the way the dial predicts). Budget/infra-gated,
   non-gating; currently blocked on OpenAI billing.

This mirrors A/B1/B2/C: a key-free deterministic gate in `goldengraph-pipeline.yml` + an opt-in
real lane in `bench-graphrag-qa.yml`.

## Non-goals

- **Temporal (B2) is OUT of the deterministic scorecard.** B2's `build_temporal_store` bakes in
  oracle `record_keys` (identity = canonical id), making it ER-insensitive by construction;
  threading ER dials through it would redesign how that bench models identity and test a
  different sub-question ("does under-merge split an anchor's timeline"). Deferred to a future
  increment. (The real frameworks can still be measured on temporal via the existing prose-QA
  head-to-head; this slice does not add a temporal capability column.)
- **Graphiti gets NO deterministic dial.** Its ER is LLM+embedding (nondeterministic, semantic);
  faking it as a deterministic string dial would be dishonest. Graphiti is measured only in the
  opt-in real lane.
- **No new dial function.** LightRAG and MS-GraphRAG collapse to one `exact_match` tier reusing
  the existing `name_only_keys` (see below); `dials.py` is unchanged.
- No new corpora. Reuses the engineered multi-hop corpus (A) and the fan-out aggregation corpus
  (B1) as-is.
- No claim about prose multi-hop QA (that is C / the existing head-to-head). D is about the
  ER-driven capabilities.

## Architecture

New module `erkgbench/qa_e2e/kg_scorecard.py` + CLI `run_kg_scorecard.py`. NO change to
`dials.py`. Reuses `ablation._build_store`/`scorecard.bridge_recall`,
`aggregation.goldengraph_aggregate`/`set_f1`/`generate_aggregation`/`agg_documents_corpus`, and
the framework adapters in `engines/`.

### ER dials (reuse existing key policies, no new dial)

Each framework's documented ER strategy is a `record_key` policy (two mentions merge across
documents iff they share a key). The scorecard maps framework labels to EXISTING dials:

- **`exact_match`** = the existing `name_only_keys` (exact-surface merge). Represents BOTH
  LightRAG (exact name) AND MS-GraphRAG (exact name+type). On this corpus they are **provably
  identical**: every concept in `dataset/concepts.jsonl` has `entity_type == "concept"` (45/45),
  so a would-be MS-GraphRAG name+type key `f"{surface} concept"` is a constant-suffix rewrite of
  the name-only key `surface` (byte-equivalent partition on every metric). Note also that
  name+type is a *refinement* of name-only that merges a SUBSET, and both capability metrics
  reward merge *recall*, so "stricter ER" is NOT "better ER" here. One exact-match tier is both
  honest and robust (a separate MS-GraphRAG dial would, on a multi-type corpus, score LOWER than
  LightRAG, the opposite of an "ER strictness" ordering). Labeled "exact-match (LightRAG /
  MS-GraphRAG)"; the opt-in real lane measures the two frameworks separately.
- `oracle_keys` (perfect ER), `goldengraph_keys` (fuzzy), `none_keys` (no merge) are the existing
  bookends.

### Deterministic scorecard (`kg_scorecard.py`)

```
DIAL_TIERS = ["oracle", "goldengraph", "exact_match", "none"]   # best -> worst ER (merge-recall)
DIAL_KEYFN = {"oracle": oracle_keys, "goldengraph": goldengraph_keys,
              "exact_match": name_only_keys, "none": none_keys}   # label -> EXISTING keyfn

for dial in DIAL_TIERS:
    bridge_recall[dial]  = mean whole-chain bridge-recall over the engineered corpus
                           (build store under DIAL_KEYFN[dial]; reuse slice-A per-dial loop)
    aggregation_f1[dial] = mean set-F1 over the fan-out list-questions
                           (build store under DIAL_KEYFN[dial]; reuse goldengraph_aggregate + set_f1)
-> ScorecardResult{ bridge_recall: {dial->float}, aggregation_f1: {dial->float} }
```

Both metrics build the store via `ablation._build_store(corpus, g, km, typ_of)`; the only thing
that changes per row is `km`. The cross-dial seed logic (invert coverage -> seed node) is already
proven in `ablation.run_ablation` (bridge-recall) and `aggregation.goldengraph_aggregate`
(aggregation); a weak/`none` dial splits the anchor into per-doc nodes so the lowest-id seed sees
~1 member, which IS the intended capability loss. The orchestrator reuses both. NEEDS the
`goldengraph_native` wheel.

### Gate (`kg_scorecard.py`, measurement-frozen)

HARD:
1. **Fuzzy ER beats the exact-match tier (HEADLINE):** for EVERY capability metric,
   `goldengraph - exact_match >= MOAT_MARGIN`.
2. **ER-quality monotonicity (merge-recall direction):** per metric,
   `oracle >= goldengraph >= exact_match >= none` (within a frozen tolerance).
3. **Exact-match ER adds ~nothing over no-merge on reachability:** `exact_match <= none + EPS`
   on bridge-recall (the slice-A `name_only == none` finding: at the corpus ambiguity, cross-doc
   bridges almost always appear under a variant surface, so exact-surface matching buys nothing).
   Soft on aggregation (exact-match may recover some within-surface members).

`MOAT_MARGIN`, the monotonicity tolerance, and `EPS` are all frozen from the SAME measured grid
with headroom (verify-then-freeze, per B1/C). `gate_exit_code` returns 1 on any HARD failure; soft
assertions WARN. The render carries the honest caveat: *the exact-match column models the
LightRAG/MS-GraphRAG ER strategy as a record_key policy, not the full framework runtime; the
real-framework confirmation is the opt-in lane.*

### Opt-in real-framework confirmation (`run_kg_scorecard.py --with-frameworks`, ungated)

Drives the real `engines/` adapters (LightRAG, MS-GraphRAG, Graphiti) over the aggregation
list-questions: build the index over the fan-out docs, ask each `List all entities that X <rel>`
question, parse the answer text into an entity set, score set-F1 vs gold. Parsing maps surfaces to
canonical ids with the SAME first-wins scalar `s2c: dict[str, str]` the deterministic aggregation
floor builds (`aggregation.run_aggregation_deterministic` lines ~275-277), NOT the set-valued
`dials.surface_to_canon` (a surface can map to multiple canonicals; the floor's first-wins map is
what `set_f1`'s scalar gold members expect). Confirms the dial models (real exact-match frameworks
under-aggregate as `exact_match` predicts) and gives Graphiti a real (semantic-ER) number the
deterministic scorecard can't. Budget-capped via `_BudgetedLLM`, behind a `run_kg_capability`
workflow input, non-gating (`|| true`). **Expected-red until the OpenAI key is funded** (the
standing 429 blocker) and needs the FalkorDB/graphrag infra the existing head-to-head wires; this
slice reuses that lane's setup.

## Components / file structure

- `erkgbench/qa_e2e/dials.py`: UNCHANGED (the scorecard reuses `oracle_keys`/`goldengraph_keys`/
  `name_only_keys`/`none_keys`).
- `erkgbench/qa_e2e/kg_scorecard.py` (CREATE): `DIAL_TIERS`, `DIAL_KEYFN`, `bridge_recall_for_dial`,
  `aggregation_f1_for_dial`, `ScorecardResult`, `run_scorecard_deterministic`,
  `evaluate_assertions`/`gate_exit_code`/`render_scorecard_md`; opt-in `parse_entity_set`,
  `framework_aggregation_f1`, `render_framework_md`.
- `erkgbench/qa_e2e/run_kg_scorecard.py` (CREATE): CLI (`--seed --n-questions --n-anchors
  --ambiguity --out-md`; `--with-frameworks --budget-usd`).
- `tests/test_qa_kg_scorecard.py` (CREATE): wheel-free: gate verdicts on a hand-built
  `ScorecardResult` (pass + each HARD-fail mode), and the `parse_entity_set` answer->set parser
  (first-wins scalar map). No new-dial test (no new dial).
- `.github/workflows/goldengraph-pipeline.yml` (MODIFY): scorecard gate step + upload.
- `.github/workflows/bench-er-kg.yml` (MODIFY): add the wheel-free test to the pure-Python list.
- `.github/workflows/bench-graphrag-qa.yml` (MODIFY): `run_kg_capability` input + opt-in step.

## Error handling

- Deterministic core is offline + fail-closed on the gate (HARD failure -> exit 1). The dial
  key-maps never raise on well-formed corpus input.
- Graph paths reuse the slice-A/B1 store build; a missing wheel is a pipeline-level failure (the
  step runs after the wheel build, like the other gates).
- Opt-in real lane is budget-capped and `|| true`: a framework that fails to build (missing infra,
  429) is recorded as a skipped/None row, never fails the lane. Answer-parse misses score as set
  misses, not errors.

## Testing strategy (TDD)

Per-task failing-test-first, ruff-clean per commit. Gate-shape + parser tests run wheel-free
locally; the real graph metrics + full gate run in `goldengraph-pipeline`. Verify the measured
scorecard on the real corpus before freezing `MOAT_MARGIN`/tolerance/`EPS`.

## Open risks

- **The dials model ER strategy, not the full framework.** Stated explicitly in the render, and the
  opt-in lane is the faithfulness check. The deterministic gate's claim is precisely scoped: *a
  store built under the exact-match ER strategy loses capability Y* (the causal mechanism the +13pp
  ER lead predicts).
- **No measured moat would be a real finding.** If goldengraph does NOT beat the exact-match tier by
  `MOAT_MARGIN` on both metrics, STOP and surface to Ben rather than loosen the gate: it would mean
  fuzzy ER doesn't convert to a capability lead on this corpus, which contradicts the program's
  thesis and must be understood before shipping.
- **`exact_match <= none + EPS` (assertion 3) leans on the slice-A measurement** (`name_only == none`
  exactly, 0.234 == 0.234). A corpus reseed could nudge `exact_match` fractionally above `none`;
  `EPS` is frozen from the measured grid (not assumed 0) to absorb that without going red on a
  correct measurement.
