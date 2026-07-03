# STaRK bulk-load + feasibility spike (SP2) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Load a pre-structured STaRK KB straight into the goldengraph store (no LLM extraction), retrieve over it two ways (dense vs graph-expanded), and produce honest at-scale numbers (ingest/index time, per-query latency, RAM, Hit@k/Recall@20/MRR).

**Architecture:** A pure `bulk_load` maps `(nodes, edges)` to a `StoreBatch` dict and calls `store.append` (single batch by default; chunked fallback for OOM). A STaRK adapter downloads the KB from HuggingFace and reimplements the 4 IR metrics. Retrieval runs over ONE `as_of` slice in view-local id space; a `source_refs`-carried `eid_to_stark` map translates results back to stark ids for scoring. A Modal entry runs the whole thing on a big box, PRIME first.

**Tech Stack:** Python 3.12, `goldengraph_native.PyStore` (real store — its append/as_of semantics are under test), `goldengraph.entity_index.EntityIndex` (SP1, on main), numpy, HuggingFace `datasets`/`stark-qa` (adapter only), Modal (run only).

**Spec:** `docs/superpowers/specs/2026-07-02-goldengraph-stark-bulkload-design.md`

---

## Ground truth confirmed before planning

- `goldengraph_native.PyStore` LOADS on the box (methods `append`, `as_of`, `history`, `snapshot`) — so `bulk_load` tests use a REAL store, not a stub.
- `store.append(json_str)` takes a JSON `StoreBatch`; `store.as_of(valid_t, tx_t)` returns a `PyGraph` with `.entities()` (dicts: `entity_id`, `canonical_name`, `typ`, `members`, `surface_names`, `source_refs`) and `.query(ids, hops)` (returns `{"entities":[...], "edges":[{subj,predicate,obj,source_refs}]}`).
- `store.snapshot()` returns canonical JSON with an `entities`/`edges`/`history`/`next_id` shape — `json.loads(snap)["history"]` is the merge/split event list (empty ⇒ pure passthrough).
- StoreBatch dict shape (from `ingest.py::build_batch`): top-level `entities`/`edges`/`ingested_at`; entity `local_id`/`canonical_name`/`typ`/`surface_names`/`record_keys`/`source_refs`; edge `subj_local`/`predicate`/`obj_local`/`valid_from`/`valid_to`/`source_refs`.
- `EntityIndex.build(entities, embedder, *, top_k=50)` filters `literal:`-typed + empty-name rows; `query(query, embedder, *, k)` needs `k <= top_k`.

## File structure

- **Create** `packages/python/goldengraph/goldengraph/bulk.py` — the `bulk_load` mapper. One responsibility: `(nodes, edges)` → `StoreBatch` dict(s) → `store.append`. No STaRK, no HF, no retrieval.
- **Modify** `packages/python/goldengraph/goldengraph/__init__.py` — export `bulk_load`.
- **Create** `packages/python/goldengraph/tests/test_bulk_load.py` — real-`PyStore` TDD for the mapper + store semantics.
- **Create** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/stark_metrics.py` — the 4 pure IR metrics + Arm-B dedup helper.
- **Create** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/tests/test_stark_metrics.py` — pure box-safe metric tests.
- **Create** `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/stark_adapter.py` — HF loader + `evaluate` arm runner (integration-only, not in box suite).
- **Create** `scripts/distill/modal_stark.py` — the Modal feasibility entry (run-only).

## Box-safe test runners

goldengraph mapper tests (real PyStore, worktree goldenmatch shadow):
```
cd packages/python/goldengraph
PYTHONPATH=/d/show_case/gg-local-llm/packages/python/goldenmatch POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_bulk_load.py -q
```
metrics tests (pure python):
```
cd packages/python/goldenmatch/benchmarks/er-kg-bench
PYTHONPATH="$PWD:/d/show_case/gg-local-llm/packages/python/goldengraph" POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_stark_metrics.py -q
```

