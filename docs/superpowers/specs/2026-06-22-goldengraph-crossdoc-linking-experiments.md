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
| 6 | **goldenprofile anti-shatter engine** (PR #1217) | *measuring* | | | | structured fingerprint breaks the threshold tension |

### #6 — the goldenprofile Semantic Signature engine (the real fix)

Every matcher #1–#5 rides a SINGLE similarity number, which is why each one sat on
one side or the other of the under/over-merge line — there is no scalar threshold
that both merges a bridge's disjoint appearances AND keeps two same-category
entities apart. PR #1217's **anti-shatter scorer** escapes the tension by
STRUCTURING the evidence into a rigid `name | category | anchor | attribute`
fingerprint:

- the **defining attribute** (the per-document neighborhood — exactly what
  DIVERGES across a bridge's mentions) is a **positive-only bonus**: it can add
  confidence but never veto. That kills the Row-3 under-merge that sank #3.
- a **hard name + category gate** must pass before any merge is considered,
  regardless of embedding proximity. That kills the Row-4 over-merge that sank
  #1/#2.

Integration (this branch): `ingest._profile_cluster` maps each compound feature
row to a deterministic fingerprint (name→name, type→category, `rel`+`nbr`→the
non-vetoing attribute) and routes the merge decision through the engine's
`resolve_profiles` (SimHash-band blocking + the fusion scorer + WCC), reusing the
existing record_key-injection merge. Selected by `GOLDENGRAPH_PROFILE_LINK=1`
(precedence above the embedding matcher); merge threshold tunable via
`GOLDENGRAPH_PROFILE_MERGE_THRESHOLD` (engine default 0.72). The contract is
locked offline against the built wheel in
`tests/test_ingest_cross_doc_link.py::test_profile_cluster_repairs_shatter_but_gates_distinct_names`
(disjoint-neighborhood Nabbes reunite; Shakespeare stays apart). **Deterministic-
fingerprint cut** — LLM-synthesized node fingerprints (a real temporal/spatial
anchor + defining attribute, the full PR #1217 design) are the next slice.

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

## Measured results — the goldenprofile engine + synthesis pivot (2026-06-23)

The goldenprofile anti-shatter engine (#6) was wired in (`GOLDENGRAPH_PROFILE_LINK=1`),
first with deterministic fingerprints, then the full **LLM-synthesized** `name |
category | anchor | attribute` fingerprints (`_entity_fps` + `fp_index` persistence).
In parallel, two non-linking levers landed: the **hop-clamp fix** (retrieve.rs
`clamp(1,2)`→`clamp(1,8)`) and the **synthesis pivot** (seed-anchored prompt that
forces a named-entity answer).

| run | N (questions) | docs | graph ent | components | **answer_match** | notes |
|---|---|---|---|---|---|---|
| profile (det fp) | 3 | ~60 | 357 | 57 | 0.0 | Politburo chain re-joined (`same_component` F→T) |
| profile (LLM fp) | 3 | ~60 | 354 | 55 | 0.0 | merges more; 2/3 Qs are non-linking gaps |
| profile (LLM fp) | **30** | ~600 | 3042 | 342 | **0.2** (6/30) | first real signal off the floor |
| profile (LLM fp, incr index) | 30 | ~600 | ~3000 | 332 | 0.167 (5/30) | ±1 Q is N=30 noise; perf-validated |

**Where the loss lives at N=30 (traced):** ~6/10 **EXTRACTION** — the gold answer is a
*non-entity* (a date, money, a descriptive phrase, or a person never extracted), which
`answer_match` structurally cannot score (a metric ceiling); 2/10 **SYNTHESIS** (answer
retrieved with its edge in the ball — e.g. `Mozilla Foundation -[developed]-> Firefox`
— but the LLM still doesn't name it); 1 **RETRIEVAL-BUDGET** (the Politburo: linking
worked, `same_component=True`, but `node_budget=64` is too small for the now-8×-bigger
graph); 1 **BROKEN-CHAIN**. The binding constraint has **shifted off cross-doc linking**.

## Scaling the build (the wall that gated N≥30)

Raising N exposed three scaling walls, each fixed measure-first:

1. **Sequential per-doc LLM (~11s/doc)** — N=200 would be ~12 h. Fix: `ingest_corpus`
   runs the per-doc LLM work (extraction + fingerprint synthesis) CONCURRENTLY, commits
   to the store serially in document order (identical graph). ~10 min prepare for 600
   docs.
2. **`seed_by_query` embeds every entity name in ONE request** — >2048 inputs at N≥30
   → HTTP 400. Fix: `GoldenmatchEmbedder.embed` chunks under the provider cap; the engine
   wraps its embedder in a run-wide `_CachingEmbedder` so each entity text embeds once
   (build AND every query).
3. **`_cross_doc_link` O(N²·dim)** — it re-fed EVERY existing fingerprint + embedding to
   `resolve_profiles` per doc (JSON-serializing all embeddings each call) → ~9 min commit
   at N=30, ~400 min at N=200. Fix: `_LinkIndex` blocks committed entities by name token;
   a doc matches its new entities only against the candidate set sharing a token (where
   bridges live). Commit collapsed; N=30 step 26→15.7 min, `answer_match` held.

Net: N=200 projects to ~85 min (≈67 prepare + cheap O(N) commit + ~13 answer), under the
330-min job cap.

---

## Artifacts

- Instrument: `erkgbench/qa_e2e/engines/goldengraph.py::localize` + `harness.py`
  `_localize_trace` (4-way classification + component membership + answer-edge dump);
  `trace` / `cross_doc_link` / `profile_link` / `profile_merge_threshold` workflow
  inputs on `bench-graphrag-qa.yml`.
- Linker: `goldengraph/ingest.py` — `_cross_doc_link` (store path) + the goldenprofile
  path (`_profile_cluster`, `_entity_fps`, `_assemble_fp_texts`) + the scaling layer
  (`ingest_corpus`/`_prepare_doc`/`_commit_doc`, `_LinkIndex`/`_cross_doc_link_incremental`);
  embedder batching in `embed.py`. Offline tests in
  `goldengraph/tests/test_ingest_cross_doc_link.py` + `test_embed_batching.py`.
- Engine: PR #1217 `goldenprofile-{core,native,wasm,cabi}` + `goldengraph/profile.py`.
- Diagnosis trail: `docs/superpowers/specs/2026-06-22-goldengraph-qa-e2e-first-headline-handoff.md`.
