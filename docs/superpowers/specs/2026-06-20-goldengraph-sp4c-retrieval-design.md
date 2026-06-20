# goldengraph SP4c — retrieval + synthesis + query (Python) — design

**Status:** Design draft 2026-06-20. SP4c slice of SP4 (`2026-06-20-goldengraph-sp4-host-pipeline-design.md`). Awaiting approval → plan. **Depends on SP4b** (the `goldengraph` Python package + `ingest`) **and SP4a** (`PyStore`/`PyGraph.communities`), both merged/queued.

**Surface:** extends the standalone `goldengraph` Python package. **The "ask the KG" path** — the second half of the standalone milestone (SP4b builds the graph; SP4c queries it).

---

## Motivation

SP4b turns text into a durable graph. SP4c answers questions against it: find the relevant entities (embedding-seeded), pull their neighborhood (SP1 retrieval over an SP2 `as_of` slice), and synthesize an answer (LLM) — local (a subgraph) or global (map-reduce over SP3 community summaries, GraphRAG's two query modes). Plus a text-to-Cypher export for users who persist to Neo4j. Resolution-merged entities mean a query about "Apple Inc." finds facts attached to "Apple"/"Apple Computer" too — the differentiator, now end-to-end.

## Prerequisite — a `PyGraph.entities()` accessor (small SP4a-binding change)

Embedding-seeded retrieval must read **every** entity's `canonical_name`, but the SP4a binding exposes no entity enumeration — `PyGraph` has only `query(seeds, hops)` (needs seeds you don't have yet), `seeds_by_name`, and `communities()`. So SP4c first adds **`PyGraph.entities() -> list[dict]`** to `goldengraph-native` (a ~10-line accessor mirroring the existing `graph_view_to_dict` entity projection: `[{entity_id, canonical_name, typ, surface_names, members}]`). This is an explicit SP4c prerequisite (like SP5a's serde-derive prereq) — without it `seed_by_query` has nothing to embed. (Reconstructing the entity set via `communities()` → `query(members, hops)` was rejected: it drops isolated/edgeless entities.)

## Modules (extend the `goldengraph` package)

### `embed.py` — embedder boundary + embedding-seeded retrieval
```
import numpy as np
class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray: ...   # shape (len(texts), dim)

def seed_by_query(slice_graph, query, embedder, *, k=5) -> list[int]   # top-k entity ids
```
- Provider-agnostic `Embedder` protocol (mirrors SP4b's `LLMClient`), returning `np.ndarray` to match goldenmatch (cosine is numpy anyway — no per-call `.tolist()`). The default adapter wraps a goldenmatch embedding **provider** (`goldenmatch.embeddings.providers` / `_ProviderEmbedder.embed(texts) -> np.ndarray`, imported lazily — NOT the bare `goldenmatch.core.embedder.Embedder` class, whose surface is `embed_column`/`cosine_similarity_matrix`, not `.embed(texts)`). Tests inject a deterministic stub.
- `seed_by_query` takes the **`PyGraph` returned by `as_of(v,t)`** (resolves the circular ordering: entity ids are slice-specific, so seeds must be valid on the same slice they query). It reads `slice_graph.entities()`, embeds the query + each `canonical_name`, cosine-ranks, and returns the top-`k` entity ids. **Tie-break: deterministic secondary sort by `entity_id`** (one-hot/zero stub vectors tie often — without this the top-k flakes). **Decision — recompute per query** (embed all entities each call): correctness-first and simple; a **persisted embedding sidecar** (keyed by entity id, alongside the snapshot — the SP2 store deliberately holds no vectors) + an ANN index is the scale optimization, noted not built. Cosine in numpy.

### `synthesize.py` — LLM answer assembly (reuse `LLMClient` + `BudgetTracker`)
```
def synthesize_local(query, subgraph, llm) -> str          # subgraph -> answer
def synthesize_global(query, community_views, llm) -> str  # map-reduce over community summaries
```
- **Local:** seed → `store.as_of(v,t).query(seeds, hops)` subgraph → LLM prompt (the subgraph's entities + edges) → answer.
- **Global:** the `as_of` slice's `.communities()` → per-community subgraph (`slice.query(members, hops)`) → LLM summary each (map) → LLM combine summaries to answer the query (reduce). The GraphRAG global mode, on SP3's communities. **Cost:** this is N+1 LLM calls; `BudgetTracker` is reactive (it enforces `max_cost_usd`/`max_calls` via `record_usage` AFTER each call), so the plan must both `record_usage` per map call AND pre-emptively cap the community count — the tracker alone can't stop the fan-out before the calls fire.

### `answer.py` — NL query orchestration + text-to-Cypher
```
def ask(query, store, *, valid_t, tx_t, llm, embedder, mode="local", k=5, hops=1) -> str
def to_cypher(query, llm, *, schema_hint=None) -> str       # emit Cypher string (NOT executed)
```
- `ask(mode="local")` flow (ordering pinned): `slice = store.as_of(v,t)` → `seeds = seed_by_query(slice, query, embedder, k)` → `sub = slice.query(seeds, hops)` → `synthesize_local(query, sub, llm)`. `mode="global"` = `slice.communities()` → per-community `slice.query(members, hops)` → map-reduce. The native-store path is primary; seeds are always taken on the same `as_of` slice they query.
- `to_cypher`: a thin LLM adapter emitting a Cypher string for Neo4j users; **returns the string, does not execute** (no Neo4j dep — the caller runs it). The native NL query is primary; Cypher is an export convenience.

## Determinism + testing

- **Stub embedder + stub LLM (deterministic):** a stub `Embedder` returns fixed vectors (e.g. one-hot per known name) so `seed_by_query` is deterministic; a stub `LLMClient` echoes a canned answer. Build a small graph via SP4b `ingest` (injected resolver) → `ask(...)` → assert the right seeds were chosen + the synthesis prompt saw the expected subgraph (assert on a recording stub's captured prompt, not on free-form LLM text). Verifies the **retrieval + assembly wiring**, not embedding/LLM accuracy.
- **`to_cypher`** test: stub LLM returns a fixed Cypher string; assert it's returned verbatim (no execution).
- **Real embedder/LLM:** opt-in lane (skipped without creds), as in SP4b. Accuracy is SP6's eval.

## CI

Extends the existing **`goldengraph-pipeline.yml`** lane (no new lane): the new `embed`/`synthesize`/`answer` tests run with the same install (goldenmatch + the native engine wheel). Informational; confirm green before arming.

## Non-goals (SP4c)

Persisted embedding sidecar / ANN index (the recompute-per-query optimization — measure first). Reranking / cross-encoder boosting. Neo4j execution (caller's). Real embedder/LLM accuracy guarantees (SP6). Streaming answers. The WASM/C surfaces (SP5). Multi-hop reasoning beyond `hops` neighborhoods.

## Risks / open questions (resolve in the plan)

- **Recompute-per-query embedding cost** is O(entities) embeds per query — fine for moderate KGs / SP4c correctness; the persisted sidecar is the escape hatch (same "measure first" discipline as the core's perf-audit lesson).
- **Embedder availability:** goldenmatch's embedder may need a model/provider; the stub keeps tests hermetic, and the default adapter imports it lazily so the package loads without it.
- **Global-search LLM cost:** map-reduce over communities is N+1 LLM calls; gate with `BudgetTracker` and cap community count in the plan.
- **`as_of` defaults:** `ask` takes explicit `valid_t`/`tx_t`; pick sensible defaults (latest known tx, open valid) in the plan so the common case is one arg.