Reference skills: @superpowers:test-driven-development, @superpowers:subagent-driven-development. Auth/commit rules: benzsevern account (`unset GH_TOKEN` before push), squash-merge SOP, arm auto-merge and STOP.

---

## Task 1: `bulk_load` — nodes/edges → StoreBatch → store.append (single batch)

**Files:**
- Create: `packages/python/goldengraph/goldengraph/bulk.py`
- Test: `packages/python/goldengraph/tests/test_bulk_load.py`

- [ ] **Step 1: Write the failing test — single-batch load stores every node with its stark_id as a record_key**

```python
"""SP2 bulk_load: pre-structured KB -> StoreBatch -> real PyStore. Exercises the
store's append/as_of semantics directly (that IS what is under test), so it needs
goldengraph_native; skip cleanly if the wheel is absent."""
from __future__ import annotations

import json

import pytest

ggn = pytest.importorskip("goldengraph_native")
from goldengraph.bulk import bulk_load  # noqa: E402

_BIG = 1 << 62


def _store():
    from goldengraph_native import _native as gg
    return gg.PyStore()


# stark ids are strings; names/types are plain; one triangle A-B, A-C
_NODES = [("s10", "Alice", "person"), ("s20", "Acme", "org"), ("s30", "Bob", "person")]
_EDGES = [("s10", "works_at", "s20"), ("s10", "knows", "s30")]


def test_single_batch_stores_all_nodes_with_stark_record_keys():
    store = _store()
    out = bulk_load(store, _NODES, _EDGES)
    assert out == {"n_nodes": 3, "n_edges": 2, "n_dropped_edges": 0, "n_batches": 1}
    snap = json.loads(store.snapshot())
    # every node present, keyed by its stark id
    keys = {tuple(e["record_keys"]) for e in snap["entities"].values()}
    assert keys == {("s10",), ("s20",), ("s30",)}
```

- [ ] **Step 2: Run test to verify it fails**

Run the goldengraph box-safe runner above.
Expected: FAIL — `ModuleNotFoundError: No module named 'goldengraph.bulk'` (or import error).

- [ ] **Step 3: Write minimal implementation**

```python
"""SP2: load a PRE-STRUCTURED knowledge base straight into the store, bypassing
extract/resolve/link. STaRK-style KBs come with canonical node ids already, so
each node's id IS its resolution: a unique `record_key` per node means the store's
overlap-merge mints a fresh stable id for each with zero merges (see
goldengraph-core/src/store.rs::append). Edges are co-batched with their endpoints
because the store panics on an edge whose endpoint is absent from the same batch.
See docs/superpowers/specs/2026-07-02-goldengraph-stark-bulkload-design.md.
"""
from __future__ import annotations

import json
from collections.abc import Iterable


def _entity(local_id: int, stark_id: str, name: str, typ: str) -> dict:
    # record_keys=[stark_id] -> unique -> passthrough (no merge). source_refs=[stark_id]
    # -> the stark id rides through as_of so retrieval can translate view-local ids back.
    return {
        "local_id": local_id,
        "canonical_name": name,
        "typ": typ,
        "surface_names": [name],
        "record_keys": [stark_id],
        "source_refs": [stark_id],
    }


def _edge(subj_local: int, predicate: str, obj_local: int, at: int) -> dict:
    return {
        "subj_local": subj_local,
        "predicate": predicate,
        "obj_local": obj_local,
        "valid_from": at,
        "valid_to": None,
        "source_refs": [],
    }


def bulk_load(store, nodes: Iterable, edges: Iterable, *, at: int = 1, chunk_edges: int | None = None) -> dict:
    """Load `(nodes, edges)` into `store`. `nodes`: iterable of (stark_id, name, typ);
    `edges`: iterable of (subj_stark_id, predicate, obj_stark_id). Returns
    {n_nodes, n_edges, n_dropped_edges, n_batches}. See module docstring / spec."""
    node_list = list(nodes)
    id_to_local: dict[str, int] = {}
    entities: list[dict] = []
    for i, (stark_id, name, typ) in enumerate(node_list):
        id_to_local[str(stark_id)] = i
        entities.append(_entity(i, str(stark_id), name, typ))

    edge_dicts: list[dict] = []
    dropped = 0
    for subj, predicate, obj in edges:
        s = id_to_local.get(str(subj))
        o = id_to_local.get(str(obj))
        if s is None or o is None:
            dropped += 1
            continue
        edge_dicts.append(_edge(s, predicate, o, at))

    n_batches = _append_single(store, entities, edge_dicts, at)
    return {
        "n_nodes": len(entities),
        "n_edges": len(edge_dicts),
        "n_dropped_edges": dropped,
        "n_batches": n_batches,
    }


def _append_single(store, entities: list[dict], edges: list[dict], at: int) -> int:
    store.append(json.dumps({"entities": entities, "edges": edges, "ingested_at": at}))
    return 1
```

