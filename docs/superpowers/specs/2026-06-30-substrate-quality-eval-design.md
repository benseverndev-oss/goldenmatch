# Substrate-Quality Eval — Design

**Status:** Approved (brainstorm), pending implementation plan.
**Date:** 2026-06-30
**Context:** After the stage-2 QA arc, the north star was redefined: **goldengraph is a trustworthy,
queryable knowledge SUBSTRATE — entity-resolved, auditable (provenance), temporal — built from messy
multi-source data; question-answering is a secondary competitive surface** (the cascade already satisfies
it). The QA arc's "construction ceiling" (entity fragmentation across documents) is now revealed as THE
central problem — and we have no eval for it. This spec defines the instrument that turns substrate
quality into an optimizable number.

## Goal

A **substrate-quality harness** that scores the knowledge graph goldengraph *builds* as a knowledge base
— at two levels, so fragmentation is attributable to **extraction** vs **resolution**.

## The core idea (why it's cheap to build)

Both levels reduce to the SAME operation — **a clustering of mentions into entities, scored against gold
entity ids** — so both reuse `erkgbench/metrics.py`'s existing pairwise precision/recall/F1
(`score(entity_ids, clustering)`) and failure classes (`same_name_collision`, `temporal_version`). No new
scoring math.

- **Level A — resolver in isolation:** feed the engineered corpus's *mentions* (as records) straight to
  the resolver → clusters → `score()` vs gold. (Essentially the existing record→cluster ER eval, on
  engineered mentions.)
- **Level B — end-to-end construction:** run the engineered corpus *text* through the full build
  (`ingest_corpus`: extract → resolve-across-docs → graph); the built graph's **nodes ARE the clusters**
  (each node = the mentions merged into it) → align nodes to gold ids → `score()` the node-clustering.
- **A − B = the extraction-induced fragmentation.** High A (good resolver) but low B (shattered graph)
  means extraction emitted inconsistent mentions the resolver never got to compare. **This decomposition
  is the headline output** — the attribution the QA arc could never make.

## The four substrate dimensions

1. **ER correctness** — `score()` at both A and B (pairwise/cluster F1 + the existing failure classes).
2. **Graph coherence** — connected components / largest-connected-fraction of the built graph (Level B;
   reuses the `same_component` machinery from the QA localize trace).
3. **Provenance completeness** — % of built edges with non-empty `source_refs` (Level B; `source_refs`
   already wired into the store + ingest).
4. **Temporal correctness** — the resolution-level `temporal_version` failure class is free at Level A;
   full as-of *query* correctness needs a temporal corpus extension → **DEFERRED to v2** (YAGNI).

## Architecture / components

Five units, mostly reuse. New code lives under `er-kg-bench` mirroring `qa_e2e`.

### 1. Corpus + gold (extend `qa_e2e/engineered.py`)

It already builds entities with canonical gold ids and renders them with surface VARIANTS across
edge-docs (the `ambiguity` dial). Add two emitters from the SAME generation:
- (a) the flat **mention records** + gold `mention → entity_id` map (for Level A),
- (b) the **text docs** (for Level B; already produced),
both carrying the gold ids + surface forms. The `ambiguity` dial controls how hard cross-doc resolution
is (more variants → harder).

### 2. Level A runner

Mentions → resolver (goldengraph's resolver / goldenmatch) → clusters → `metrics.score(gold, clusters)`.
Reuses the existing `er-kg-bench` adapter path.

### 3. Level B runner

`ingest_corpus(text)` → built graph; then:
- derive the **node-clustering over the gold mentions** (via §4) → `metrics.score`,
- **coherence**: connected components + largest-fraction of the graph,
- **provenance**: fraction of edges with non-empty `source_refs`.

### 4. Alignment (the one new algorithm — the engineering risk)

Map *built graph nodes → gold entity ids*. Each gold entity has known surface variants; for each built
node, match its `surface_names` to the gold surfaces to assign it to a gold entity (or mark
unmatched/noise). A gold entity whose surfaces land in 3 nodes → recall loss; a node absorbing 2 gold
entities → precision loss. **Surface-set matching with explicit unmatched handling, fail-soft.** This is
the component to get right and the focus of the unit tests.

### 5. Scoreboard emitter

A committed markdown: per engine/config — `ER-F1(A)`, `ER-F1(B)`, the **A−B gap**, component count /
largest-fraction, provenance coverage.

## Data flow

```
engineered(seed, ambiguity) ──► gold {mention→entity_id, surfaces}
   ├─ Level A: mentions ─► resolver ─► clusters ─► score ─► ER-F1(A)
   └─ Level B: text ─► ingest_corpus ─► graph
                         ├─ nodes ─► align-to-gold ─► node-clustering ─► score ─► ER-F1(B)
                         ├─ components ─► coherence
                         └─ edge source_refs ─► provenance
   ──► scoreboard {ER-F1(A), ER-F1(B), A−B gap, coherence, provenance}
```

## Error handling

- Unmatched built nodes (no gold surface match) → counted as unmatched noise, fail-soft (do not crash
  the score).
- Per-doc build/extraction errors → the existing fail-soft ingest path (empty extraction, not a crash).
- `ambiguity=0` is the clean control; raising it is the difficulty knob.

## Testing

The new logic is mostly pure → box-safe TDD:
- **Alignment** (core): given a built graph with known node `surface_names` + a gold surface→entity map —
  a gold entity split across 3 nodes → recall loss; a node absorbing 2 gold entities → precision loss; an
  unmatched node → noise. Assert the derived node-clustering, then `metrics.score` on it.
- **Coherence**: a stub graph with K known components → assert component count + largest-fraction.
- **Provenance**: edges with/without `source_refs` → assert coverage %.
- **End-to-end wiring**: a deterministic smoke test with a STUB extractor + STUB resolver (the existing
  `qa_e2e` stub pattern), asserting the full scoreboard without an LLM — locks Level A + Level B + the
  A−B computation.
- `metrics.score` is already tested; no new scoring math.

## The eval's OWN success criterion (its validation)

On the engineered corpus: at `ambiguity=0`, Level A ≈ Level B ≈ high (clean). As `ambiguity` rises,
**Level B should drop below Level A — the A−B gap reproducing the construction ceiling as a number.** If
the instrument surfaces the fragmentation we KNOW is there (from the QA arc), it is trustworthy. That is
the validation run.

## Scope / YAGNI

**v1 (IN):** both levels' ER-F1 + failure classes, the A−B gap, coherence, provenance — on the engineered
corpus (ambiguity dial) — a committed scoreboard for **goldengraph**.

**Deferred to v2:**
- **Temporal as-of query correctness** (needs bitemporal corpus rendering + as-of test queries; the
  resolution-level `temporal_version` failure class still ships free at Level A).
- **Competitor adapters → the reframed substrate bake-off** (v1 builds the INSTRUMENT on goldengraph;
  adding other KG-construction engines as adapters and running the bake-off is the follow-on —
  instrument-before-comparison, as stage-2-A built the metric before ranking).
- **Real corpora** (external validity, after the engineered instrument works — synthetic-first, the
  whole arc's pattern).

## Files

- Modify: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/engineered.py` (gold
  mention→entity_id + mention-records emitters).
- Create: `erkgbench/substrate_eval.py` (alignment, coherence, provenance, the A/B scoring) +
  `erkgbench/run_substrate_eval.py` (CLI + scoreboard).
- Create: `tests/test_substrate_eval.py` (alignment, coherence, provenance, stub end-to-end).
- Report: `docs/superpowers/reports/2026-06-30-substrate-quality-eval.md` (the ambiguity-sweep validation
  scoreboard).
