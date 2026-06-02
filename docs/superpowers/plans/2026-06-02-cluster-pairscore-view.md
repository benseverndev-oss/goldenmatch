# Lazy cluster pair-score view + identity/golden migration (Phase 2 SP2) — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `ClusterPairScores` lazy view sourced from the FINAL (post-split) cluster partition, and migrate the two durability/output-critical `pair_scores` consumers — `identity/resolve.py` (evidence edges + bottleneck) and golden `confidence_majority` — onto it, behind the existing `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` gate, BYTE-IDENTICAL.

**Architecture:** A new `core/cluster_pairscores.py::ClusterPairScores` flattens the final `build_clusters` result's per-cluster `pair_scores` into a `(cluster_id, id_a, id_b, score)` frame and exposes `iter_clusters()` (one columnar pass for identity) + `for_cluster(cid)` (per-cluster dict for golden + bottleneck). The pipeline constructs the view from the returned cluster dict when the gate is ON and threads it (optional param) into the resolver and the golden build; both fall back to `cluster["pair_scores"]` when no view is passed. Because `view.for_cluster(cid)` returns the SAME data as the dict, parity is byte-identical by construction; the parity gates prove no drift, native AND off-native.

**Tech Stack:** Python 3.11+, Polars. Pure-Python SP2. No new Rust kernel; no `ClusterFrames` schema change.

**Spec:** `docs/superpowers/specs/2026-06-02-cluster-pairscore-view-design.md` — READ it. **Branch:** `feat/cluster-pairscore-view`.

**Run tests:** `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest <path> -v`. ruff: `D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check <files>`. Do NOT run the full suite (xdist OOMs); targeted files only. The native cluster kernel is CI-only-validatable locally (stale `_native.pyd`); SP2 is pure-Python so this only affects the native parity lane (CI).

## Background the implementer needs