- [ ] **Step 4: Run test to verify it passes**

Run the goldengraph box-safe runner.
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /d/show_case/gg-local-llm && unset GH_TOKEN
git add packages/python/goldengraph/goldengraph/bulk.py packages/python/goldengraph/tests/test_bulk_load.py
git commit -m "feat(goldengraph): bulk_load nodes+edges -> StoreBatch (single batch)"
```

---

## Task 2: `bulk_load` — edges remap to correct stable-id pairs, passthrough, provenance

**Files:**
- Modify: `packages/python/goldengraph/tests/test_bulk_load.py`

- [ ] **Step 1: Write the failing tests — edges land on the right entities, zero history, stark_id rides through**

```python
def _by_name(slice_graph):
    return {e["canonical_name"]: e for e in slice_graph.entities()}


def test_edges_remap_to_right_entity_pairs():
    store = _store()
    bulk_load(store, _NODES, _EDGES)
    g = store.as_of(_BIG, _BIG)
    ents = _by_name(g)
    eid = {name: e["entity_id"] for name, e in ents.items()}
    edges = {(e["subj"], e["predicate"], e["obj"]) for e in g.query(list(eid.values()), 1)["edges"]}
    assert (eid["Alice"], "works_at", eid["Acme"]) in edges
    assert (eid["Alice"], "knows", eid["Bob"]) in edges


def test_passthrough_no_merges_or_splits():
    store = _store()
    bulk_load(store, _NODES, _EDGES)
    assert json.loads(store.snapshot())["history"] == []   # distinct keys -> zero HistoryEvent


def test_stark_id_rides_through_as_of():
    # eid_to_stark (the Arm-B translation map) must be recoverable from the slice.
    store = _store()
    bulk_load(store, _NODES, _EDGES)
    g = store.as_of(_BIG, _BIG)
    for e in g.entities():
        assert e["source_refs"] == [ {"Alice": "s10", "Acme": "s20", "Bob": "s30"}[e["canonical_name"]] ]


def test_dangling_edge_dropped_and_counted():
    store = _store()
    out = bulk_load(store, _NODES, [("s10", "works_at", "s99")])  # s99 unknown
    assert out["n_edges"] == 0 and out["n_dropped_edges"] == 1
    # no edge landed
    g = store.as_of(_BIG, _BIG)
    assert g.query([e["entity_id"] for e in g.entities()], 1)["edges"] == []
```

- [ ] **Step 2: Run to verify they fail (or pass) against Task 1's implementation**

Run the goldengraph box-safe runner.
Expected: these should PASS with the Task 1 implementation (this task is the semantics lock — if any FAIL, the implementation is wrong, fix it, don't weaken the test). Note `store.as_of` uses `_BIG` for BOTH axes so all facts/entities are visible.

- [ ] **Step 3: (only if a test failed) fix `bulk.py`**

No new code expected. If `test_stark_id_rides_through_as_of` fails, verify `_entity` sets `source_refs=[stark_id]`. If edges are missing, verify endpoints are co-batched (they are, single batch).

- [ ] **Step 4: Re-run — all green**

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/tests/test_bulk_load.py
git commit -m "test(goldengraph): lock bulk_load edge-remap, passthrough, provenance"
```

