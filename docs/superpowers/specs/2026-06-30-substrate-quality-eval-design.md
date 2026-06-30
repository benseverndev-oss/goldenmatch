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
  (`ingest_corpus`: extract → resolve-across-docs → graph). Then build a **mention-level clustering**:
  assign each gold mention to the built node it landed in (via the doc's `source_refs` — see §4, exact,
  not fuzzy), and `score()` that clustering over the SAME gold-mention index space as Level A.
- **A − B = the extraction-induced fragmentation.** High A (good resolver) but low B (shattered graph)
  means extraction emitted inconsistent mentions the resolver never got to compare. **This decomposition
  is the headline output** — the attribution the QA arc could never make.

## The four substrate dimensions

1. **ER correctness** — `metrics.score()` **`__overall__`** pairwise/cluster P/R/F1 at both A and B.
   (Per-class failure breakdown via `score_by_class` needs per-mention `failure_class` labels +
   deliberately-injected `same_name_collision`s that the corpus does not emit today → **DEFERRED to
   v1.1** along with the collision injection. v1 ships the overall score, which is the headline.)
2. **Graph coherence** — connected components / largest-connected-fraction of the built graph (Level B;
   reuses the `same_component` machinery from the QA localize trace).
3. **Provenance completeness** — % of built edges with non-empty `source_refs` (Level B; `source_refs`
   already wired into the store + ingest).
4. **Temporal correctness** — needs bitemporal corpus rendering + as-of test queries; the corpus has no
   temporal versions today, so the `temporal_version` failure class would be empty → **DEFERRED to v2**
   (YAGNI). v1 makes no temporal claim.

## Architecture / components

Five units, mostly reuse. New code lives under `er-kg-bench` mirroring `qa_e2e`.

### 1. Corpus + gold (extend `qa_e2e/engineered.py`)

It already builds entities with canonical gold ids and renders them with surface VARIANTS across
edge-docs (the `ambiguity` dial), and each `Document` already carries `src_surface`/`dst_surface` with an
id encoding `src_id::rel::dst_id`. Add ONE emitter from the SAME generation: the list of **gold mentions**
as `(entity_id, surface, doc_id)` — two per edge-doc (src + dst). This single structure serves both
levels: it IS the record set for Level A (clustered by the resolver, scored against `entity_id`), and it
is the index space + `source_refs` anchor for Level B (§4). The text docs for Level B are already
produced. The `ambiguity` dial controls how hard cross-doc resolution is (more variants → harder).

### 2. Level A runner

