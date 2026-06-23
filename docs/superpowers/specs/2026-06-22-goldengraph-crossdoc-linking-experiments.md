# GoldenGraph cross-document linking — experiment log

**Date:** 2026-06-22
**Branch / PR:** `claude/goldengraph-crossdoc-linking` → #1215 (stacked on #1209)
**Status:** active — iterating matchers against the localize trace.

---

## The goal

Make **goldengraph competitive as a GraphRAG engine on real-world multi-hop QA**
(MuSiQue), where its claimed moat — best-in-class entity resolution — should let
it answer questions whose evidence is spread across documents. Concretely: lift
`answer_match` on MuSiQue off the floor (currently 0.0) by fixing the specific,
measured reason it fails.

The narrower goal of *this* thread: **re-connect the multi-hop chains** that real
MuSiQue questions need, without corrupting the graph.

---

## The problem (localized, not guessed)

The QA-e2e **localize trace** (`GOLDENGRAPH_QA_TRACE=1`) classifies, per question,
*where* the answer is lost — extraction / retrieval-budget / retrieval-broken-chain
/ synthesis — using three graph checks plus connected-component membership.

It pinned MuSiQue's ~0 to a **shattered graph**:

- The ~375-entity graph fragments into **~60 disconnected components**.
- For every answerable question, the gold answer sits in a **different component**
  than the question's seeds (`same_component=False`) — the multi-hop chain is
  *physically severed*.
- **Root cause (code-confirmed):** `resolve.py` runs fuzzy ER only *within one
  document*; the durable store (`store.rs::append`) reconciles entities *across*
  documents **only on exact `record_key`** (a hash of the exact surface string). So
  a bridge entity mentioned as "Thomas Nabbes" in one paragraph and "Nabbes" in
  another never merges → each paragraph becomes its own island.

So the fix is **cross-document entity linking**: merge bridge entities across
paragraphs so the islands re-join. The localize trace is the built-in validator —
a working link **collapses components** and flips `same_component` to **True**.

---

## What we've tried (all opt-in: `GOLDENGRAPH_CROSS_DOC_LINK=1`)

All numbers are a **single N=3 MuSiQue trace** (directional, noisy — not a
statistically robust score). `answer_match` is 0.0 in every row so far; the
*structural* columns are the signal we steer on.

| # | matcher | graph ent | components | largest comp | token_f1 | verdict |
|---|---|---|---|---|---|---|
| 0 | **OFF** (baseline) | 375 | 60 | 55 | 0.018 | shattered (the bug) |
| 1 | naive normalized + token-subset | 351 | 58 | 72 | — | **over-merge** (one blob) |
| 2 | goldenmatch zero-config on `(name,type)` | 270 | 17 | **140** | 0.007 | **severe over-merge** — RED config |
| 3 | goldenmatch on compound key + **graph neighborhood** | 372 | 56 | 77 | **0.036** | **under-merge** (barely links) |
| 4 | embedding-threshold (cosine ≥ **0.82**) | 368 | 64 | 52 | 0.006 | **under-merge** — threshold too high |
| 5 | embedding-threshold (cosine ≥ **0.60**) | *sweeping* | | | | finding the Goldilocks cutoff |

### Why each failed — the through-line

- **#1 naive token-subset** matched on shared tokens → fused unrelated entities
  ("any Scipio") into a 152-node blob. Too blunt. (It *did* connect one chain —
  `same_component=True` for "the Politburo" — proving the lever is real.)
- **#2 name-only goldenmatch** — a bare name is **near-unique / low-signal**, so
  the zero-config controller commits a *best-effort RED config* and over-merges
  even harder (375→270 entities, a 140-node blob). goldenmatch correctly refuses an
  under-determined match.
- **#3 compound + neighborhood** — gave goldenmatch real columns (name + type +
  aliases + **incident predicates + neighbor names**). This *fixed the over-merge*
  (graph stayed 372/375, no blob) — but swung to **under-merge**, and the reason is
  the key insight:
  > **A bridge entity's neighborhood DIVERGES across paragraphs by construction.**
  > In the subject's paragraph its neighbor is the subject; in the answer's
  > paragraph its neighbor is the answer. So the `nbr`/`rel` columns say
  > *"different"* exactly when we need *"same."* Neighborhood is a great
  > disambiguator but it **suppresses the bridge merge** we're after.

**The convergent lesson:** the linking signal must be **invariant across a bridge's
separate appearances**. Name alone is invariant but too weak (→ RED/over-merge);
neighborhood is strong but *anti*-invariant for bridges (→ under-merge).

---

## Current attempt: embedding-threshold linking (#4)

Embeddings are computed from the **name/alias text alone** → invariant across
appearances. Union **same-type** entity pairs at **cosine ≥ `GOLDENGRAPH_LINK_THRESHOLD`**
(default 0.82). "Thomas Nabbes" ≈ "Nabbes" matches regardless of divergent
neighborhoods; the high cutoff (a pure threshold, *no* auto-config) avoids the
blob. Uses the engine's existing OpenAI embedder.

**Success = the Goldilocks zone:** components drop meaningfully below 60,
`same_component` flips True on more than one question, **and** the largest component
stays moderate (no 140-blob). The threshold is one env knob from a re-sweep.

---

## Levers still on the table (if #4 doesn't land it)

1. **Tune the embedding threshold** (`GOLDENGRAPH_LINK_THRESHOLD`) — one env sweep,
   no code change.
2. **Context-aware key** — thread each entity's *description* (which `resolve()`
   already extracts as `mention.context`, but the store doesn't retain) into the
   match. Invariant *and* high-signal — needs the store to carry context.
3. **Retrieval that follows the connected chain** — even when linking re-joins the
   graph (`same_component=True`), the answer can sit outside the budget-capped ball
   (measured on "the Politburo"). Once chains connect, retrieval seeding/depth is
   the next lever — possibly *semantic* retrieval across islands, which would
   sidestep the need for a perfectly connected graph.

---

## Artifacts

- Instrument: `erkgbench/qa_e2e/engines/goldengraph.py::localize` + `harness.py`
  `_localize_trace` (4-way classification + component membership); `trace` /
  `cross_doc_link` workflow inputs on `bench-graphrag-qa.yml`.
- Linker: `goldengraph/ingest.py::_cross_doc_link` (+ `_embed_cluster`,
  `_existing_features`, `_new_features`); offline tests in
  `goldengraph/tests/test_ingest_cross_doc_link.py` (stub embedder/matcher — no
  native, no goldenmatch).
- Diagnosis trail: `docs/superpowers/specs/2026-06-22-goldengraph-qa-e2e-first-headline-handoff.md`.
