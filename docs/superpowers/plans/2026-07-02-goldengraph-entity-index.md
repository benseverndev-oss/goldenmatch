# GoldenGraph Entity Index (SP1) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the STaRK-scale retrieval blocker — embed each entity name ONCE into a persisted ANN index (reusing goldenmatch's `ANNBlocker`) so `seed_by_query` stops re-embedding all N names on every query.

**Architecture:** New `EntityIndex` owns the L2-normalized embedding array + `entity_id`↔row map, wrapping `ANNBlocker` (FAISS flat + numpy fallback). `query` embeds only the query. `seed_by_query`/`ask` gain an opt-in `index=`/`entity_index=` param (default `None` = today's behavior). Box-tested on the numpy fallback with stub embedders.

**Tech Stack:** Python, numpy, pytest. Reuses `goldenmatch.core.ann_blocker.ANNBlocker`.

**Spec:** `docs/superpowers/specs/2026-07-02-goldengraph-entity-index-design.md`

**Branch:** `feat/goldengraph-entity-index` (off `origin/main`; goldengraph already deps goldenmatch).

---

## Files

- **Create** `packages/python/goldengraph/goldengraph/entity_index.py` — `EntityIndex`.
- **Modify** `packages/python/goldengraph/goldengraph/embed.py` — `seed_by_query` gains `index=None`.
- **Modify** `packages/python/goldengraph/goldengraph/answer.py` — `ask` gains `entity_index=None`, threaded to `seed_by_query`.
- **Create** `packages/python/goldengraph/tests/test_entity_index.py` — box-safe tests (numpy fallback, stub embedders).

## Test runner (box-safe)

```bash
cd /d/show_case/gg-local-llm/packages/python/goldengraph
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_entity_index.py -q
```

## Ground-truth facts (verified)

- `ANNBlocker(top_k=…)`; `build_index(embeddings)` (stores `_corpus` on the numpy path); `query_one(vec) -> list[(neighbor_row, score)]` **rank-ordered, capped at `top_k`, filtered to row >= 0**. `_HAS_FAISS` (module flag) selects faiss vs the numpy `_np_search` — tests force `_HAS_FAISS=False` for hermetic runs. `IndexFlatIP` does NOT normalize internally → callers pass L2-normalized vectors.
- `seed_by_query(slice_graph, query, embedder, *, k=5)` (embed.py:66) filters entities: `typ` NOT startswith `"literal:"` AND non-empty stripped `canonical_name`. `Embedder` = Protocol `embed(texts) -> np.ndarray`. The embedder batches internally (seed_by_query passes all names in one `embed()` call).
- `ask()` (answer.py) calls `seeds = seed_by_query(slice_graph, query, embedder, k=k)` at line 323. Only source caller of `seed_by_query`.

---

### Task 1: `EntityIndex.build` + `query` (+ filter + k contract)

**Files:**
- Create: `goldengraph/entity_index.py`
- Test: `tests/test_entity_index.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_entity_index.py`:

```python
"""SP1 EntityIndex: embed-once ANN retrieval over entity names (box-safe, numpy fallback)."""
from __future__ import annotations

import numpy as np
import pytest

import goldenmatch.core.ann_blocker as _ab
from goldengraph.entity_index import EntityIndex


@pytest.fixture(autouse=True)
def _force_numpy_fallback(monkeypatch):
    # Hermetic: never depend on faiss being installed; numpy fallback gives the same neighbor set.
    monkeypatch.setattr(_ab, "_HAS_FAISS", False)


_VECS = {"apple": [1.0, 0.0, 0.0], "banana": [0.0, 1.0, 0.0], "cherry": [0.0, 0.0, 1.0]}


class _StubEmbedder:
    """Deterministic: known name -> its axis vector, unknown -> zero. Counts embed() calls."""
    def __init__(self):
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return np.array([_VECS.get(str(t).strip().lower(), [0.0, 0.0, 0.0]) for t in texts], dtype=float)


def _ent(eid, name, typ="thing"):
    return {"entity_id": eid, "canonical_name": name, "typ": typ}


def test_build_and_query_topk():
    idx = EntityIndex.build([_ent(0, "apple"), _ent(1, "banana"), _ent(2, "cherry")], _StubEmbedder())
    assert idx.query("banana", _StubEmbedder(), k=1) == [1]
    assert idx.query("apple", _StubEmbedder(), k=1) == [0]


def test_query_maps_rows_to_entity_ids():
    # non-contiguous ids: apple is row 0 but entity_id 5 -> query must return 5, not 0
    idx = EntityIndex.build([_ent(5, "apple"), _ent(1, "banana"), _ent(99, "cherry")], _StubEmbedder())
    assert idx.query("apple", _StubEmbedder(), k=1) == [5]
    assert idx.query("cherry", _StubEmbedder(), k=1) == [99]


def test_build_filters_literals_and_empty():
    ents = [_ent(0, "apple"), _ent(1, "2024-01-01", typ="literal:date"), _ent(2, "  ")]
    idx = EntityIndex.build(ents, _StubEmbedder())
    assert len(idx) == 1                      # only "apple"
    assert idx.query("apple", _StubEmbedder(), k=1) == [0]


def test_query_embeds_query_only():
    idx = EntityIndex.build([_ent(0, "apple"), _ent(1, "banana")], _StubEmbedder())
    emb = _StubEmbedder()
    idx.query("apple", emb, k=1)
    assert emb.calls == 1                      # ONE embed (the query), NOT N -- the anti-regression
    idx.query("banana", emb, k=1)
    assert emb.calls == 2                      # one more, still per-query O(1)


def test_query_rejects_k_above_capacity():
    idx = EntityIndex.build([_ent(0, "apple")], _StubEmbedder(), top_k=50)
    with pytest.raises(ValueError):
        idx.query("apple", _StubEmbedder(), k=100)


def test_empty_index_returns_empty():
    idx = EntityIndex.build([_ent(0, "  ", typ="literal:x")], _StubEmbedder())
    assert len(idx) == 0 and idx.query("apple", _StubEmbedder(), k=5) == []
```

- [ ] **Step 2: Run to verify it fails** — box-safe. Expected: `ModuleNotFoundError: goldengraph.entity_index`.

- [ ] **Step 3: Implement** — create `goldengraph/entity_index.py`:

```python
"""SP1: a persisted ANN index over entity canonical-name embeddings, keyed by entity_id.

Embed each entity name ONCE (batched by the embedder), keep an ANNBlocker (FAISS IndexFlatIP + numpy
fallback) over the L2-normalized vectors, and answer per-query top-k WITHOUT re-embedding the corpus --
the fix for seed_by_query's O(N)-embed-per-query blocker. EntityIndex OWNS the embedding array, so
persistence is backend-agnostic (np.save) and nothing reaches into ANNBlocker internals.
See docs/superpowers/specs/2026-07-02-goldengraph-entity-index-design.md.
"""
from __future__ import annotations

import numpy as np


def _l2(mat: np.ndarray) -> np.ndarray:
    mat = np.asarray(mat, dtype=np.float32)
    if mat.ndim == 1:
        return mat / (np.linalg.norm(mat) + 1e-12)
    return mat / (np.linalg.norm(mat, axis=1, keepdims=True) + 1e-12)


class EntityIndex:
    def __init__(self, corpus: np.ndarray, row_to_entity_id, top_k: int, dim: int):
        self._corpus = np.asarray(corpus, dtype=np.float32)
        self._row_to_entity_id = [int(x) for x in row_to_entity_id]
        self._top_k = int(top_k)
        self._dim = int(dim)
        self._blocker = None
        self._build_blocker()

    def _build_blocker(self) -> None:
        from goldenmatch.core.ann_blocker import ANNBlocker  # lazy: keep import off the hot path
        self._blocker = ANNBlocker(top_k=self._top_k)
        if len(self._row_to_entity_id):
            self._blocker.build_index(self._corpus)

    @classmethod
    def build(cls, entities, embedder, *, top_k: int = 50) -> "EntityIndex":
        """Filter to real entity nodes (typ not 'literal:*', non-empty name -- mirrors seed_by_query),
        embed all names ONCE, L2-normalize, index. `top_k` = index capacity (max neighbors per query)."""
        ids, names = [], []
        for e in entities:
            typ = str(e.get("typ", ""))
            name = str(e.get("canonical_name", "")).strip()
            if typ.startswith("literal:") or not name:
                continue
            ids.append(int(e["entity_id"]))
            names.append(name)
        if not names:
            return cls(np.zeros((0, 1), dtype=np.float32), [], top_k, 1)
        vecs = _l2(np.asarray(embedder.embed(names), dtype=np.float32))
        return cls(vecs, ids, top_k, vecs.shape[1])

    def query(self, query: str, embedder, *, k: int = 5) -> list[int]:
        """Embed the QUERY only, ANN top-k, map rows->entity_ids. Requires k <= top_k."""
        if k > self._top_k:
            raise ValueError(f"k={k} exceeds index top_k={self._top_k}; rebuild with a larger top_k")
        if not self._row_to_entity_id:
            return []
        q = _l2(np.asarray(embedder.embed([query]), dtype=np.float32)[0])
        rows = self._blocker.query_one(q)   # [(row, score)] rank-ordered, capped at top_k
        return [self._row_to_entity_id[r] for r, _ in rows[:k]]

    def __len__(self) -> int:
        return len(self._row_to_entity_id)
```

- [ ] **Step 4: Run to verify passes** — box-safe. Expected: PASS (6).

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/entity_index.py \
        packages/python/goldengraph/tests/test_entity_index.py
git commit -m "feat(goldengraph): EntityIndex build+query (embed-once ANN over entity names, k<=top_k) (SP1 task 1)"
```

---

### Task 2: `save` / `load` (backend-agnostic)

**Files:**
- Modify: `goldengraph/entity_index.py`
- Test: `tests/test_entity_index.py`

- [ ] **Step 1: Write the failing test**

```python
def test_save_load_roundtrip(tmp_path):
    ents = [_ent(5, "apple"), _ent(1, "banana"), _ent(99, "cherry")]
    idx = EntityIndex.build(ents, _StubEmbedder())
    idx.save(str(tmp_path / "idx"))
    loaded = EntityIndex.load(str(tmp_path / "idx"))
    assert len(loaded) == 3
    assert loaded.query("cherry", _StubEmbedder(), k=1) == [99]   # same top-k after reload
```

- [ ] **Step 2: Run to verify it fails** — box-safe `-k save_load`. Expected: FAIL — no `save`.

- [ ] **Step 3: Implement** — add to `EntityIndex` (`import json`, `import os` at module top):

```python
    def save(self, path: str) -> None:
        """Backend-agnostic: np.save the (normalized) corpus + row_to_entity_id + a meta.json. NO
        faiss.write_index -- EntityIndex owns the array, so load rebuilds the ANNBlocker from it."""
        os.makedirs(path, exist_ok=True)
        np.save(os.path.join(path, "corpus.npy"), self._corpus)
        np.save(os.path.join(path, "row_to_entity_id.npy"),
                np.asarray(self._row_to_entity_id, dtype=np.int64))
        with open(os.path.join(path, "meta.json"), "w", encoding="utf-8") as fh:
            json.dump({"top_k": self._top_k, "dim": self._dim}, fh)

    @classmethod
    def load(cls, path: str) -> "EntityIndex":
        corpus = np.load(os.path.join(path, "corpus.npy"))
        ids = np.load(os.path.join(path, "row_to_entity_id.npy")).tolist()
        with open(os.path.join(path, "meta.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
        return cls(corpus, ids, meta["top_k"], meta["dim"])
```

- [ ] **Step 4: Run to verify passes** — box-safe `-k save_load`. Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/entity_index.py \
        packages/python/goldengraph/tests/test_entity_index.py
git commit -m "feat(goldengraph): EntityIndex save/load (backend-agnostic np.save, no faiss internals) (SP1 task 2)"
```

---

### Task 3: `seed_by_query(index=)` seam + `ask(entity_index=)` threading

**Files:**
- Modify: `goldengraph/embed.py` (`seed_by_query`, line 66)
- Modify: `goldengraph/answer.py` (`ask`, line 323)
- Test: `tests/test_entity_index.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_seed_by_query_uses_index_when_given():
    from goldengraph.embed import seed_by_query

    # a minimal PyGraph-like stub exposing .entities()
    class _Graph:
        def __init__(self, ents):
            self._e = ents
        def entities(self):
            return self._e

    ents = [_ent(0, "apple"), _ent(1, "banana")]
    idx = EntityIndex.build(ents, _StubEmbedder())
    emb = _StubEmbedder()
    seeds = seed_by_query(_Graph(ents), "apple", emb, k=1, index=idx)
    assert seeds == [0]
    assert emb.calls == 1          # ONLY the query embedded (via the index), graph NOT re-embedded


def test_seed_by_query_none_preserves_current_path():
    from goldengraph.embed import seed_by_query

    class _Graph:
        def __init__(self, ents):
            self._e = ents
        def entities(self):
            return self._e

    ents = [_ent(0, "apple"), _ent(1, "banana")]
    emb = _StubEmbedder()
    seeds = seed_by_query(_Graph(ents), "apple", emb, k=1)   # index=None -> current re-embed path
    assert seeds == [0]
    assert emb.calls == 1          # current path embeds [query]+names in ONE call (embedder batches)
```

(Note: the current `seed_by_query` embeds `[query] + names` in a single `embed()` call, so `emb.calls == 1` on BOTH paths — the difference at scale is N names embedded vs 0. The index path is validated by `test_query_embeds_query_only` counting; here we assert behavior parity + correct seeds.)

- [ ] **Step 2: Run to verify it fails** — box-safe `-k "seed_by_query"`. Expected: FAIL — `seed_by_query() got an unexpected keyword argument 'index'`.

- [ ] **Step 3: Implement** — in `embed.py`, change `seed_by_query`'s signature + add the index branch at the top of the body:

```python
def seed_by_query(slice_graph, query: str, embedder: Embedder, *, k: int = 5, index=None) -> list[int]:
    # (docstring unchanged; add:) `index` (an EntityIndex): when given, query the prebuilt index
    # instead of re-embedding every entity name -- the O(1)-embed-per-query path for scale.
    if index is not None:
        return index.query(query, embedder, k=k)
    # ... existing body unchanged ...
```

In `answer.py`: add `entity_index=None` to `ask`'s keyword args, and thread it at line 323:
```python
    seeds = seed_by_query(slice_graph, query, embedder, k=k, index=entity_index)
```

- [ ] **Step 4: Run to verify passes** — box-safe `-k "seed_by_query"`, then the WHOLE file:
```bash
cd /d/show_case/gg-local-llm/packages/python/goldengraph
PYTHONIOENCODING=utf-8 PYTHONUTF8=1 POLARS_SKIP_CPU_CHECK=1 GOLDENMATCH_NATIVE=0 \
  /d/show_case/goldenmatch/.venv/Scripts/python.exe -m pytest tests/test_entity_index.py -q   # 9 green
```
Also run the existing goldengraph retrieval/answer tests to confirm the `seed_by_query`/`ask` signature change is back-compat (default None):
```bash
... -m pytest tests/test_chain_retrieval.py tests/test_asof_mode.py -q   # or the relevant answer/retrieve tests
```

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldengraph/goldengraph/embed.py \
        packages/python/goldengraph/goldengraph/answer.py \
        packages/python/goldengraph/tests/test_entity_index.py
git commit -m "feat(goldengraph): seed_by_query/ask opt-in index= (EntityIndex path, back-compat None) (SP1 task 3)"
```

---

### Task 4: Finish

- [ ] **Step 1: Full green** — `tests/test_entity_index.py` (9) + a sanity run of the existing goldengraph answer/retrieval tests (back-compat). Use the box-safe command; if a test needs the native store, note it (the EntityIndex tests do NOT).
- [ ] **Step 2: Lint** — `ruff check` the three files; fix findings.
- [ ] **Step 3: PR + arm** — push, open PR base `main`, arm `--auto`, STOP. (No Modal — SP1 is pure/box-safe; the STaRK feasibility run is SP2.)
- [ ] **Step 4: Memory** — note SP1 EntityIndex shipped in `project_goldengraph_local_oss_llm_lane.md` (or the STaRK program memory); SP2 = `StoreBatch` bulk-loader + STaRK-AMAZON feasibility run is next.

---

## Notes for the implementer

- **Box-safe only.** `tests/test_entity_index.py`. The `_force_numpy_fallback` fixture makes it faiss-independent. Do NOT run the full suite or Modal.
- **`ANNBlocker` import is lazy** (inside `_build_blocker`) so a plain `import goldengraph.entity_index` doesn't pull goldenmatch's ANN stack onto the import path.
- **`query` embeds the QUERY only** — the one property that matters. `test_query_embeds_query_only` is the anti-regression; don't let a refactor reintroduce corpus embedding in `query`.
- **Back-compat:** `seed_by_query(..., index=None)` and `ask(..., entity_index=None)` default to today's behavior; the only source caller (`ask` @ 323) passes the new kwarg. Verify existing answer/retrieval tests stay green.
- **`k <= top_k`** is a hard contract (`query_one` caps at `top_k`); `query` raises rather than silently truncating.
