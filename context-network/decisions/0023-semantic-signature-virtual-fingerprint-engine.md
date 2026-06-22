# 0023 — Semantic Signature / Virtual Fingerprint engine (goldenprofile)

**Status:** accepted • **Shipped:** PR #TBD (2026-06-22)

## Context
GoldenGraph builds a knowledge graph as `text → LLM extraction → goldenmatch
entity resolution → durable bi-temporal store`. On multi-hop QA corpora
(MuSiQue-style), the graph *shatters*: the same real-world entity, described by
DISJOINT neighborhoods across documents, fails to link, so the multi-hop path
from question to answer is never physically present in the graph. Two failure
modes pull against each other:

- **Under-merge (Row 3).** Doc A: "Nabbes wrote Play X". Doc B: "Nabbes born
  1605". Raw-neighborhood / raw-text comparison sees ~0% overlap → no merge →
  shatter.
- **Over-merge (Row 4).** Dense embeddings of raw text blur "Nabbes" and
  "Shakespeare" (both "17th-century playwright") → spurious merge.

Tuning a single similarity threshold cannot escape this tension: the evidence
that disambiguates (name, era) and the evidence that diverges across documents
(the per-document fact) live in the same undifferentiated text blob.

`goldengraph/embed.py` already flagged the adjacent gap: retrieval re-embeds
every entity per query and brute-force cosines — "a persisted embedding sidecar
+ ANN index is the scale optimization, not built."

## Decision
Add an **entity-profiling / Virtual Fingerprint** engine: synthesize a rigid,
standardized fingerprint for every graph element (node AND edge) from its local
neighborhood, then resolve cross-document by comparing fingerprints instead of
raw text. The fingerprint is a brutally rigid 4-part pipe-delimited string —
free-text fingerprints collapse straight back into the Row-4 over-merge:

```
<name> | <category> | <temporal/spatial anchor> | <defining attribute>
```

Structuring the evidence is what breaks the tension:
- the **defining attribute** is EXPECTED to diverge across documents, so it can
  only ADD confidence, never veto a merge → kills Row-3 under-merge;
- the **name + category** are the stable identity, gated hard before any merge →
  kills Row-4 over-merge regardless of embedding proximity;
- **anchor** and the **embedding cosine** of the rendered fingerprint are soft,
  tunable signals, and a MISSING (UNKNOWN) field contributes a neutral prior,
  never a penalty (a second Row-3 guard: absent ≠ contradicting).

### Architecture — reuse the kernels, add the orchestration
The novel layer is the orchestration + the anti-shatter scorer. Every *signal*
is reused from an existing shared core, so the engine is byte-identical with the
surfaces that already ship those kernels:

- **SimHash band hashing** over the host-supplied dense fingerprint embedding
  (`sketch-core::simhash`) — the **semantic signature** / cosine-LSH blocker.
  This is the "embedding ANN index" `embed.py` wanted, generalized to blocking.
- **Field scorers** (`score-core`: jaro_winkler / token_sort) for name/category.
- **Canonical hashing** (`fingerprint-core`) for the structured block key.
- **Connected components** (`graph-core`) to cluster kept pairs.

Pipeline: `block (structured token keys ∪ semantic SimHash bands) → score
(anti-shatter fusion) → WCC cluster`. Mirrors `goldengraph-core`'s
block→score→cluster shape.

### Surfaces — all four, one core (the established pattern)
- `goldenprofile-core` — pyo3-free engine + the single `resolve_json` boundary.
- `goldenprofile-native` — pyo3/maturin abi3 wheel (`resolve_json` str→str).
- `goldenprofile-wasm` — wasm-bindgen wrapper exposing the pyo3-free
  `resolve_json_impl` (also linked by the C ABI; wasm-bindgen cfg-gated to
  wasm32).
- `goldenprofile-cabi` — C ABI reusing `resolve_json_impl` (no re-implementation).

LLM synthesis and embedding stay in the Python host (`goldengraph/profile.py`),
exactly as `goldengraph-core` keeps the LLM out of the engine. The engine is
LLM-free, embedding-model-free, and Arrow-free; the host supplies precomputed
fingerprint embeddings as the semantic signature.

### One JSON boundary
All bindings marshal the SAME `goldenprofile_core::resolve_json(&str) -> String`.
Request: `{profiles:[{kind,name,category,anchor,attribute}], embeddings?:[[f64]],
config?:{...partial overrides...}}`. Response: `{clusters:[[usize]],
edges:[{a,b,score:{...breakdown...}}]}`. The per-pair score breakdown is kept
(not just the scalar) for the never-black-box North Star — a host can show
exactly WHY two fingerprints merged.

## Consequences / tradeoffs
- **Lexical-only is conservative on synonym categories.** Without embeddings,
  "Playwright" vs "Dramatist" does not merge (category gate is lexical). The
  semantic signature bridges it: a strong embedding cosine satisfies the category
  gate (`category_embedding_gate`). Documented and tested both ways.
- **Edge fingerprints are deterministic in v1** (`predicate | predicate | UNKNOWN
  | subj -> obj`); predicate-synonym merging ("WROTE"/"PENNED") rides on the
  semantic signature. LLM-synthesized edge fingerprints are a future enhancement.
- **No pure-Python fallback resolver** (unlike sketch-core). `goldengraph`
  already requires its native engine; `profile.py` requires `goldenprofile_native`
  the same way (lazy import, actionable error). The Rust core owns correctness;
  the new host logic (LLM synthesis + defensive parsing) is what `test_profile.py`
  covers without the wheel.
- **Persistence (sidecar index) is deferred.** v1 resolves a batch in memory.
  A persisted signature index alongside the SP2 store snapshot is the next slice
  (the `embed.py` "persisted sidecar" note); the JSON boundary doesn't preclude it.

## Validation
- Rust: 25 core unit tests incl. the headline `musique_shatter_repair_three_docs`
  (disjoint Nabbes mentions reunite, Shakespeare stays distinct), the Row-4
  over-merge veto, the embedding-bridged synonym category, and the unknown-field
  neutrality. wasm/cabi parity tests assert identical bytes to the core boundary.
- Python: `test_profile.py` covers synthesis parsing + defensive fallbacks with a
  stub LLM, and a skip-if-absent end-to-end integration test through the wheel.

## Future
Persisted sidecar signature index in the SP2 store; LLM edge-fingerprint
synthesis; gate `goldenprofile` into the goldengraph ingest path as an optional
resolution strategy; ER-KG-Bench / MuSiQue eval to quantify the shatter repair.