---

## Task 3: `bulk_load` — chunked-edges fallback with store-state parity

**Files:**
- Modify: `packages/python/goldengraph/goldengraph/bulk.py`
- Modify: `packages/python/goldengraph/tests/test_bulk_load.py`

- [ ] **Step 1: Write the failing tests — chunked load yields the SAME store state, edge co-batching does not panic**

```python
def _canonical_state(store):
    """Order-independent (entities, edges) view for parity comparison across batchings."""
    g = store.as_of(_BIG, _BIG)
    ents = {tuple(e["source_refs"]): (e["canonical_name"], e["typ"]) for e in g.entities()}
    eid_to_stark = {e["entity_id"]: e["source_refs"][0] for e in g.entities()}
    edges = sorted(
        (eid_to_stark[e["subj"]], e["predicate"], eid_to_stark[e["obj"]])
        for e in g.query(list(eid_to_stark), 1)["edges"]
    )
    return ents, edges


def test_chunked_matches_single_batch_state():
    a, b = _store(), _store()
    out_single = bulk_load(a, _NODES, _EDGES)
    out_chunk = bulk_load(b, _NODES, _EDGES, chunk_edges=1)   # 1 edge per batch
    assert _canonical_state(a) == _canonical_state(b)          # identical final graph
    assert out_chunk["n_batches"] == 1 + 2                     # nodes batch + 2 edge batches
    assert out_single["n_batches"] == 1


def test_chunked_edge_relists_endpoints_without_panic():
    # The whole risk of chunking: an edge batch must re-list its endpoint entities or
    # store.append panics on the missing local id. This asserts it lands the edge.
    store = _store()
    bulk_load(store, _NODES, _EDGES, chunk_edges=1)
    g = store.as_of(_BIG, _BIG)
    assert len(g.query([e["entity_id"] for e in g.entities()], 1)["edges"]) == 2
```

- [ ] **Step 2: Run to verify failure**

Run the goldengraph box-safe runner.
Expected: FAIL — `chunk_edges` currently ignored (single batch), so `n_batches` is 1 not 3 (and, if the naive path were used, a KeyError/panic on missing endpoints).

- [ ] **Step 3: Implement the chunked path**

Add to `bulk.py` and route `bulk_load` through it when `chunk_edges` is set:

```python
def bulk_load(store, nodes, edges, *, at=1, chunk_edges=None):
    # ... (unchanged node/edge building above) ...
    if chunk_edges is None:
        n_batches = _append_single(store, entities, edge_dicts, at)
    else:
        n_batches = _append_chunked(store, entities, edge_dicts, at, chunk_edges)
    return {"n_nodes": len(entities), "n_edges": len(edge_dicts),
            "n_dropped_edges": dropped, "n_batches": n_batches}


def _append_chunked(store, entities: list[dict], edges: list[dict], at: int, chunk: int) -> int:
    """Initial nodes-ONLY batch mints every stable id; then edge batches, each
    re-listing ONLY the endpoint entities it references so the store's overlap-merge
    (record_keys=[stark_id]) re-resolves them to the already-minted id (single
    inheritor -> no merge, no new mint). Bounds peak JSON size vs one giant batch."""
    if chunk < 1:
        raise ValueError(f"chunk_edges must be >= 1, got {chunk}")
    by_local = {e["local_id"]: e for e in entities}
    store.append(json.dumps({"entities": entities, "edges": [], "ingested_at": at}))
    n_batches = 1
    for start in range(0, len(edges), chunk):
        window = edges[start:start + chunk]
        needed = {e["subj_local"] for e in window} | {e["obj_local"] for e in window}
        batch_entities = [by_local[lid] for lid in sorted(needed)]
        store.append(json.dumps({"entities": batch_entities, "edges": window, "ingested_at": at}))
        n_batches += 1
    return n_batches
```

- [ ] **Step 4: Run — all green (parity holds)**

