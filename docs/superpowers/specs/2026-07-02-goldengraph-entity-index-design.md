# GoldenGraph Entity Index (ANN-Indexed Retrieval) — Design (SP1)

**Date:** 2026-07-02
**Status:** design, pre-implementation
**Sub-project:** SP1 of the STaRK-scale retrieval prerequisite (SP2 = `StoreBatch` bulk-loader + STaRK-AMAZON feasibility run). Prerequisite to test goldengraph on a structure-load-bearing benchmark (rebut the MuSiQue "graph inert" finding).

## Problem

`seed_by_query` (`goldengraph/embed.py:66`) — the query→seed-entity retrieval step — **re-embeds ALL entity canonical-names on EVERY query** (`embedder.embed([query] + names)`, then a brute-force cosine). That is O(N) *embedding calls* per query over the network/model. At STaRK-AMAZON scale (~1M+ nodes) a single query re-embeds a million names → minutes-to-hours per query. Node embeddings are never persisted. This is the hard blocker for any at-scale retrieval; the Rust store + BFS neighborhood already scale.

## Insight / reuse

The catastrophe is the per-query *embedding*, not the O(N) dot-products. The repo already has a FAISS index: `goldenmatch/core/ann_blocker.py::ANNBlocker` (`build_index(embeddings)`, `query_one(vec) -> [(row, score)]`, numpy all-pairs fallback when faiss absent). So SP1 is a **reuse + re-seam**: embed each node's name **once**, build an `ANNBlocker` over those vectors, and make the per-query path query the prebuilt index instead of re-embedding. `ANNBlocker` uses `IndexFlatIP` (exact flat, O(N) SIMD dot-products/query — ~ms at 1M, fine for the spike; `IndexHNSWFlat` is a later one-line swap if latency demands).

## Non-goals

- No approximate (HNSW/IVF) index tuning — flat FAISS for the spike; index-type is a measured SP2 finding.
- No bulk-KB-loader, no STaRK adapter, no Modal run (SP2).
- No change to the Rust store (embeddings live in a **sidecar**, keyed by `entity_id` — the store is a JSON-boundary graph; vectors never cross it).
- No change to `ask()`'s existing default behavior — the indexed path is opt-in via a passed index.

## Architecture

New module `goldengraph/entity_index.py`:

### `EntityIndex`
```python
class EntityIndex:
    """A persisted ANN index over entity canonical-name embeddings, keyed by entity_id. Built ONCE
    (embed all names in batches), queried per-request without re-embedding the corpus. Wraps
    goldenmatch's ANNBlocker (FAISS IndexFlatIP + numpy fallback). EntityIndex OWNS the L2-normalized
    embedding array (`self._corpus`) + `self._row_to_entity_id`; ANNBlocker is a rebuildable view over
    that array, so nothing reaches into ANNBlocker's private state."""

    @classmethod
    def build(cls, entities, embedder, *, top_k=50) -> "EntityIndex":
        # entities: iterable of {"entity_id", "canonical_name", "typ", ...} (e.g. slice_graph.entities()).
        # Filter to real entity nodes (typ not startswith "literal:", non-empty name) -- same rule as
        # seed_by_query. Embed all names ONCE (batched via embedder.embed), L2-normalize -> self._corpus.
        # Keep row_to_entity_id: list[int] parallel to the corpus rows. Construct ANNBlocker(top_k=top_k)
        # and build_index(self._corpus). `top_k` is the index CAPACITY (max neighbors any query can ask).

    def query(self, query: str, embedder, *, k=5) -> list[int]:
        # embed the QUERY only (one call), ANNBlocker.query_one -> [(row, score)], map rows->entity_ids,
        # take the top k. REQUIRES k <= top_k (query_one caps at the construction-time top_k); raise
        # ValueError if k > self._top_k rather than silently truncate.

    def save(self, path) -> None:
        # Backend-agnostic (identical on faiss + numpy): np.save the corpus + row_to_entity_id, write a
        # meta.json (dim, top_k). NO faiss.write_index (that would reach into ANNBlocker._index).
    @classmethod
    def load(cls, path) -> "EntityIndex":
        # np.load corpus + row_to_entity_id, read meta, ANNBlocker(top_k).build_index(corpus).
    def __len__(self) -> int: ...
```
- **Filtering** mirrors `seed_by_query`: skip `typ` starting `literal:` and empty/whitespace names (embedding a bare value 400s the provider batch — the exact bug seed_by_query documents).
- **Normalization:** L2-normalize the corpus (in `build`) and the query (in `query`) so `IndexFlatIP` inner product == cosine (`ANNBlocker` does NOT normalize internally; the numpy fallback ranks by raw IP, so normalized inputs make both paths return cosine ranking identically).
- **`k <= top_k` contract:** `ANNBlocker.query_one` returns at most the construction-time `top_k`; `query` validates `k <= self._top_k` and raises `ValueError` otherwise (no silent truncation). `build` defaults `top_k=50` (ample retrieval capacity); raise it for larger-recall runs.
- **`EntityIndex` owns the array:** `save`/`load` operate on `self._corpus` (the normalized embeddings `EntityIndex` computed) — backend-agnostic, no `faiss.write_index`, no private-attr access. `load` rebuilds the `ANNBlocker` from the saved corpus. This is why SP2 can skip re-embedding 1M names every run.

