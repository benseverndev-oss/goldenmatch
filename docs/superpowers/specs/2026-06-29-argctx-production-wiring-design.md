# Argument-context resolution — production wiring (stage 1) — design

**Status:** Designed (approved 2026-06-29); ready for implementation plan.
**Owner:** Ben Severn
**Context:** The argument-context experiment ([[2026-06-29-argument-context-relation-resolution-experiment-design]])
PROVED — locally, on gold structure — that resolving predicates by the entity pairs they connect
crosses the open-vocab synonymy boundary that defeated every phrase-level method (B-cubed 1.0/1.0;
co-occurrence is the necessary+sufficient signal, ablation-confirmed). This wires that resolver into the
LIVE goldengraph discovery pipeline (real 7B extraction, not gold) and validates it on a controlled
co-occurrence corpus — stage 1 of a staged rollout (real-corpus benchmark is the follow-up milestone).

## Goal

Make open-vocab schema discovery work in the live pipeline: a `GOLDENGRAPH_DISCOVER_RESOLVE=argctx`
backend that clusters the 7B's extracted predicates by the **surface entity pairs** they connect, so
synonymous relations merge on a corpus that provides co-occurrence — recovering toward the closed-vocab
discovery number (~0.65) where every phrase-level method collapsed to ~0.17.

## Architecture

Two focused changes plus a validation run; the canonicalizer and ingest flow are untouched.

- The argctx resolver is a new value of the existing `GOLDENGRAPH_DISCOVER_RESOLVE` switch in
  `schema_discovery.discover_schema`. It reuses the experiment's proven pair-set Jaccard logic, but over
  **surface-normalized pairs built from the live extracted edges** — which `discover_schema` already
  collects in its `by_phrase` map (`predicate -> [(subj_surface, pred, obj_surface, source)]`). So the
  resolver needs almost no new threading; the data is already in hand.
- Co-occurrence alone is sufficient (ablation: types neither necessary nor sufficient), so there is **no
  entity-typing dependency**.
- The engineered corpus gains a co-occurrence rendering mode so the live pipeline actually HAS the
  signal (the existing `GOLDENGRAPH_BENCH_REL_PARAPHRASE` renders ONE phrasing per edge — the
  no-co-occurrence case that failed).

```
live docs (co-occurrence) -> 7B extract -> discover_schema [argctx: surface-pair-set clustering]
   -> RelationSchema -> canonicalize_extraction (UNCHANGED) -> resolve -> store -> ask
```

## Components

### 1. `_cluster_predicates_argctx(by_phrase)` — `schema_discovery.py`

`by_phrase` maps each predicate to its extracted edges. Build `pair_set[pred] = {(_norm(subj_surface),
_norm(obj_surface))}` and cluster by pair-set **Jaccard ≥ threshold** (default **0.3** — more lenient
than the gold experiment's 0.5, for recall under live-extraction pair noise; env-tunable
`GOLDENGRAPH_ARGCTX_JACCARD`) via union-find — the experiment's `resolve_distributional` without the type
blocker (surface-only). Wire as `argctx` in `discover_schema`'s `GOLDENGRAPH_DISCOVER_RESOLVE` dispatch.
Singleton-isolation is automatic: a predicate sharing no pairs stays its own cluster.

### 2. Co-occurrence corpus rendering — `engineered.py` (gated `GOLDENGRAPH_BENCH_COOCCUR=1`)

Render the SAME edge `(subj, rel, dst)` in MULTIPLE docs — one per phrasing from `_REL_PHRASINGS[rel]`
— so the same `(subj,obj)` pair appears with different predicate phrasings (co-occurrence).

**Doc-id reconciliation (load-bearing).** The question generator records gold support as the BASE
`_edge_doc_id(cur, rel, nxt)` (`engineered.py:161`), so doc-ids cannot be blindly suffixed or every
`gold_supporting_fact_ids` entry dangles. Rule: **phrasing index 0 keeps the unsuffixed base
`_edge_doc_id`; only the ADDITIONAL phrasings get a `::<i>` suffix.** Then the base doc still exists, the
sampled multi-hop questions (text, gold_answer, `gold_supporting_fact_ids`) are **byte-identical** to
the non-co-occurrence corpus, and the only change is extra docs. Default off; composes with `ambiguity`
(stage 1 uses ambiguity=0 so surfaces are canonical and pairs align without a resolution step).

### 3. No change to `canonicalize_extraction` / the walk

`argctx` is just another `GOLDENGRAPH_DISCOVER_RESOLVE` value; the discovered `RelationSchema` flows
through the existing path unchanged.

## Validation — the live-pipeline proof (the delta)

Two e2e runs on the SAME co-occurrence corpus (`BENCH_COOCCUR=1`, ambiguity=0, live 7B), so the delta
attributes the win to argctx, not an easier corpus:

- **argctx** (`DISCOVER_RESOLVE=argctx`): target recovery toward **~0.65** (closed-vocab discovery); the
  discovered-schema dump should show live-extracted synonyms clustered into the right relations.
- **default string backend** (control): expected to **fragment (~0.2)** — string clustering can't merge
  synonyms (the Phase-2 result).

**PASS = argctx clearly beats the default on the same corpus AND lands in range of closed-vocab
discovery (≥ ~0.55, allowing for the live-extraction noise the gold experiment didn't have).** That
delta proves the validated signal carries into the live, noisy pipeline. If argctx ≈ default (~0.2), the
live 7B isn't producing usable co-occurrence (extraction too noisy / surface pairs don't align) — a
real finding, diagnosed via the schema dump (the existing `[schema-discover]` logging).