Run the goldengraph box-safe runner. Expected: PASS. The parity test is the load-bearing guard: chunked and single-batch must produce byte-equivalent graph state.

- [ ] **Step 5: Export `bulk_load` and commit**

Add to `packages/python/goldengraph/goldengraph/__init__.py`: `from .bulk import bulk_load` and add `"bulk_load"` to `__all__`.

```bash
git add packages/python/goldengraph/goldengraph/bulk.py packages/python/goldengraph/goldengraph/__init__.py packages/python/goldengraph/tests/test_bulk_load.py
git commit -m "feat(goldengraph): bulk_load chunked-edges fallback (OOM escape), export"
```

---

## Task 4: STaRK IR metrics (pure, box-safe)

**Files:**
- Create: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/stark_metrics.py`
- Create: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/tests/test_stark_metrics.py`

- [ ] **Step 1: Write the failing tests — hand-worked rankings**

```python
"""SP2 STaRK IR metrics + Arm-B dedup: pure, no store/HF/Modal. Box-safe."""
from erkgbench.stark_metrics import dedup_first_seen, metrics


def test_hit_at_1_gold_in_first_position():
    m = metrics(ranked_ids=[7, 3, 9], gold_ids={7})
    assert m["hit@1"] == 1.0 and m["hit@5"] == 1.0
    assert m["mrr"] == 1.0 and m["recall@20"] == 1.0


def test_hit_at_5_not_at_1():
    m = metrics(ranked_ids=[1, 2, 3, 4, 7], gold_ids={7})
    assert m["hit@1"] == 0.0 and m["hit@5"] == 1.0
    assert m["mrr"] == 1 / 5


def test_gold_absent():
    m = metrics(ranked_ids=[1, 2, 3], gold_ids={7})
    assert m == {"hit@1": 0.0, "hit@5": 0.0, "recall@20": 0.0, "mrr": 0.0}


def test_multi_gold_recall_partial():
    # 2 of 3 gold in the top-20
    m = metrics(ranked_ids=[10, 11, 99], gold_ids={10, 11, 42})
    assert m["recall@20"] == 2 / 3
    assert m["hit@1"] == 1.0 and m["mrr"] == 1.0


def test_zero_gold_recall_is_none_sentinel():
    # A query with no gold answers must NOT contribute to the recall mean.
    m = metrics(ranked_ids=[1, 2], gold_ids=set())
    assert m["recall@20"] is None      # caller skips None from the recall mean
    assert m["hit@1"] == 0.0 and m["mrr"] == 0.0


def test_dedup_first_seen_preserves_order_and_drops_repeats():
    assert dedup_first_seen([5, 3, 5, 9, 3]) == [5, 3, 9]
```

- [ ] **Step 2: Run to verify failure**

Run the metrics box-safe runner.
Expected: FAIL — module missing.

- [ ] **Step 3: Implement**