### Retrieval seam
`seed_by_query(slice_graph, query, embedder, *, k=5, index=None)` gains an optional `index`:
- `index is not None` → return `index.query(query, embedder, k=k)` (NO corpus re-embed).
- `index is None` → the current re-embed path, unchanged (fine for small graphs / back-compat).

`ask(..., entity_index=None)` threads it: passes `index=entity_index` to `seed_by_query`. Default `None` → today's behavior. The STaRK harness (SP2) builds the `EntityIndex` once and passes it to every `ask`.

## Testing (TDD, box-safe — numpy fallback, stub embedder)

`packages/python/goldengraph/tests/test_entity_index.py` (no faiss needed — `ANNBlocker` falls back to numpy; a `_StubEmbedder` returns deterministic vectors):
- `build_and_query_topk` — 4 entities with distinct stub vectors; `query` returns the nearest entity_ids in order.
- `query_maps_rows_to_entity_ids` — non-contiguous entity_ids (e.g. 5, 1, 99) → `query` returns entity_ids, not row indices.
- `build_filters_literals_and_empty` — a `literal:`-typed node and an empty-name node are excluded from the index (mirrors seed_by_query).
- `query_embeds_query_only` — a counting stub embedder: after `build`, each `query` makes exactly ONE `embed()` call (the query), NOT N (the anti-regression for the whole sub-project). Count ONLY query-phase calls — `build` may make >1 `embed()` call (the embedder batches at `_MAX_EMBED_BATCH`), so don't assert a total across build+query.
- `query_rejects_k_above_capacity` — `query(k=100)` on an index built with `top_k=50` raises `ValueError` (no silent truncation).
- `save_load_roundtrip` — `build` → `save` → `load` → `query` returns the same top-k (numpy-backend path; the faiss path is exercised in SP2/CI where faiss is installed).
- `empty_index_returns_empty` — no eligible entities → `query` returns `[]`.
- `seed_by_query_uses_index_when_given` — `seed_by_query(graph, q, emb, index=idx)` calls `idx.query` and does NOT re-embed the graph (counting stub confirms one embed call); `index=None` preserves the current path.

## Design choices flagged for review

- **Reuse `ANNBlocker`, don't add hnswlib.** The FAISS `IndexFlatIP` + numpy fallback already exists; box tests run on the fallback. Flat-vs-approximate is a measured SP2 call, not a design commitment.
- **Sidecar, keyed by entity_id.** Embeddings never enter the Rust store's JSON boundary (millions × 768-dim would be absurd). The index is a separate artifact.
- **`query` embeds the query only.** This single property is the entire point — the `query_embeds_query_only` test is the load-bearing regression guard.
- **Cross-package dep:** `goldengraph` importing `goldenmatch.core.ann_blocker`. goldengraph already depends on goldenmatch (the resolver). Acceptable; if the import is heavy, import `ANNBlocker` lazily inside `EntityIndex.build`.

## Follow-ons (SP2)

- `StoreBatch` bulk-loader (map a pre-structured KB's nodes+edges → `StoreBatch` → `store.append`, bypassing extraction).
- STaRK-AMAZON adapter + Modal feasibility run: ingest time, `EntityIndex.build` time, per-query latency, RAM; the flat-vs-HNSW verdict.
