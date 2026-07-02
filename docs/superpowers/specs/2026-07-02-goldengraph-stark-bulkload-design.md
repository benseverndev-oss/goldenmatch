# STaRK bulk-load + feasibility spike (SP2) — design

**Program:** STaRK-scale retrieval. SP1 shipped `EntityIndex` (embed-once ANN
retrieval reusing goldenmatch `ANNBlocker`; PR #1395, merged). SP2 answers one
question: **does goldengraph's ingest + retrieve path run at STaRK scale, and
what are the numbers?** (ingest time, `EntityIndex.build` time, per-query
latency, peak RAM, retrieval Hit@1 / Hit@5 / Recall@20 / MRR).

This is a **feasibility spike**: minimal build to get honest numbers, not a
leaderboard entry. The ER moat is deliberately NOT exercised here — vanilla
STaRK entities are pre-resolved with canonical ids, so this proves "structure
loads + retrieves at scale," not "our ER beats ad-hoc dedup." Alias-injected
STaRK (the moat experiment) is a later sub-project.

## Decisions locked in brainstorming

- **First target: STaRK-PRIME** (~130K nodes, biomedical — the smallest STaRK
  KB). Fail-fast/cheap: validate loader + adapter + metrics + RAM end-to-end in
  minutes, then run the SAME code on AMAZON (~1M) only if PRIME is clean.
- **Single-batch load, measure.** One `StoreBatch` → one `append` (O(N)). If the
  giant `json.dumps` OOMs, THAT is the finding and it motivates the `chunk_edges`
  fallback. The chunk knob is built but off by default.
- **Skip the resolver — direct id passthrough.** STaRK's canonical node ids ARE
  the resolution. Each node gets `record_keys=[stark_id]` (globally unique), so
  within one batch there is zero key-overlap → every node mints a fresh stable
  id, no merges, no splits. Clean passthrough.
- **Reuse STaRK data loading, reimplement the 4 metrics.** Their file format is
  the fiddly part (reuse it); Hit@k / Recall@20 / MRR are ~30 trivial lines over
  `(ranked_ids, gold_ids)` with no dependency or retriever-API coupling.

## Load-bearing store constraints (from `goldengraph-core/src/store.rs`)

These drove the decisions above and constrain the loader:

1. **Edges must be co-batched with their endpoints.** `append` remaps each edge
   via `local_to_stable[subj_local]` and **panics** if an endpoint is absent from
   the same batch. There is no cross-batch edge. A chunked load must re-list an
   edge's endpoint entities in that edge's batch.
2. **`append` is O(stored) per call** — it rebuilds `key_to_stored` over every
   currently-stored entity. So N small batches ≈ O(N × batches) (quadratic); one
   batch is O(N). Single-batch is strictly cheapest on append cost; its only cost
   is peak JSON-string memory (the thing the spike measures).
3. **Unique record_key ⇒ passthrough.** With `record_keys=[stark_id]` and all ids
   distinct, `overlaps` is empty for every batch entity, so all `assigned[i]`
   mint fresh ids in `sorted_keys` order. No `HistoryEvent` is emitted. Verified
   against the plurality-heir algorithm in `store.rs::append`.

## Components

### 1. `bulk_load` — `packages/python/goldengraph/goldengraph/bulk.py` (new)

```python
def bulk_load(store, nodes, edges, *, at: int = 1, chunk_edges: int | None = None) -> dict:
    """Load a PRE-STRUCTURED KB straight into `store`, bypassing extract/resolve/link.

    `nodes`: iterable of (stark_id: str, name: str, typ: str).
    `edges`: iterable of (subj_stark_id: str, predicate: str, obj_stark_id: str).

    Each node -> BatchEntity(local_id=positional index, canonical_name=name,
    typ=typ, surface_names=[name], record_keys=[stark_id], source_refs=[]).
    Each edge -> BatchEdge(subj_local, predicate, obj_local, valid_from=at,
    valid_to=None, source_refs=[]). Edges whose endpoint stark_id is unknown are
    dropped (counted). Returns {"n_nodes", "n_edges", "n_dropped_edges", "n_batches"}.

    Default single batch (O(N) append). `chunk_edges=C` splits edges into batches
    of <=C edges, each re-listing ONLY its endpoint entities (so the store's
    local->stable remap resolves them to their already-minted ids via the unique
    record_key). The fallback for when the single-batch JSON OOMs -- off by default."""
```

Responsibility: the node/edge → `StoreBatch` mapping and the `store.append`
call(s). Nothing STaRK-specific and nothing about retrieval. Pure w.r.t. the
data source — testable with a tiny in-memory `nodes`/`edges` list against a stub
store.

`local_id` is the node's positional index (`u32`; ~1M fits). A single
`stark_id -> local_id` dict is built once so edges remap in O(1). In the chunked
path, an edge-batch's endpoint entities carry the same `record_keys=[stark_id]`,
so `append`'s overlap-merge re-resolves them to the id minted in the node batch
(single inheritor → no merge, no new mint).

### 2. STaRK adapter — `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/stark_adapter.py` (new)

- `load_stark_kb(name)` → `(nodes, edges, queries)`. Downloads
  `snap-stanford/stark` from HuggingFace (the `stark-qa` package / HF datasets),
  maps its `(node_id, node_type, name|title, text)` and `(src, relation, dst)`
  schema to the loader's `(stark_id, name, typ)` / `(subj, predicate, obj)`
  tuples, and loads the eval split's `(query_text, gold_node_ids)` pairs.
- `metrics(ranked_ids, gold_ids)` → `{hit@1, hit@5, recall@20, mrr}`.
  Reimplemented, standard definitions:
  - Hit@k = 1 if any gold id in the top-k, else 0 (mean over queries).
  - Recall@20 = |gold ∩ top-20| / |gold|.
  - MRR = 1 / rank of the first gold id (0 if none in the ranking).
- `evaluate(index, store, queries, embedder, *, arm)` runs one retrieval arm over
  the query set and returns the mean metric dict + timing.

Node ids in STaRK are integers already; the adapter stringifies them for
`record_keys` and keeps the int for `EntityIndex.entity_id` so `query()` returns
ints directly comparable to gold.

### 3. Retrieval arms + measurement — driven from the adapter / a Modal entry

Build `EntityIndex` with `entity_id = the STaRK node id` and
`canonical_name = node name` (so `query()` returns STaRK ids, sidestepping the
`as_of` view-local id remap). Two arms:

- **Arm A — dense baseline:** `EntityIndex.query(q, k=20)`. Pure vector
  retrieval; the graph contributes nothing. This is the "vectors alone" number.
- **Arm B — graph-expanded:** seeds from `EntityIndex` (k small, e.g. 5) then
  1-hop neighborhood expansion via `ask(entity_index=index)` /
  `store.as_of(∞,∞).query(seeds, 1)`, ranking seeds + their neighbors. This is
  the graph's value-add — answers reachable by a relation but not textually near
  the query.

Captured per run: ingest wall, `EntityIndex.build` wall, mean/95p per-query
latency, peak RSS (via `resource`/`tracemalloc` or a Modal memory sample), and
the A-vs-B metric table. Flat (`IndexFlatIP`) is the default; HNSW
(`IndexHNSWFlat`, a 1-line `ANNBlocker` swap) is a follow-up knob ONLY if flat
per-query latency is measured too slow.

### 4. Modal feasibility entry

A `scripts/distill/modal_*` entry (or an extension of the existing
`modal_bench.py`) that: downloads the KB on Modal's box, runs `bulk_load`, builds
the index, runs both arms over a query sample, and prints the numbers table.
`--detach --spawn` + poll; creds from Infisical dev project (never in literals).
PRIME first; AMAZON is the same entry with `--kb amazon` once PRIME is clean.

## Data flow

```
STaRK HF download
  -> load_stark_kb(name) -> (nodes, edges, queries)
  -> bulk_load(store, nodes, edges)         # StoreBatch -> store.append  [MEASURE ingest, RAM]
  -> EntityIndex.build(nodes-as-entities)   # embed names once            [MEASURE build, RAM]
  -> for q in queries:
       Arm A: index.query(q, k=20)                                        [MEASURE latency]
       Arm B: seeds=index.query(q,k=5); expand via store.as_of.query(seeds,1)
  -> metrics(ranked, gold) per arm -> numbers table
```

## Error handling / honest-null posture

- **OOM on single-batch append** (giant JSON): the expected first-run risk. Catch
  it as a FINDING, report the node/edge count at which it broke, then re-run with
  `chunk_edges`. Do not silently fall back — the ceiling is the result.
- **Edge endpoint unknown** (dangling STaRK edge): drop + count, never crash.
- **Empty / literal node names**: `EntityIndex.build` already filters these
  (mirrors `seed_by_query`); the loader still stores them as nodes (they can be
  edge endpoints), they are just not index-able seeds.
- **Metric on zero gold**: Recall@20 undefined → skip that query from the Recall
  mean (count skipped), never divide by zero.

## Testing (box-safe, TDD)

`packages/python/goldengraph/tests/test_bulk_load.py` (numpy/stub only, no HF,
no Modal):
- `bulk_load` single-batch: N nodes → N stored entities, each with its stark_id
  as a record_key; edges remapped to the right stable-id pairs.
- passthrough: distinct record_keys ⇒ zero `HistoryEvent` (no merges/splits).
- dangling edge dropped + counted; return dict shape.
- `chunk_edges=C`: same final store state as single-batch (endpoint re-listing
  resolves to the same ids) — the parity guard for the fallback path.
- edge co-batching: a chunk with an edge whose endpoint is re-listed does NOT
  panic and lands the edge.

`erkgbench/tests/test_stark_metrics.py`:
- Hit@1/Hit@5/Recall@20/MRR against hand-worked rankings (gold in position 1, in
  top-5 not top-1, absent; multi-gold recall; zero-gold skip).

Adapter HF download + Modal run are integration-only (not in the box suite).

Box-safe runner (worktree goldenmatch shadow, per SP1):
`cd packages/python/goldengraph; PYTHONPATH=/d/show_case/gg-local-llm/packages/python/goldenmatch POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_bulk_load.py -q`

## Out of scope (YAGNI / deferred)

- Alias-injected STaRK (the ER moat experiment) — separate sub-project.
- A native Rust bulk-append that bypasses reconciliation — only if the
  Python single-batch + chunked paths BOTH prove infeasible at AMAZON scale
  (measured, not assumed).
- HNSW — a measured follow-up knob, not built up front.
- MAG (~1.9M) — not a de-risking target; runnable later via the same entry.