```python
"""Standard IR metrics for the STaRK retrieval spike. Definitions match STaRK
(arXiv 2404.13207): Hit@k, Recall@20, MRR over a single query's ranked id list
against its gold answer id set. Reimplemented (not STaRK's harness) -- ~30 lines,
no dependency or retriever-API coupling. `recall@20` returns None on a zero-gold
query so the caller can EXCLUDE it from the recall mean (never divide by zero)."""
from __future__ import annotations

from collections.abc import Iterable

_RECALL_K = 20


def dedup_first_seen(ids: Iterable[int]) -> list[int]:
    """De-duplicate preserving first-seen order (Arm-B ranks seeds ++ neighbors;
    an undeduped repeat can push a distinct gold id past position 20)."""
    seen: set[int] = set()
    out: list[int] = []
    for i in ids:
        if i not in seen:
            seen.add(i)
            out.append(i)
    return out


def metrics(ranked_ids: list[int], gold_ids: set[int]) -> dict:
    """One query's metrics. `ranked_ids`: rank-ordered retrieved ids (assumed already
    deduped by the caller). `gold_ids`: the answer set."""
    gold = set(gold_ids)
    hit1 = 1.0 if ranked_ids[:1] and ranked_ids[0] in gold else 0.0
    hit5 = 1.0 if gold & set(ranked_ids[:5]) else 0.0
    mrr = 0.0
    for rank, i in enumerate(ranked_ids, start=1):
        if i in gold:
            mrr = 1.0 / rank
            break
    recall = None if not gold else len(gold & set(ranked_ids[:_RECALL_K])) / len(gold)
    return {"hit@1": hit1, "hit@5": hit5, "recall@20": recall, "mrr": mrr}


def mean_metrics(per_query: list[dict]) -> dict:
    """Aggregate per-query dicts. Recall averages only over non-None (has-gold)
    queries; the rest average over all queries."""
    n = len(per_query) or 1
    rec = [m["recall@20"] for m in per_query if m["recall@20"] is not None]
    return {
        "hit@1": sum(m["hit@1"] for m in per_query) / n,
        "hit@5": sum(m["hit@5"] for m in per_query) / n,
        "mrr": sum(m["mrr"] for m in per_query) / n,
        "recall@20": (sum(rec) / len(rec)) if rec else 0.0,
        "n_queries": len(per_query),
        "n_with_gold": len(rec),
    }
```

- [ ] **Step 4: Run — all green** (create `erkgbench/tests/__init__.py` if the package needs it; mirror the existing tests dir layout).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/stark_metrics.py packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/tests/test_stark_metrics.py
git commit -m "feat(erkgbench): STaRK IR metrics (hit@k/recall@20/mrr) + arm-B dedup"
```

---

## Task 5: STaRK adapter — HF loader + arm runner (integration-only)

**Files:**
- Create: `packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/stark_adapter.py`

Not box-TDD'd (needs the HF download + a built native store + an embedder). Build it to a clear interface; it is exercised by the Modal run (Task 6). Keep the pure pieces (metrics, dedup) in Task 4 so they ARE tested.

- [ ] **Step 1: Implement `load_stark_kb`**

```python
"""STaRK KB adapter: download a STaRK semi-structured KB from HuggingFace and map
it to goldengraph's (nodes, edges) + a query set. Integration surface -- the pure
metrics live in stark_metrics.py (box-tested). See the spec for the id-space model.
"""
from __future__ import annotations

import time


def load_stark_kb(name: str):
    """Return (nodes, edges, queries). `name` in {"prime","amazon","mag"}.
    nodes: list of (stark_id:str, name:str, typ:str).
    edges: list of (subj_stark_id:str, predicate:str, obj_stark_id:str).
    queries: list of (query_text:str, gold_stark_ids:set[int]).  # ints -- match index/entity_id

    Uses the `stark_qa` package (SKB loader + QA split) when available; that is the
    canonical STaRK data path. The node's display name is title/name if present else
    the first line of its text; typ is the STaRK node_type. Stark integer ids are
    stringified so they slot straight into record_keys."""
    from stark_qa import load_qa
    from stark_qa.skb import load_skb

    skb = load_skb(name)
    nodes = []
    for nid in skb.node_ids:
        info = skb.get_node_info(nid)
        display = str(info.get("name") or info.get("title") or "").strip()
        nodes.append((str(nid), display, str(skb.node_type_dict[skb.node_types[nid]])))
    edges = [
        (str(s), str(skb.edge_type_dict[r]), str(o))
        for (s, r, o) in skb.get_tuples()   # (head, relation, tail)
    ]
    qa = load_qa(name)
    queries = [(row["query"], {int(a) for a in row["answer_ids"]}) for row in qa]
    return nodes, edges, queries