**Unit tests (wheel-free, deterministic).** `_cluster_predicates_argctx` over a small synthetic
`by_phrase` (two phrasings sharing pairs merge; a distinct-pair predicate stays apart; a no-pair
spurious predicate stays a singleton); it uses `schema_discovery._norm` (already imported) for surface
normalization. The co-occurrence renderer (assert the exact invariants, not a vague "unchanged"): the
document SET is a strict SUPERSET of the non-co-occurrence corpus's; doc-ids are unique; and the
generated question objects (`text`, `gold_answer`, `gold_supporting_fact_ids`) are **byte-identical** to
the non-co-occurrence corpus at the same seed (proving phrasing-0-keeps-base-id resolved the dangling
references).

## Error handling

- `argctx` fails-soft: any error in the backend → fall back to the default string clustering, logged.
- Extraction-noise isolation: a spurious predicate with no shared pairs stays a **singleton** (pair-set
  Jaccard 0 with everything) — cannot pollute real clusters.
- Determinism: the clustering is deterministic given a fixed extraction; the e2e carries the usual
  live-7B non-determinism (same caveat as every live run).

## Scope (YAGNI)

- **Surface-normalized pairs only.** Resolved-entity identity (for surface variants / real corpora) is
  the explicit real-corpus-stage follow-up, NOT stage 1. Stage 1 uses ambiguity=0 so surfaces align.
- **No type signal** (co-occurrence is sufficient — ablation-proven).
- Stage 1 = co-occurrence engineered live-pipeline proof; the **real-corpus benchmark is the next
  milestone** (where resolved-entity identity and real co-occurrence density get tested).
- No change to the canonicalizer or the ingest control flow.

## Files

- Modify: `packages/python/goldengraph/goldengraph/schema_discovery.py`
  (`_cluster_predicates_argctx` + the `argctx` dispatch branch).
- Modify: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/qa_e2e/engineered.py`
  (co-occurrence rendering, gated).
- Modify/Create: `packages/python/goldengraph/tests/test_schema_discovery.py` (argctx unit tests);
  a corpus test under the bench `tests/` dir for the co-occurrence renderer.
- Validation: the existing `scripts/distill/modal_bench.py` (opts pass the new env flags; no bench code
  change beyond what already threads opts → env).

---

## Results — DIAGNOSED HONEST-NULL (2026-06-29)

Stage 1 ran end to end on live 7B extraction. The machinery works; the signal does not carry at
realistic sparsity. Recording the negative so the line is closed, not silently dropped.

### What worked

- **Query-side schema canonicalization** (the missing half): `ingest_corpus` returns the discovered
  `RelationSchema`; the bench engine threads it into `ask(query_schema=)`; `ask` routes the query
  relation through the same schema (`_canon_query_rel`). Without it, relabeling a cluster to an
  arbitrary synonym (`located_in` → `sits_within`) breaks query matching. **Fix moved 0.086 → 0.586**
  on the all-phrasings co-occurrence corpus. Shipped (`dff6cb67`).
- **argctx clustering** groups synonyms by shared `(subj,obj)` pairs on live extraction — confirmed in
  the schema dumps. The distributional hypothesis itself is sound (gold experiment: B-cubed 1.0 under
  DENSE co-occurrence).

### What didn't — the necessity test failed

The all-phrasings corpus was a **non-discriminating control**: it renders the canonical phrasing on
every edge, so the default (canonical-label) backend already answered everything (0.655) and argctx
wasn't needed (0.586, slightly *worse* via relabeling noise). Fixed the corpus to render **one random
extra phrasing per edge** (`7212c90b`): 75% of edges still co-occur (clustering signal) but the
canonical word is absent from ~42% of edges — reachable only by clustering. On that discriminating
corpus:

| backend | answer_match (58 q) |
|---|---|
| argctx (jaccard 0.2) | **0.328** |
| default (string) | **0.345** |

A statistical tie (Δ = 1 question). argctx does **not** beat the default. Cause: sparse co-occurrence
→ **partial** clustering (catches ~2-of-3 synonyms; `acquired`/`belongs_to` fragment) → the gain on
recovered canonical-free edges cancels against relabeling noise.

### All three rescue signals measured out

| signal | result |
|---|---|
| distributional (argctx pair-set Jaccard) | partial clustering at realistic sparsity → ties default |
| lexical (token similarity) | within-synonym content-token overlap = 0.00; only stopword signal, which false-merges `works_at`↔`located_in` |
| semantic (nomic embedding) | `cosine_probe`: min(within-synonym)=0.527 < max(across-relation)=0.605, margin **−0.078** → NO threshold separates synonyms from distinct relations |

The embedding kill-test (`scripts/distill/modal_bench.py::cosine_probe`, `dc6df27e`) was decided in
~2 min instead of a full e2e gamble.

### Conclusion

Open-vocab synonym resolution on the **7B + nomic** stack cannot be done by clustering (distributional,
lexical, or semantic) when co-occurrence is sparse — the realistic regime. The argument-context method
is necessary-and-sufficient only under DENSE co-occurrence (gold experiment), which real text won't
supply. This matches the concluded OSS-LLM arc: the deterministic `SCHEMA_CANON` path (known vocab)
beat every cleverer approach, including 32B. **Open-vocab clustering line CLOSED.** The proven
open-vocab path forward is a known/constrained vocabulary, not unsupervised predicate clustering.
