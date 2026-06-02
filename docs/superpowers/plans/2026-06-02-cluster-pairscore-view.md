# Lazy cluster pair-score view + identity migration (Phase 2 SP2) — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `ClusterPairScores` lazy view sourced from the FINAL (post-split) cluster partition, and migrate the `identity/resolve.py` evidence-edge consumer (all three pair_scores read sites) onto it, behind the existing `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` gate, BYTE-IDENTICAL.

**Architecture:** `core/cluster_pairscores.py::ClusterPairScores` copies the final `build_clusters` result's per-cluster `pair_scores` (dict-of-dicts backing) and exposes `for_cluster(cid)` / `iter_clusters()` / `score_for(cid,a,b)`. The pipeline constructs the view from the returned cluster dict when the gate is ON and threads it (optional param) into `_resolve_identities` -> `resolve_clusters`; the resolver reads pair_scores from the view when provided, else `info["pair_scores"]`. Because `view.for_cluster(cid)` returns the SAME data as the dict, parity is byte-identical by construction; the parity gate proves no drift, native AND off-native.

**Tech Stack:** Python 3.11+. Pure-Python SP2. No new Rust kernel; no `ClusterFrames` schema change; no perf bench / gate-default flip (this is an abstraction + de-risking step, not a perf win — see spec "Measure-first / gate").

