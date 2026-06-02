# unmerge_record optional scored_pairs source — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `core/cluster.py::unmerge_record` an optional `scored_pairs` list it re-clusters from (falling back to `cluster["pair_scores"]`), so a future build that drops `pair_scores` won't break it. BYTE-IDENTICAL.

**Architecture:** Build the affected cluster's per-cluster score map ONCE inside `unmerge_record` — from `scored_pairs` filtered to that cluster's members (canonical `(min,max)` keys) when provided, else `cinfo.get("pair_scores") or {}` — and route BOTH the memory-correction loop and the re-cluster extraction through that one local map. Wire `tui/engine.py::unmerge_record` to pass `self._last_result.scored_pairs` (covers MCP + REST, which route through the engine).

**Tech Stack:** Python 3.11+. Pure-Python. No new deps.

**Spec:** `docs/superpowers/specs/2026-06-02-unmerge-scored-pairs-design.md` — READ it. **Branch:** `feat/unmerge-scored-pairs`.

**Run tests / verify:** Local `import goldenmatch` HANGS (Polars-level env issue, unresolved). Verify with `ruff` + `py_compile` locally; the actual pytest runs in CI. ruff: `D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check <files>`; compile: `... -m py_compile <files>`. Do NOT attempt to run pytest locally (it will hang on import).

## Background the implementer needs (exact current `unmerge_record` body)

`unmerge_record(record_id, clusters, threshold=0.0, *, memory_store=None, dataset=None) -> dict` (`core/cluster.py:941`). After finding the cluster containing `record_id` (`:963-968`), getting `cinfo = clusters[source_cid]` (`:973`), and the `size <= 1` early-return (`:974-975`):
- **`:978-988` memory loop** (only if `memory_store is not None`): iterates `cinfo.get("pair_scores", {}).keys()` (ALREADY tolerant `.get`) to collect `(record_id, other)` edges for correction.
- **`:990-996` re-cluster extraction:** `remaining_members = [m for m in cinfo["members"] if m != record_id]`; `remaining_pairs = [(a,b,s) for (a,b),s in cinfo["pair_scores"].items() if a != record_id and b != record_id and s >= threshold]`. The `cinfo["pair_scores"].items()` at `:994` is the DIRECT subscript (the KeyError blocker).
- **`:998-1024`** re-clusters via `build_clusters(remaining_pairs, remaining_members)`, deletes the old cluster, adds `record_id` as a singleton + the re-clustered sub-clusters. UNCHANGED by this work.

`pair_scores` keys are `(min,max)` in practice (all upstream producers canonicalize). `EngineResult.scored_pairs` (`tui/engine.py:40`) is the flat `list[(a,b,s)]` fed to `build_clusters`; `engine.unmerge_record` (`:401-408`) calls core `unmerge_record(record_id, self._last_result.clusters, threshold)`.

---

## File Structure

- **Modify** `goldenmatch/core/cluster.py`: `unmerge_record` — add `scored_pairs` kwarg, build one local `pair_scores` map, route both consumers through it.
- **Modify** `goldenmatch/tui/engine.py`: pass `scored_pairs=self._last_result.scored_pairs`.
- **Create** `tests/test_unmerge_scored_pairs.py`: byte-identical parity + decouple + engine-wiring tests.

---

## Task 1: `unmerge_record` optional scored_pairs source

**Files:**
- Modify: `goldenmatch/core/cluster.py`
- Test: `tests/test_unmerge_scored_pairs.py`

- [ ] **Step 1: Write the failing parity test**

Create `tests/test_unmerge_scored_pairs.py`. The fixture is a clusters dict built by hand (no pipeline → no import-hang on the *data*, though the import itself hangs locally — runs in CI). Flatten its pair_scores to a flat list and assert `scored_pairs=` gives byte-identical output to the dict-read path.

```python
import copy
from goldenmatch.core.cluster import unmerge_record


def _clusters():
    # cid 1: chain {0,1,2} held by 0-1 (0.9) and 1-2 (0.5); removing 1 splits it.
    # cid 2: pair {3,4}. cid 3: singleton {5}.
    return {
        1: {"members": [0, 1, 2], "size": 3, "oversized": False,
            "pair_scores": {(0, 1): 0.9, (1, 2): 0.5}, "confidence": 0.7,
            "bottleneck_pair": (1, 2), "cluster_quality": "weak"},
        2: {"members": [3, 4], "size": 2, "oversized": False,
            "pair_scores": {(3, 4): 0.95}, "confidence": 0.95,
            "bottleneck_pair": None, "cluster_quality": "strong"},
        3: {"members": [5], "size": 1, "oversized": False,
            "pair_scores": {}, "confidence": 1.0,
            "bottleneck_pair": None, "cluster_quality": "strong"},
    }


def _flat(clusters):
    out = []
    for c in clusters.values():
        for (a, b), s in c["pair_scores"].items():
            out.append((a, b, s))
    return out


def test_scored_pairs_matches_dict_path_for_each_record():
    base = _clusters()
    flat = _flat(base)
    for rid in [0, 1, 2, 3, 4, 5, 99]:
        from_dict = unmerge_record(rid, copy.deepcopy(base))
        from_flat = unmerge_record(rid, copy.deepcopy(base), scored_pairs=flat)
        assert from_flat == from_dict, f"record {rid}: {from_flat} != {from_dict}"
```

- [ ] **Step 2: Run — verify FAIL**

CANNOT run pytest locally (import hangs). Instead: `py_compile` the test (syntax) and confirm `unmerge_record` has no `scored_pairs` param yet (so CI would TypeError). Note in your report that RED is established by the absent kwarg; CI confirms.
Run: `D:/show_case/goldenmatch/.venv/Scripts/python.exe -m py_compile tests/test_unmerge_scored_pairs.py` (expect exit 0).