```

NOTE for the implementer: the exact `stark_qa` API names (`load_skb`, `get_tuples`, `answer_ids`) must be confirmed against the installed package version at build time via `stark_qa`'s README/source — adapt the attribute names if they differ. The mapping CONTRACT (what each field must contain) is fixed; the accessor names are the only thing that may drift.

- [ ] **Step 2: Implement `evaluate` (one arm over the query set)**

```python
def evaluate(index, slice_graph, stark_to_eid, eid_to_stark, queries, embedder, *,
             arm: str, sample: int | None = None) -> dict:
    """Run one retrieval arm over `queries`, return mean metrics + timing. `arm` in
    {"dense","graph"}. The index returns STARK ids (entity_id=int(stark_id) at build
    time), so Arm A needs no translation and covers ALL nodes. Arm B walks the STORE
    (as_of().query -- the thing under test), translating stark<->slice-local ids only
    at the walk boundary. `stark_to_eid`/`eid_to_stark` cover edge-endpoint nodes
    only, which is exactly the set that has neighbors."""
    from erkgbench.stark_metrics import dedup_first_seen, mean_metrics, metrics

    qs = queries[:sample] if sample else queries
    per_query, latencies = [], []
    for text, gold in qs:
        t0 = time.perf_counter()
        if arm == "dense":
            ranked = index.query(text, embedder, k=20)               # stark ids already
        elif arm == "graph":
            seeds = index.query(text, embedder, k=5)                 # stark ids
            seed_eids = [stark_to_eid[s] for s in seeds if s in stark_to_eid]
            nbr = [eid_to_stark[e["entity_id"]] for e in _neighbors(slice_graph, seed_eids)]
            ranked = dedup_first_seen([*seeds, *nbr])
        else:
            raise ValueError(f"unknown arm {arm!r}")
        latencies.append(time.perf_counter() - t0)
        per_query.append(metrics(ranked, gold))                      # gold: int stark ids
    agg = mean_metrics(per_query)
    lat = sorted(latencies)
    agg["latency_ms_mean"] = 1000 * sum(lat) / (len(lat) or 1)
    agg["latency_ms_p95"] = 1000 * lat[int(0.95 * (len(lat) - 1))] if lat else 0.0
    agg["arm"] = arm
    return agg


def _neighbors(slice_graph, seed_eids):
    """1-hop neighbor entity dicts of `seed_eids` (view-local ids) on the slice.
    `query(ids, 1)` returns {'entities':[...], 'edges':[...]}; entities are the seeds
    ++ their neighbors, so drop the seeds themselves."""
    if not seed_eids:
        return []
    res = slice_graph.query(list(seed_eids), 1)
    seed_set = set(seed_eids)
    return [e for e in res["entities"] if e["entity_id"] not in seed_set]
