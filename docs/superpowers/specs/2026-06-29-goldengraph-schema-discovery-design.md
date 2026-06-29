# GoldenGraph schema discovery — design

**Status:** IMPLEMENTED + Phase-1 PASSED 2026-06-29. Discovery recovers the hand-fed win on the
engineered corpus **without being told the schema**: e2e answer-match **0.655** (hand-fed 0.672;
band 0.655–0.689), all 5 real relations recovered, correctly separated, with correct directions.

> **KEY CORRECTION to the design below (the LLM tie-break).** The hybrid LLM consolidation (Component
> 1d) was the ENTIRE gap: a weak 7B asked to merge same-relation clusters over-merged `acquired`+
> `authored` into one relation, mislabeling every authored edge (0.53 with it on). Disabling it ->
> pure deterministic string-clustering -> **0.655** (clean `acquired`/`authored` separation). So the
> implemented default is **deterministic-only**; the LLM tie-break is **opt-in** (`GOLDENGRAPH_
> DISCOVER_LLM=1`), not the default the design assumed. Same lesson as the rest of the program:
> deterministic structure beats LLM judgment on a weak model. The 6 spurious low-frequency relations
> the 7B's noisy extraction invents (`describes`, `involves`, ...) are present but non-load-bearing
> (no query uses them; 0.655 confirms). Knobs: `GOLDENGRAPH_DISCOVER_COSINE` (embedding-merge
> threshold, default 0.93 -- string rules are primary), `GOLDENGRAPH_DISCOVER_LLM` (tie-break, default
> off). Module: `goldengraph/schema_discovery.py`; gate `GOLDENGRAPH_SCHEMA_DISCOVER=1`.

**Status (original):** Designed (approved 2026-06-29); ready for implementation plan.
**Owner:** Ben Severn
**Context:** Generalizes the schema-constrained ingest win
([[2026-06-28-goldengraph-distilled-extractor-design]]): the 7B reached **0.672**
end-to-end and beat a 32B — but only because we *hand-fed* the 5-relation schema.
This removes the human: discover the canonical relation schema (vocabulary + per-relation
direction) from the corpus, so the win survives when the schema is unknown.

## Goal

A discovery pass that, from a corpus's open extractions, produces a `RelationSchema`
(closed relation vocabulary + forward/reverse aliases + canonical direction) — the exact
object the existing `goldengraph/schema.py::canonicalize_extraction` already consumes.
Discovery **replaces** the hand-coded `default_schema(["works_at", ...])`; nothing else in
the canonicalization path changes.

This turns "strongest on a schema-*known* benchmark" toward a general claim: clean,
direction-canonical graph construction on arbitrary text with no hand-specified schema —
*discovering* the canonical schema is how messy extraction becomes a source of truth that
yields conclusions of truth.

## Architecture — one-pass

Open-extract the whole corpus once (no vocab), discover the schema from those extractions,
canonicalize the *same* extractions with the discovered schema, then resolve + store.
**No extra LLM extraction cost** — discovery is deterministic over data we already have,
plus one small bounded LLM naming call.

```
docs ──open-extract (no vocab)──> [extractions + source sentences]
                                          │
                                          ▼
                              discover_schema(...)  ──> RelationSchema
                                          │
              ┌───────────────────────────┘
              ▼
canonicalize_extraction(extraction, schema)  (UNCHANGED)  ──> resolve ──> store ──> ask
```

Two-pass (re-extract under the discovered schema) is rejected: 2× LLM cost for marginal
gain (YAGNI). One-pass reorders ingest into a batch (extract-all → discover → canonicalize-all
→ store) instead of streaming per-doc — a contained change to `ingest_corpus`.

## Components

### 1. `discover_schema(extractions, sources, embedder, llm=None) -> RelationSchema`

The new unit (new module `goldengraph/schema_discovery.py`). Pure-ish: deterministic backbone
+ one optional bounded LLM call. Three steps:

**a. Vocabulary (deterministic).** Collect every raw predicate string; normalize
(lowercase, underscore↔space, strip). Cluster into canonical relations by a combination of
embedding cosine (reuse the run's embedder) and string edit-distance. The most frequent member
names each cluster. Output: clusters of surface predicates → one canonical relation each.

**b. Direction (the crux).** For each edge, align its subj/obj **surfaces back to the source
sentence**: the surface appearing *before* the relation phrase is the canonical subject; the one
after is the object. Flip on passive markers in the predicate phrase (`was … by`, `… ed by`,
leading `is/are/were … by`). The source order is ground truth **even when the extracted triple
is reversed** — which is exactly the signal that was missing (fine-tuning could not learn it).
Entity-type asymmetry (the dominant `(subj_type → obj_type)` pairing across a relation's mentions)
corroborates and breaks ties when types are informative; it contributes nothing on
homogeneous-entity corpora (the engineered corpus is all `concept`), which is why source order is
primary.

*Source granularity:* `sources` is the per-document text already available in `ingest_corpus`
(the docs aligned 1:1 with the extractions); no new field on the `Extraction`/`Relationship`
records. Alignment locates the subj/obj **surface strings** (`Mention.name`) and the relation
phrase within the doc text by substring position; when a doc is multi-sentence, the relevant
sentence is the one containing both surfaces. The engineered corpus is one sentence per doc, so
this is exact there; general corpora rely on the both-surfaces-present heuristic, accepting some
noise.

*Per-edge → per-surface aggregation (feeds 1c):* step (b) yields a direction *per edge*; the
forward/reverse classification is per **surface predicate phrase**, decided by the **majority
observed direction** of that phrase across all its edges within the cluster (ties → forward).
So a phrase seen mostly object-first (e.g. `acquired by`) becomes a reverse alias; mostly
subject-first becomes forward.

**c. Alias map.** Cluster members split into forward vs reverse alias sets (reverse = the
passive-phrased or type-reversed members), assembled into the `RelationSchema.{forward,reverse}`
shape the canonicalizer already holds.

**d. LLM tie-break (hybrid, bounded).** One call to name ambiguous clusters and merge
near-duplicate clusters. Bounded (a single call over cluster summaries, not per-doc); fail-open
(LLM error → keep the deterministic clustering). **Determinism posture:** the LLM merge mutates
the schema, so for a reproducible bench it is pinned (temperature 0; the merge applied as
deterministic post-processing of the parsed LLM output). The "seed-stable" guarantee below holds
strictly for the `llm=None` deterministic backbone; with the LLM tie-break on, stability is
pinned-best-effort.

### 2. Ingest wiring

A discovery pass gated `GOLDENGRAPH_SCHEMA_DISCOVER=1`. When on, the schema is *discovered*
instead of read from `GOLDENGRAPH_RELATION_VOCAB`; `canonicalize_extraction` runs unchanged with
the discovered schema. Off by default. Fail-soft: any discovery error → fall back to today's open
extraction (no canonicalization), logged.

## Data flow

`docs → open-extract (no vocab) → collect predicates + source-aligned directions →
discover_schema → canonicalize each extraction with the discovered schema → resolve → store → ask`.

## Validation & testing

**Phase 1 gate (engineered, falsifiable).** Run engineered N=60, `GOLDENGRAPH_SCHEMA_DISCOVER=1`,
no hand-fed vocab. Two bars:
- **Schema recovery:** discovered schema recovers the 5 known relations (modulo naming) with
  correct canonical direction — precision/recall on the relation set + direction agreement vs the
  known engineered schema.
- **End-to-end:** answer-match **≈ 0.672** (within ~1-question noise at N=60, i.e. ≈ 0.655–0.689).
  *Discovered ≈ given* is the real proof the win survives without a human.

**Unit tests (wheel-free, deterministic).** `discover_schema` over a small synthetic
`(source, extraction)` set returns the expected clusters, aliases, and directions — including a
reversed/passive case, so direction-from-source is tested in isolation (the way the chain/schema
levers were). Clustering must be seed-stable (same corpus → same schema) for bench reproducibility.

**Phase 2 (schema-unknown).** A harder corpus where the answer is *not* known up front: a
paraphrase-injected engineered variant (multiple surface phrasings per relation, so clustering is
actually exercised) and/or a real multi-hop set (MuSiQue). Report honestly — does discovery hold,
and how much of the 0.672-style advantage carries to messy text. No pre-committed number; the
measurement *is* the generalization test.

## Error handling

- **Unconfident predicates stay OPEN.** A predicate matching no cluster above threshold is kept
  as-is, not dropped — discovery must not *lose* edges it cannot classify (unlike the hand-fed
  schema, which drops out-of-vocab; discovery is conservative because its vocab is inferred).
- **Fail-soft:** any discovery error → fall back to open extraction, logged.
- **Determinism:** the deterministic backbone (`llm=None`) is seed-stable (same corpus → same
  schema) so the bench is reproducible; the optional LLM tie-break is pinned-best-effort (see 1d).

## Out of scope (v1)

- Re-extraction under the discovered schema (two-pass). 
- Cross-corpus schema persistence / a learned global schema library.
- Discovering entity-type taxonomies (we use whatever types extraction emits, as a tie-break only).

## What carries over

- `goldengraph/schema.py::{RelationSchema, canonicalize_extraction}` — consumed unchanged;
  discovery only replaces `default_schema`.
- The Modal bench harness (`scripts/distill/modal_bench.py`) — add a `SCHEMA_DISCOVER=1` opts run
  for the Phase 1 gate; same measure loop.
- The engineered corpus's known schema (`RELATION_SCHEMA`) — the ground truth for the recovery check.