- [ ] **Step 3: Add `scored_pairs` + build the one local map**

In `unmerge_record`'s signature, add `scored_pairs: list[tuple[int, int, float]] | None = None` to the keyword-only block (after `dataset`). Immediately after the `size <= 1` early-return (`:975`) and `cinfo = clusters[source_cid]`, insert:

```python
    member_set = set(cinfo["members"])
    if scored_pairs is not None:
        pair_scores = {
            (min(a, b), max(a, b)): s
            for a, b, s in scored_pairs
            if a in member_set and b in member_set
        }
    else:
        pair_scores = cinfo.get("pair_scores") or {}
```

Then change BOTH consumers to use the local `pair_scores`:
- `:980` memory loop: `for (a, b) in pair_scores.keys():`
- `:994` re-cluster: `for (a, b), s in pair_scores.items()`

Leave everything else (`remaining_members`, `build_clusters` call, singleton add, sub-cluster add) unchanged.

- [ ] **Step 4: ruff + py_compile (local), confirm logic**

Run: `D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check goldenmatch/core/cluster.py tests/test_unmerge_scored_pairs.py` (expect clean) and `... -m py_compile goldenmatch/core/cluster.py` (expect exit 0). Re-read your diff: confirm both `:980` and `:994` now read the local `pair_scores`, and the `scored_pairs` path canonicalizes keys to `(min,max)`.

- [ ] **Step 5: Add the decouple test**

Add to `tests/test_unmerge_scored_pairs.py`: prove unmerge works when the cluster dict has NO pair_scores key but `scored_pairs` is supplied:

```python
def test_recluster_from_scored_pairs_without_dict_pair_scores():
    base = _clusters()
    flat = _flat(base)
    # Strip pair_scores from every cluster (simulate the future build-drop).
    stripped = copy.deepcopy(base)
    for c in stripped.values():
        c.pop("pair_scores", None)
    out = unmerge_record(1, stripped, scored_pairs=flat)
    # Removing 1 from {0,1,2} (edges 0-1, 1-2): 1 becomes singleton, 0 and 2 are
    # no longer connected -> each its own singleton. 3 clusters from the original 1.
    members = sorted(sorted(c["members"]) for c in out.values())
    assert [0] in members and [1] in members and [2] in members


def test_none_path_tolerant_when_pair_scores_absent():
    # None path on a pair_scores-less dict degrades (no KeyError): shatter.
    stripped = _clusters()
    stripped[1].pop("pair_scores", None)
    out = unmerge_record(1, stripped)   # no scored_pairs
    assert out is not None  # did not raise
```

- [ ] **Step 6: ruff + py_compile, commit**

ruff + py_compile the two files (expect clean). Commit: `feat(cluster): unmerge_record accepts optional scored_pairs source (byte-identical, decouples from cluster pair_scores)`.

---

## Task 2: wire the TUI engine (covers MCP + REST)

**Files:**
- Modify: `goldenmatch/tui/engine.py`
- Test: `tests/test_unmerge_scored_pairs.py` (add engine test)

- [ ] **Step 1: Add the engine test**

Add a test that the engine passes its `scored_pairs` and produces the expected clusters. Construct a minimal `MatchEngine` with a stubbed `_last_result` (an `EngineResult` with hand-built clusters + matching `scored_pairs`), call `engine.unmerge_record(rid)`, assert the updated `_last_result.clusters` matches `unmerge_record(rid, clusters, scored_pairs=...)`. If constructing `MatchEngine` is heavy, instead assert via monkeypatch that core `unmerge_record` was called with `scored_pairs=` equal to `_last_result.scored_pairs` (spy on `goldenmatch.core.cluster.unmerge_record`). Use whichever is cleaner against the real `EngineResult`/`MatchEngine` shape (read `tui/engine.py`).

- [ ] **Step 2: Wire the engine**

In `tui/engine.py::unmerge_record` (`:408`), change the core call to:
```python
clusters = unmerge_record(
    record_id, self._last_result.clusters, threshold,
    scored_pairs=self._last_result.scored_pairs,
)
```
(Keep everything else — the new `EngineResult` construction — unchanged.)

- [ ] **Step 3: ruff + py_compile, commit**

ruff + py_compile `goldenmatch/tui/engine.py` + the test (expect clean). Commit: `feat(tui): engine.unmerge_record passes scored_pairs (SP4-ready, byte-identical)`.

---

## Final validation (orchestrator)

1. ruff + py_compile clean on all changed files (local).
2. Open the PR; CI runs the full goldenmatch lane — the parity + decouple + engine tests run there (local import hang prevents local pytest). Confirm green, and confirm existing `test_cluster.py` / TUI / MCP unmerge tests still pass (byte-identical change).
3. No gate, no bench. `cluster["pair_scores"]` still read on the None path; no behavior change today.

## Notes for the implementer

- **Byte-identical:** the `scored_pairs`-filtered map equals the cluster's `pair_scores` for a single cluster (cross-cut edges excluded by the member-filter). If any parity assertion fails in CI, STOP and report — do not relax.
- **ONE local map, BOTH consumers** (`:980` memory loop + `:994` re-cluster) — don't leave one reading the dict directly.
- **Local pytest is unavailable** (Polars import hangs); verify via ruff + py_compile + CI. This is the established posture for this environment.
- **Out of scope:** `unmerge_cluster`, the web router, dropping `pair_scores` from the build.
- **Skill:** @superpowers:test-driven-development (RED established via CI / absent-kwarg, since local pytest can't run).