Mentions → resolver (goldengraph's resolver / goldenmatch) → clusters → `metrics.score(gold, clusters)`.
Reuses the existing `er-kg-bench` adapter path.

### 3. Level B runner

`ingest_corpus(text, doc_ids=…)` → built graph; then:
- derive the **mention-level clustering** (via §4) → `metrics.score` over the gold-mention index space,
- **coherence**: connected components + largest-fraction of the graph,
- **provenance**: fraction of edges with non-empty `source_refs`.

### 4. Alignment (the one new algorithm — the engineering risk)

The unit is the **gold mention**, NOT the node — so the clustering can represent both fragmentation AND
over-merge, and survive surface collisions. The engineered corpus makes this **exact** (no fuzzy
matching):

- Each engineered doc is ONE edge `src_id —rel→ dst_id`; its two gold mentions are `(src_id,
  src_surface)` and `(dst_id, dst_surface)`, and the doc id is `_edge_doc_id(src_id, rel, dst_id)`.
- After the build, the edge for that doc carries `source_refs` containing the doc id (stage-2-D wiring,
  `ingest_corpus(doc_ids=…)`). That edge connects two built nodes (`subj`, `obj`). **So `subj` node = the
  cluster the src mention landed in; `obj` node = the dst mention's cluster** — recovered directly from
  the edge endpoints + `source_refs`. No surface heuristics; collisions are disambiguated by doc.
- The Level-B clustering is then: **group all gold mentions by the built-node id they were assigned to.**
  A gold entity whose mentions land in 3 different nodes → recall loss; a node holding mentions of 2 gold
  entities → precision loss — both representable because the clustering is over mentions.
- **Unmatched mention** (the doc produced no edge, or the edge's endpoint can't be located) → its own
  singleton cluster, tagged an *extraction miss* (a real failure the eval should count, fail-soft).
- **Tie-break / robustness:** if a doc yields multiple candidate edges (the build may emit several), pick
  the edge whose `source_refs` contain the EXACT unsuffixed doc id (the base doc id), falling back to any
  edge whose refs include a doc-id prefix; document this so it builds one way. Surface forms are used
  ONLY as a secondary check, never as the primary key (they are a deduped set and collision-prone).
- **src/dst role recovery:** the gold tuple has no explicit src/dst flag, so parse the doc id
  `src_id::rel::dst_id` (`::`-split per `_edge_doc_id`) and compare the mention's `entity_id` to the
  parsed src/dst to pick the edge endpoint (subj for src, obj for dst). **Strip the `::N` co-occurrence
  suffix** (`GOLDENGRAPH_BENCH_COOCCUR` extras) before the 3-way split. **Assumption: direction
  canonicalization OFF** (`GOLDENGRAPH_SCHEMA_CANON` can flip subj/obj relative to src/dst); the v1
  config runs canon-off, stated here so the planner doesn't assume otherwise.
- **Known precision-attribution limit (in scope to document, not fix):** if the resolver merges a single
  doc's src+dst (two distinct gold entities) into one node, the build drops the resulting self-loop
  (`build_batch` skips `s == o`), so that doc produces no edge and both mentions fall to extraction-miss
  singletons — mislabeling a within-doc over-merge as two recall misses. This does NOT affect the headline
  (the `ambiguity` dial drives CROSS-doc fragmentation, which is recall-side and correctly captured;
  cross-doc over-merge is also captured via two docs' endpoints landing on one node). Note the limit; do
  not engineer around it in v1.

This is the component to get right and the focus of the unit tests (§Testing), including the
shared-surface-across-two-entities and node-absorbs-two-entities cases.

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
- **Alignment** (core): given a built graph (nodes + edges with `source_refs`) + the gold mentions
  `(entity_id, surface, doc_id)`, assert the derived **mention-level clustering** in four cases:
  (a) a gold entity whose mentions land in 3 nodes → recall loss; (b) a node holding mentions of 2 gold
  entities → precision loss; (c) **a surface shared by two gold entities** (`ambiguity` collision) →
  still resolved correctly because assignment is by `source_refs`/doc, not surface; (d) a doc that
  produced no edge → the mention is an unmatched singleton (extraction miss). Then `metrics.score` on the
  clustering.
- **Coherence**: a stub graph with K known components → assert component count + largest-fraction.
- **Provenance**: edges with/without `source_refs` → assert coverage %.
- **End-to-end wiring**: a deterministic smoke test. NOTE `ingest_corpus` has no `extractor` param — it
  takes an injected `llm` + `resolver`; drive it with a stub `LLMClient` (returns canned extractions) +
  an injected deterministic `resolver`, asserting the full scoreboard without a real LLM. Locks Level A +
  Level B + the A−B computation.
- `metrics.score` is already tested; no new scoring math.

## The eval's OWN success criterion (its validation)

On the engineered corpus: at `ambiguity=0`, Level A ≈ Level B ≈ high (clean). As `ambiguity` rises,
**Level B should drop below Level A — the A−B gap reproducing the construction ceiling as a number.** If
the instrument surfaces the fragmentation we KNOW is there (from the QA arc), it is trustworthy. That is
the validation run.

## Scope / YAGNI

**v1 (IN):** both levels' **`__overall__` ER-F1**, the A−B gap, coherence, provenance — on the engineered
corpus (ambiguity dial) — a committed scoreboard for **goldengraph**. (Provenance is ~100% for
goldengraph alone — it always stamps `source_refs` — so it is a cheap *correctness check* in v1 and only
becomes *discriminating* in the v2 multi-engine bake-off.)

**Deferred to v1.1:**
- **Per-class failure breakdown** (`score_by_class`) — requires emitting per-mention `failure_class`
  labels and *deliberately injecting* `same_name_collision`s (today `ambiguity` only swaps a per-entity
  variant; cross-entity surface collisions are incidental). v1 ships `__overall__` ER-F1.

**Deferred to v2:**
- **Temporal as-of query correctness** (needs bitemporal corpus rendering + as-of test queries; with no
  temporal versions in the corpus the `temporal_version` class is empty, so v1 makes no temporal claim).
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