```

- [ ] **Step 3: Commit** (no box test — integration surface)

```bash
git add packages/python/goldenmatch/benchmarks/er-kg-bench/erkgbench/stark_adapter.py
git commit -m "feat(erkgbench): STaRK HF loader + dense/graph arm evaluator"
```

---

## Task 6: Modal feasibility entry (run-only)

**Files:**
- Create: `scripts/distill/modal_stark.py` (model on `scripts/distill/modal_bench.py` — same `modal.App`, image with rust/maturin + `stark_qa` + the two packages, a cache Volume for the HF download, a `resource`/`tracemalloc` RSS sample).

- [ ] **Step 1: Implement the entry**

The function (runs on the Modal box):
1. `nodes, edges, queries = load_stark_kb(kb)`  # `kb` default "prime"
2. `store = goldengraph_native._native.PyStore()`; time `bulk_load(store, nodes, edges)` → **ingest wall + peak RSS**. On MemoryError (single-batch OOM), re-run with `chunk_edges` and RECORD the node/edge count at the ceiling — that is a finding, not a silent fallback.
3. **Index over ALL nodes (not the slice — isolated nodes must stay in the dense baseline):**
   `index = EntityIndex.build([{"entity_id": int(sid), "canonical_name": name, "typ": typ} for sid, name, typ in nodes], embedder)` → **index-build wall + peak RSS**. `entity_id=int(stark_id)` so `query()` returns stark ids directly.
4. `slice_graph = store.as_of(_BIG, _BIG)`  (for the Arm-B store walk only)
5. `stark_to_eid = {int(e["source_refs"][0]): e["entity_id"] for e in slice_graph.entities() if e["source_refs"]}`; `eid_to_stark = {v: k for k, v in stark_to_eid.items()}`
6. `dense = evaluate(index, slice_graph, stark_to_eid, eid_to_stark, queries, embedder, arm="dense", sample=N)`
7. `graph = evaluate(index, slice_graph, stark_to_eid, eid_to_stark, queries, embedder, arm="graph", sample=N)`
8. Print a numbers table: `n_nodes / n_edges / n_dropped_edges / n_batches`, count of ISOLATED nodes (`n_nodes - len(stark_to_eid)`, the honesty caveat), ingest wall, index-build wall, per-query mean+p95 latency, peak RSS, and the dense-vs-graph metric rows.

Embedder: a real goldenmatch embedding provider (`GoldenmatchEmbedder("local")` or a GPU provider on the box). PRIME first with a query `sample` (e.g. 200) to keep the first run cheap.

- [ ] **Step 2: Run PRIME on Modal** (do NOT run in the box)

Creds from Infisical dev project (never in literals):
```
P=a99885f0-c5af-4ae1-9dc8-255cc60aa129
export MODAL_TOKEN_ID=$(infisical.cmd secrets get MODAL_TOKEN_ID --projectId $P --env dev --plain)
export MODAL_TOKEN_SECRET=$(infisical.cmd secrets get MODAL_TOKEN_SECRET --projectId $P --env dev --plain)
modal run --detach scripts/distill/modal_stark.py --kb prime --sample 200
```
`--detach` so it survives disconnect; poll status, don't foreground-loop.

- [ ] **Step 3: Record the numbers** in a verdict report `docs/superpowers/reports/2026-07-02-stark-prime-feasibility.md` (ingest/index/latency/RAM + dense-vs-graph table + the honest verdict: did it run at PRIME scale, does the graph arm beat dense). AMAZON is the same entry `--kb amazon` ONLY if PRIME is clean.

- [ ] **Step 4: Commit** the entry + report.

```bash
git add scripts/distill/modal_stark.py docs/superpowers/reports/2026-07-02-stark-prime-feasibility.md
git commit -m "feat(bench): Modal STaRK feasibility entry + PRIME verdict"
```

---

## Wrap-up

- [ ] Push branch, open PR against main, arm `gh pr merge --auto --squash`, STOP (no CI poll loop). Auth: `GH_TOKEN=$(gh auth token --user benzsevern)` for `gh pr create`, `unset GH_TOKEN` before push.
- [ ] Update memory `project_stark_retrieval_scale.md`: SP2 shipped, the PRIME numbers, the flat-vs-HNSW verdict, any OOM ceiling found.
- [ ] Doc surfaces: `bulk_load` is a new public goldengraph export — sweep per @feedback_rollout_docs_sweep at PR time.

## Notes / risks

- **Metrics tests and mapper tests are the only box-TDD'd units.** The adapter + Modal entry are integration surfaces validated by the actual PRIME run — that is deliberate (the whole point is the at-scale numbers, which can't be produced on the box).
- **The parity test (Task 3) is load-bearing:** chunked and single-batch MUST produce identical graph state, or the OOM fallback silently changes results. Do not weaken it.
- **`stark_qa` accessor names may drift** by version — the mapping contract is fixed; confirm the exact attribute names against the installed package when building Task 5.
- **OOM at single-batch AMAZON is an expected possible finding**, not a failure — record the ceiling and switch to `chunk_edges`.
- **`as_of()` surfaces edge-endpoint nodes only** (store.rs:412-432) — isolated nodes vanish from `slice_graph.entities()`. This is WHY the index is built over the full `nodes` list (Task 6 step 3), not the slice: a slice-built index would silently drop isolated nodes from the dense baseline and understate Recall@20. The Task 2/3 tests use all-connected fixtures so their slice-based assertions are unaffected; report the isolated-node count in the Task-6 verdict so the numbers stay honest.