**Spec:** `docs/superpowers/specs/2026-06-02-cluster-pairscore-view-design.md` — READ it (note the IDENTITY-ONLY re-scope; golden is deferred to issue #678). **Branch:** `feat/cluster-pairscore-view`.

**Run tests:** `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest <path> -v`. ruff: `D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check <files>`. Do NOT run the full suite (xdist OOMs); targeted files only. The native cluster kernel is CI-only-validatable locally (stale `_native.pyd`); SP2 is pure-Python so this only affects the native parity lane (CI).

## Background the implementer needs

- `build_clusters(...) -> dict[int,dict]` (`core/cluster.py:361`). Returned dict is byte-identical gate ON/OFF (SP1, PR #673). Each cluster: `members, size, oversized, pair_scores (dict[(a,b)->score]), confidence, bottleneck_pair, cluster_quality`. `pair_scores` is pairs-input order, last-wins on dup canonical pairs, FINAL post-split partition.
- `_columnar_cluster_build_enabled()` (`core/cluster.py:436`, reads `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD`, default `"0"`).
- `identity/resolve.py`: per-cluster loop header `for cluster_id, info in clusters.items():` (~`:333`). THREE pair_scores read sites in that loop:
  - bulk fast path (postgres) evidence edges: `pair_scores = info.get("pair_scores") or {}` (~`:396-398`),
  - slow path evidence edges: `pair_scores = info.get("pair_scores") or {}` (~`:559-560`),
  - weak bottleneck score: `info.get("pair_scores", {}).get((min(int(ba),int(bb)), max(int(ba),int(bb))))` (~`:601-602`).
  The loop variable is `cluster_id` (NOT `cid`). `resolve_clusters(...)` signature is at ~`:209` (keyword-friendly for a new optional kwarg).
- `core/pipeline.py`: cluster build inside an `if _use_columnar / else` block ending ~`:1460` (`build_clusters(...)` at `:1455` is the `else` branch); `_resolve_identities(clusters, ...)` defined at `:245`, called at `:1714`; `_resolve_identities` calls `resolve_clusters(...)` (~`:308`) on the in-memory path.

---

## File Structure

- **Create** `goldenmatch/core/cluster_pairscores.py`: `ClusterPairScores` (the view).
- **Create** `tests/test_cluster_pairscores.py`: view unit tests.
- **Modify** `goldenmatch/identity/resolve.py`: optional `pair_score_view` param on `resolve_clusters`; read all three sites from it when provided.
- **Modify** `goldenmatch/core/pipeline.py`: when the gate is ON, build the view from the returned cluster dict and thread it through `_resolve_identities` into `resolve_clusters`.
- **Create** `tests/test_cluster_pairscore_view_parity.py`: byte-identical evidence-edge parity gate (gate ON vs OFF, native + off-native).

---

## Task 1: `ClusterPairScores` view + unit tests

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/core/cluster_pairscores.py`
- Test: `packages/python/goldenmatch/tests/test_cluster_pairscores.py`

- [ ] **Step 1: Write the failing unit tests**

```python
import pytest
from goldenmatch.core.cluster_pairscores import ClusterPairScores


def _clusters():
    return {
        1: {"members": [0], "size": 1, "pair_scores": {}},
        2: {"members": [1, 2], "size": 2, "pair_scores": {(1, 2): 0.9}},
        3: {"members": [3, 4, 5], "size": 3,
            "pair_scores": {(3, 4): 0.8, (4, 5): 0.7, (3, 5): 0.6}},
    }


def test_for_cluster_matches_dict_exactly():
    clusters = _clusters()
    view = ClusterPairScores.from_cluster_dict(clusters)
    for cid, info in clusters.items():
        assert view.for_cluster(cid) == info["pair_scores"]


def test_for_cluster_missing_or_singleton_is_empty():
    view = ClusterPairScores.from_cluster_dict(_clusters())
    assert view.for_cluster(1) == {}
    assert view.for_cluster(999) == {}


def test_iter_clusters_yields_pairs_in_row_order():
    view = ClusterPairScores.from_cluster_dict(_clusters())
    got = {cid: list(pairs) for cid, pairs in view.iter_clusters()}
    assert got[2] == [(1, 2, 0.9)]
    assert got[3] == [(3, 4, 0.8), (4, 5, 0.7), (3, 5, 0.6)]
    assert 1 not in got  # singleton contributes no rows


def test_score_for_bottleneck_lookup():
    view = ClusterPairScores.from_cluster_dict(_clusters())
    assert view.score_for(3, 5, 3) == 0.6   # canonical (min,max) regardless of arg order
    assert view.score_for(3, 9, 9) is None
```

- [ ] **Step 2: Run — verify FAIL**

Run: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_cluster_pairscores.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Implement `ClusterPairScores`**

```python
"""Lazy per-cluster pair-score view (Phase 2 SP2). Decouples the identity
evidence-edge consumer from the legacy per-cluster ``pair_scores`` dict. Sourced
from the FINAL (post-split) cluster partition so it is byte-identical to the dict
path. dict-of-dicts backing; the ``iter_clusters`` interface is frame-ready for a
future SP that makes the build produce a columnar pair frame natively."""
from __future__ import annotations

from typing import Iterable, Iterator


class ClusterPairScores:
    __slots__ = ("_by_cid",)

    def __init__(self, by_cid: dict[int, dict[tuple[int, int], float]]):
        self._by_cid = by_cid

    @classmethod
    def from_cluster_dict(cls, clusters: dict[int, dict]) -> "ClusterPairScores":
        by_cid: dict[int, dict[tuple[int, int], float]] = {}
        for cid, info in clusters.items():
            ps = info.get("pair_scores") or {}
            if ps:
                by_cid[cid] = dict(ps)
        return cls(by_cid)

    def for_cluster(self, cid: int) -> dict[tuple[int, int], float]:
        return self._by_cid.get(cid, {})

    def iter_clusters(self) -> Iterator[tuple[int, Iterable[tuple[int, int, float]]]]:
        for cid, ps in self._by_cid.items():
            yield cid, [(a, b, s) for (a, b), s in ps.items()]

    def score_for(self, cid: int, a: int, b: int) -> float | None:
        return self._by_cid.get(cid, {}).get((min(a, b), max(a, b)))
```

- [ ] **Step 4: Run — verify PASS**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_cluster_pairscores.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: ruff + commit**

ruff check both files. Commit: `feat(cluster): ClusterPairScores lazy view (SP2 foundation)`.

---

## Task 2: identity migration + byte-identical evidence-edge parity gate

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/identity/resolve.py`
- Modify: `packages/python/goldenmatch/goldenmatch/core/pipeline.py`
- Test: `packages/python/goldenmatch/tests/test_cluster_pairscore_view_parity.py`

- [ ] **Step 1: Write the failing byte-identical evidence-edge parity test**

The test resolves identities against a SQLite store with the gate OFF (dict path) then ON (view path) on an adversarial cluster fixture, and asserts the emitted `evidence_edges` are byte-identical. Model the harness on an existing `tests/identity/` test that calls `resolve_clusters(...)` with a clusters dict + an `IdentityStore(backend="sqlite", path=str(tmp_path/"x.db"))` (find one and copy its setup). Steps:
- Build a clusters dict via `build_clusters(pairs, all_ids, max_cluster_size=5, auto_split=True)` on an adversarial fixture (multi-member, an oversized-split cluster, singletons, dup canonical pair) — reuse the fixture shape from `tests/test_columnar_cluster_build_parity.py`.
- Resolve gate OFF (no view), read back all edges from the store, sort to a canonical tuple list `[(entity_id, record_a_id, record_b_id, kind, score), ...]`.
- Resolve gate ON: build `view = ClusterPairScores.from_cluster_dict(clusters)`, pass `pair_score_view=view` to `resolve_clusters`; read back + sort the same way. Add a spy asserting the view path actually ran (e.g. wrap `view.for_cluster`).
- `assert on_edges == off_edges`. Parametrize `GOLDENMATCH_NATIVE` `["1","0"]`; skip native=1 when the native cluster kernel is absent (copy the guard from `test_columnar_cluster_build_parity.py`).

- [ ] **Step 2: Run — verify FAIL**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_cluster_pairscore_view_parity.py -v`
Expected: FAIL (`resolve_clusters` doesn't accept `pair_score_view` yet / spy never fires).

- [ ] **Step 3: Add the optional `pair_score_view` param to `resolve_clusters` and use it at all three sites**

In `identity/resolve.py`, add `pair_score_view: "ClusterPairScores | None" = None` to `resolve_clusters` (~`:209`; use a string annotation or a TYPE_CHECKING import to avoid a runtime import cycle). Inside the `for cluster_id, info in clusters.items():` loop, replace the THREE reads:

At `:396-398` (bulk path) and `:559-560` (slow path), both currently:
```python
pair_scores = info.get("pair_scores") or {}
```
become:
```python
pair_scores = (
    pair_score_view.for_cluster(cluster_id)
    if pair_score_view is not None
    else (info.get("pair_scores") or {})
)
```

At `:601-602` (bottleneck), currently:
```python
bottleneck_score = (
    info.get("pair_scores", {}).get((min(int(ba), int(bb)), max(int(ba), int(bb))))
)
```
becomes:
```python
bottleneck_score = (
    pair_score_view.score_for(cluster_id, int(ba), int(bb))
    if pair_score_view is not None
    else info.get("pair_scores", {}).get((min(int(ba), int(bb)), max(int(ba), int(bb))))
)
```

Use the loop's `cluster_id` (NOT `cid`). Behavior is byte-identical because `for_cluster(cluster_id) == info["pair_scores"]` and `score_for` returns the same canonical-pair score. Do NOT otherwise change the edge-emit loops. (Both the bulk `:396` and slow `:559` sites get the identical substitution; the SQLite parity test covers the slow path, the bulk path is the same one-line change in the same loop reading the same `info` — covered by code parity, no postgres CI lane required for SP2.)

- [ ] **Step 4: Wire the pipeline to build + pass the view when the gate is ON**

In `core/pipeline.py`, after the cluster-build `if/else` block closes (after ~`:1460`, where `clusters` is the dict), and before `_resolve_identities` (`:1714`): when `_columnar_cluster_build_enabled()` (import from `core.cluster`), build `view = ClusterPairScores.from_cluster_dict(clusters)`; else `view = None`. Thread `view` through `_resolve_identities` (`:245`) into its `resolve_clusters(...)` call (~`:308`) as `pair_score_view=view`. Build the view ONCE; reuse if other call sites need it later (SP3+).

**Gate trap (IMPORTANT):** `pipeline.py` has TWO live columnar gates. Use `_columnar_cluster_build_enabled()` (from `core.cluster`, reads `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD`) for the view — the SAME gate that drives the columnar `build_clusters`. Do NOT reuse the in-scope local `_use_columnar` flag (pipeline.py:1445, reads the DIFFERENT `GOLDENMATCH_COLUMNAR_PIPELINE` via `_columnar_pipeline_enabled()`); that's an unrelated gate and wiring the view to it would mis-gate the migration.

- [ ] **Step 5: Run — verify PASS (both native states)**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_cluster_pairscore_view_parity.py -v`
Expected: PASS for native=0 (native=1 if the kernel is present; else SKIP). If edges differ, STOP and report — do NOT relax. This is the entity-id durability proof.

- [ ] **Step 6: Regression — existing identity suite with the gate ON**

Run: `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1 ../../../.venv/Scripts/python.exe -m pytest tests/identity/ -q`
Confirm no NEW failures vs gate OFF (flag only new ones; pre-existing native artifacts excepted).

- [ ] **Step 7: ruff + commit**

ruff check the changed files. Commit: `feat(identity): resolve evidence edges via ClusterPairScores view (gated, byte-identical)`.

---

## Final validation (orchestrator)

1. `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1 pytest tests/test_cluster_pairscores.py tests/test_cluster_pairscore_view_parity.py tests/identity/` — green (native=0; native=1 via CI).
2. Open the PR; CI's native lane validates the native=1 byte-identical evidence-edge gate with the fresh kernel (CI-only — the durability proof, as in SP1).
3. No bench / gate-flip (per spec: identity-only is an abstraction step, gate stays default-OFF; the perf win + default-on decision belong to a future columnar-frame SP).

## Notes for the implementer

- **Byte-identical is the durability invariant** — identity evidence edges feed entity-ids. The view returns the SAME data as the dict (`for_cluster(cid) == info["pair_scores"]`), so parity should be trivial; if it isn't, you wired something wrong — STOP and report, do not relax.
- **Build the view ONCE per pipeline run** (gate ON). DRY.
- **Gate at the pipeline level** after the cluster build returns — never reach into `_build_clusters_via_frames` internals.
- **Loop variable is `cluster_id`**, not `cid`.
- **All three resolve sites** migrate (bulk `:396`, slow `:559`, bottleneck `:601`) — the bulk path matters for the postgres lane even though the SQLite parity test exercises the slow path.
- **Native cluster path is CI-only-validatable locally**; the parity test skips native=1 locally, runs it in CI's fresh-native lane.
- **Golden is NOT in scope** (issue #678). Do not touch golden.
- **Skill:** @superpowers:test-driven-development per task.