- `build_clusters(...) -> dict[int,dict]` (`core/cluster.py:361`). When `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD != "0"` it runs the columnar path; either way the returned dict is byte-identical (SP1, PR #673). Each cluster dict: `members, size, oversized, pair_scores (dict[(a,b)->score]), confidence, bottleneck_pair, cluster_quality`. `pair_scores` is in pairs-input order, last-wins on duplicate canonical pairs, and reflects the FINAL post-split partition (cross-cut edges dropped by `split_oversized_cluster`).
- `identity/resolve.py`: evidence edges emitted by `for pair_key, score in info["pair_scores"].items()` at `:560`; bottleneck-score lookup `info.get("pair_scores", {}).get((min,max))` at `:601-602`. The enclosing loop has the cluster id and `info` (the cluster dict).
- `golden.py::_confidence_majority` (`:235+`) reads a per-cluster `pair_scores` dict (param). Only the `confidence_majority` strategy (and `custom:` plugin strategies) read it; the columnar `build_golden_records_df` fast path does NOT. The pipeline golden build is at `pipeline.py:1591-1615`; it passes each multi-member cluster's `pair_scores` into `build_golden_records_batch`.
- The gate helper `_columnar_cluster_build_enabled()` already exists in `core/cluster.py` (reads `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD`, default `"0"`).

---

## File Structure

- **Create** `packages/python/goldenmatch/goldenmatch/core/cluster_pairscores.py`: `ClusterPairScores` (the view). One responsibility: serve per-cluster pair scores from a flat frame.
- **Create** `packages/python/goldenmatch/tests/test_cluster_pairscores.py`: view unit tests.
- **Modify** `packages/python/goldenmatch/goldenmatch/identity/resolve.py`: optional `pair_score_view` param; read evidence-edge + bottleneck scores from it when provided.
- **Modify** `packages/python/goldenmatch/goldenmatch/core/pipeline.py`: when the gate is ON, build the view from the returned cluster dict and thread it into the resolver and golden build.
- **Create** `packages/python/goldenmatch/tests/test_cluster_pairscore_view_parity.py`: byte-identical evidence-edge + golden parity gates (gate ON vs OFF, native + off-native).
- **Create** `packages/python/goldenmatch/scripts/bench_cluster_pairscore_view.py` + `.github/workflows/bench-cluster-pairscore-view.yml`: measure-first bench.

---

## Task 1: `ClusterPairScores` view + unit tests

**Files:**
- Create: `packages/python/goldenmatch/goldenmatch/core/cluster_pairscores.py`
- Test: `packages/python/goldenmatch/tests/test_cluster_pairscores.py`

- [ ] **Step 1: Write the failing unit tests**

```python
import polars as pl
import pytest
from goldenmatch.core.cluster_pairscores import ClusterPairScores


def _clusters():
    # cid 1: singleton (no pairs). cid 2: {1,2} one pair. cid 3: {3,4,5} three pairs
    # incl a duplicate canonical pair (last-wins).
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
    assert view.for_cluster(1) == {}      # singleton, no pairs
    assert view.for_cluster(999) == {}    # absent cluster


def test_iter_clusters_yields_pairs_in_row_order():
    view = ClusterPairScores.from_cluster_dict(_clusters())
    got = {cid: list(pairs) for cid, pairs in view.iter_clusters()}
    # only clusters WITH pairs appear; pairs in insertion/row order.
    assert got[2] == [(1, 2, 0.9)]
    assert got[3] == [(3, 4, 0.8), (4, 5, 0.7), (3, 5, 0.6)]
    assert 1 not in got  # singleton contributes no rows


def test_last_wins_on_duplicate_canonical_pair():
    clusters = {7: {"members": [1, 2], "size": 2, "pair_scores": {(1, 2): 0.5}}}
    # simulate the dict path's last-wins: the dict already collapsed dups, so
    # from_cluster_dict must preserve whatever the dict holds.
    view = ClusterPairScores.from_cluster_dict(clusters)
    assert view.for_cluster(7) == {(1, 2): 0.5}


def test_score_for_bottleneck_lookup():
    view = ClusterPairScores.from_cluster_dict(_clusters())
    assert view.score_for(3, 3, 5) == 0.6
    assert view.score_for(3, 9, 9) is None
```

- [ ] **Step 2: Run — verify FAIL**

Run: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_cluster_pairscores.py -v`
Expected: FAIL (`ModuleNotFoundError` / `cluster_pairscores` not defined).

- [ ] **Step 3: Implement `ClusterPairScores`**

```python
"""Lazy per-cluster pair-score view (Phase 2 SP2). Decouples pair_scores
consumers (identity evidence edges, golden confidence_majority) from the
legacy per-cluster ``pair_scores`` dict. Sourced from the FINAL (post-split)
cluster partition so it is byte-identical to the dict path."""
from __future__ import annotations

from typing import Iterable, Iterator


class ClusterPairScores:
    """Serves per-cluster pair scores from a flat ``(cluster_id, id_a, id_b,
    score)`` table. Construct via :meth:`from_cluster_dict`."""

    __slots__ = ("_by_cid",)

    def __init__(self, by_cid: dict[int, dict[tuple[int, int], float]]):
        # cid -> {(a, b): score} in insertion (pairs-input) order. Internal;
        # callers use from_cluster_dict.
        self._by_cid = by_cid

    @classmethod
    def from_cluster_dict(cls, clusters: dict[int, dict]) -> "ClusterPairScores":
        """Build the view from a finalized ``build_clusters`` result dict. Copies
        each cluster's ``pair_scores`` (already final-partition, pairs-order,
        last-wins). Clusters with no pairs contribute nothing."""
        by_cid: dict[int, dict[tuple[int, int], float]] = {}
        for cid, info in clusters.items():
            ps = info.get("pair_scores") or {}
            if ps:
                by_cid[cid] = dict(ps)
        return cls(by_cid)

    def for_cluster(self, cid: int) -> dict[tuple[int, int], float]:
        """That cluster's ``{(a, b): score}`` (empty for singleton/absent)."""
        return self._by_cid.get(cid, {})

    def iter_clusters(self) -> Iterator[tuple[int, Iterable[tuple[int, int, float]]]]:
        """Yield ``(cluster_id, pairs)`` for clusters WITH pairs; pairs as
        ``(a, b, score)`` in row order. One pass for identity's evidence edges."""
        for cid, ps in self._by_cid.items():
            yield cid, [(a, b, s) for (a, b), s in ps.items()]

    def score_for(self, cid: int, a: int, b: int) -> float | None:
        """Score for the canonical pair ``(min, max)`` in ``cid`` (or None)."""
        return self._by_cid.get(cid, {}).get((min(a, b), max(a, b)))
```

(NOTE: SP2 sources from the final result dict, so the internal repr is a
dict-of-dicts, NOT a Polars frame. The spec's "flat frame" is the conceptual
model; a dict-of-dicts is the simplest byte-identical-safe backing and avoids a
premature flatten/partition_by that would only pay off once the BUILD produces
the frame natively — explicitly deferred to a later SP. The `iter_clusters`
interface is frame-ready: a future SP can swap the backing to a Polars
`partition_by` without touching consumers.)

- [ ] **Step 4: Run — verify PASS**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_cluster_pairscores.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: ruff + commit**

ruff check the two files. Commit: `feat(cluster): ClusterPairScores lazy view (SP2 foundation)`.

---

## Task 2: identity migration + byte-identical evidence-edge parity gate

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/identity/resolve.py`
- Modify: `packages/python/goldenmatch/goldenmatch/core/pipeline.py`
- Test: `packages/python/goldenmatch/tests/test_cluster_pairscore_view_parity.py`

- [ ] **Step 1: Write the failing byte-identical evidence-edge parity test**

The test runs identity resolution against a SQLite store with the gate OFF (dict path) and ON (view path) on an adversarial cluster fixture, and asserts the emitted `evidence_edges` are byte-identical. Use the existing identity test harness pattern (`tests/identity/`) for store setup (`IdentityStore(backend="sqlite", path=str(tmp_path/"x.db"))`) and a small dedupe result. Assert the sorted edge tuples `(record_a_id, record_b_id, kind, score)` per entity are identical ON vs OFF, parametrized over `GOLDENMATCH_NATIVE` in `["1","0"]` (skip native=1 when the native cluster kernel is absent, mirroring `tests/test_columnar_cluster_build_parity.py`'s guard). Include an oversized-split cluster in the fixture so the post-split partition is exercised.

The implementer should model the harness on an existing `tests/identity/` resolve test (find one that calls `resolve_clusters`/`_resolve_identities` with a clusters dict). The assertion compares the full edge set read back from the store after resolve, gate ON vs OFF.

- [ ] **Step 2: Run — verify FAIL**

Run: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_cluster_pairscore_view_parity.py -v -k evidence`
Expected: FAIL (the resolver doesn't accept/use a view yet, OR — if you write the spy first — the view path isn't wired). Get a genuine RED by asserting (spy) that the view path actually ran with the gate ON.

- [ ] **Step 3: Add the optional `pair_score_view` param to the resolver**

In `identity/resolve.py`, thread an optional `pair_score_view: ClusterPairScores | None = None` into the resolve function that owns the per-cluster loop (the one with `info` at `:559`). Where it currently reads `pair_scores = info.get("pair_scores") or {}` (`:559`) and `info.get("pair_scores", {}).get(...)` (`:601-602`), change to:

```python
if pair_score_view is not None:
    pair_scores = pair_score_view.for_cluster(cid)
else:
    pair_scores = info.get("pair_scores") or {}
```

and for the bottleneck lookup:

```python
bottleneck_score = (
    pair_score_view.score_for(cid, int(ba), int(bb))
    if pair_score_view is not None
    else info.get("pair_scores", {}).get((min(int(ba), int(bb)), max(int(ba), int(bb))))
)
```

`cid` is the cluster id from the enclosing loop. Behavior is byte-identical because `for_cluster(cid) == info["pair_scores"]` and `score_for` returns the same canonical-pair score. Do NOT change the edge-emit loop body otherwise.

- [ ] **Step 4: Wire the pipeline to build + pass the view when the gate is ON**

In `core/pipeline.py`, after `clusters = build_clusters(...)` (`:1455`) and before `_resolve_identities` (`:1714`), when `_columnar_cluster_build_enabled()` (import from `core.cluster`), build `view = ClusterPairScores.from_cluster_dict(clusters)` and pass it through `_resolve_identities` into the resolver as `pair_score_view`. When the gate is OFF, pass `None` (today's path). Thread the param through `_resolve_identities`'s signature.

- [ ] **Step 5: Run — verify PASS (both native states)**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_cluster_pairscore_view_parity.py -v -k evidence`
Expected: PASS for native=0 (and native=1 if the kernel is present; else SKIP). If edges differ, STOP and report — do not relax. The durability invariant (entity-id evidence edges) is the whole point.

- [ ] **Step 6: ruff + commit**

Commit: `feat(identity): resolve evidence edges via ClusterPairScores view (gated, byte-identical)`.

---

## Task 3: golden migration + byte-identical golden parity gate

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/pipeline.py` (golden build `:1591-1615`)
- Test: `packages/python/goldenmatch/tests/test_cluster_pairscore_view_parity.py` (add golden case)

- [ ] **Step 1: Write the failing byte-identical golden parity test**

Add a test that builds golden records with the `confidence_majority` strategy on a multi-member-cluster fixture (configure `golden_rules` survivorship `confidence_majority`), gate OFF vs ON, and asserts the golden records are byte-identical. Reuse `tests/test_golden.py` fixtures/patterns for the `confidence_majority` setup. Parametrize `GOLDENMATCH_NATIVE` `["1","0"]` with the same native-skip guard.

- [ ] **Step 2: Run — verify FAIL**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_cluster_pairscore_view_parity.py -v -k golden`
Expected: FAIL (golden build not yet sourcing from the view; add a spy to assert the view path ran with the gate ON for a genuine RED).

- [ ] **Step 3: Source the golden build's per-cluster pair_scores from the view when gate ON**

In `pipeline.py:1591-1615`, where the golden build pulls each multi-member cluster's `pair_scores` to pass into `build_golden_records_batch`, source it from `view.for_cluster(cid)` when the view is present (gate ON), else `cluster["pair_scores"]` (today). The view is the SAME one built in Task 2 Step 4 — build it ONCE per pipeline run and reuse for both identity and golden. `confidence_majority` (and `custom:` strategies) then receive the identical per-cluster dict.

- [ ] **Step 4: Run — verify PASS (both native states)**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_cluster_pairscore_view_parity.py -v`
Expected: PASS (evidence + golden, native=0; native=1 if kernel present else SKIP). If golden differs, STOP and report.

- [ ] **Step 5: Regression — existing identity + golden suites with the gate ON**

Run: `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1 ../../../.venv/Scripts/python.exe -m pytest tests/identity/ tests/test_golden.py -q`
Confirm no NEW failures vs gate OFF (flag only new ones; pre-existing native artifacts excepted).

- [ ] **Step 6: ruff + commit**

Commit: `feat(golden): source confidence_majority pair_scores via ClusterPairScores view (gated, byte-identical)`.

---

## Task 4: measure-first bench

**Files:**
- Create: `packages/python/goldenmatch/scripts/bench_cluster_pairscore_view.py`
- Create: `.github/workflows/bench-cluster-pairscore-view.yml`

- [ ] **Step 1: Bench script**

Model on `scripts/bench_columnar_cluster_build.py` (SP1's harness — same per-variant subprocess isolation, ASCII table, `$GITHUB_STEP_SUMMARY`). Bench the identity-resolve + golden(`confidence_majority`) stages end-to-end (or the closest measurable slice that exercises the pair_scores consumers) gate OFF vs ON at `--np 1000000,5000000`, median of 3, wall + peak RSS, with a byte-identical assertion (evidence edges + golden) on a small N first. Reuse the SP1 deterministic pair generator. Smoke locally at `--np 50000`.

- [ ] **Step 2: Workflow**

`bench-cluster-pairscore-view.yml`: workflow_dispatch, `runs-on: large-new-64GB`, build native, run the bench. Model on `bench-columnar-cluster-build.yml`.

- [ ] **Step 3: Commit**

Commit: `bench(cluster): SP2 identity+golden view vs dict measure-first harness`.

- [ ] **Step 4 (orchestrator, NOT a subagent step): dispatch + decide gate default**

Merge (workflow must be on main to dispatch), dispatch at 1M/5M, read wall + RSS. Per spec, SP2 MAY flip `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` default-ON if the columnar identity+golden path wins net. If it's a wash/loss (likely, since the view is sourced from the still-materialized dict), keep gated; the abstraction + de-risked identity/golden migration is the deliverable, and a later SP makes the build produce the frame natively. Record the numbers in the spec.

---

## Final validation (orchestrator)

1. `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1 pytest tests/test_cluster_pairscores.py tests/test_cluster_pairscore_view_parity.py tests/identity/ tests/test_golden.py` — green (native=0; native=1 via CI).
2. Open the PR; CI's native lane validates the native=1 byte-identical evidence-edge + golden gates with the fresh kernel (CI-only — the durability proof, as in SP1).
3. Dispatch the bench; fold numbers into the spec; decide the gate default.

## Notes for the implementer

- **Byte-identical is the durability invariant** — identity evidence edges feed entity-ids. The view returns the SAME data as the dict (`for_cluster(cid) == info["pair_scores"]`), so parity should be trivial; if it isn't, you wired something wrong — STOP and report, do not relax the gate.
- **Build the view ONCE per pipeline run** (gate ON), reuse for identity AND golden. DRY.
- **Gate after the columnar build returns**, at the pipeline level — never reach into `_build_clusters_via_frames` internals (the spec's "construction point" pin).
- **Native cluster path is CI-only-validatable locally** (stale `_native.pyd`); the parity tests skip native=1 locally and run it in CI's fresh-native lane.
- **Skill:** @superpowers:test-driven-development per task.
