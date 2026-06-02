# Columnar cluster-build core + dict adapter (Phase 2 SP1) — Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `core/cluster.py::build_clusters` compute clusters columnar (Arrow UF + the existing kernel's batched confidence + columnar pair_scores; auto-split unchanged) and emit a BYTE-IDENTICAL `dict[int,dict]` via an adapter, behind a gate — so the ~25 cluster consumers are untouched.

**Architecture:** A new internal `_build_clusters_via_frames(...)` that `build_clusters` dispatches to behind `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` (default OFF), AFTER the Ray short-circuit. It reuses `build_clusters_arrow_native` (Arrow UF → `ClusterFrames` whose metadata already carries bit-identical confidence/bottleneck), materializes `pair_scores` order-preservingly, runs the unchanged auto-split, computes cluster_quality, and a dict adapter reproduces the exact `dict[int,dict]`. NO new Rust kernel.

**Tech Stack:** Python 3.11+, Polars, the existing `goldenmatch._native` Arrow cluster kernel (already built). Pure-Python SP1.

**Spec:** `docs/superpowers/specs/2026-06-02-columnar-cluster-build-core-design.md` — READ it; this plan implements its routing. **Branch:** `feat/columnar-cluster-build`.

**Run tests:** `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest <path> -v`. ruff: `D:/show_case/goldenmatch/.venv/Scripts/python.exe -m ruff check <files>`. Native `_native.pyd` is built in-tree locally (so the native path is exercised). Do NOT run the full suite (xdist OOMs); targeted files only.

## Background the implementer needs (read these lines first)

- `build_clusters(pairs, all_ids, max_cluster_size, weak_cluster_threshold, auto_split) -> dict[int,dict]` (`cluster.py:361`). Flow: Ray short-circuit (:376-394) → derive all_ids → UF `connected_components` (:419-434) → sort clusters by `min(members)` + enumerate start=1 (:436-446) → `member_to_cid` → result dict `{members(list, UNSORTED per PR#598), size, oversized, pair_scores:{}}` (:448-466) → pair_scores fill (:468-471) → `compute_cluster_confidence` per cluster (:477-481) → auto-split oversized (:494-528) → cluster_quality (:531-546) → `_emit_cluster_profile(result)` (:548) → return.
- The output dict per cluster has EXACTLY: `members, size, oversized, pair_scores (dict[(a,b)]->score), confidence, bottleneck_pair, cluster_quality` (+ transient `_was_split` popped at :546). NOT `min_edge/avg_edge/connectivity`.
- `build_clusters_arrow_native(pairs_df, all_ids, max_cluster_size) -> ClusterFrames` (`cluster.py:1216`): Arrow UF; metadata columns `cluster_id, size, confidence, quality, oversized, bottleneck_pair_a/b`. The kernel computes confidence/bottleneck per cluster in pair-INPUT order via the same `cluster_confidence` logic (cluster.rs:478-486) → **bit-identical to the dict path's confidence** (the parity test proves this). `quality` is always "strong" (no auto-split/weak in the kernel) — do NOT use it; compute quality yourself.
- `compute_cluster_confidence(pair_scores, size)` (`cluster.py:552`): the off-native confidence path (bit-identical; Python sequential sum).
- `split_oversized_cluster(members, pair_scores)` (`cluster.py:157`): the auto-split, reused unchanged.
- `cluster_frames_to_dict` (`cluster.py:1121`) is LOSSY (sets `pair_scores={}`, :1157) — do NOT reuse it for the adapter; build a full-pair_scores adapter.
- `ClusterFrames` (`cluster.py:1009`); `PAIR_STREAM_SCHEMA` (id_a,id_b,score) from `core/scorer.py`.

---

## File Structure

- **Modify** `packages/python/goldenmatch/goldenmatch/core/cluster.py`: add `_columnar_cluster_build_enabled()` gate, `_build_clusters_via_frames(...)`, and dispatch in `build_clusters` (after the Ray short-circuit). The dict adapter is internal to `_build_clusters_via_frames`.
- **Test** `packages/python/goldenmatch/tests/test_columnar_cluster_build_parity.py` (create): the byte-identical gate.
- **Modify** `packages/python/goldenmatch/scripts/bench_build_clusters_arrow_spike.py` OR create `bench_columnar_cluster_build.py` + a workflow: the measure-first bench (Task 2).

---

## Task 1: `_build_clusters_via_frames` + gate + byte-identical parity gate

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/cluster.py`
- Test: `packages/python/goldenmatch/tests/test_columnar_cluster_build_parity.py`

- [ ] **Step 1: Write the failing byte-identical parity test**

The test runs `build_clusters` on an adversarial fixture with the gate OFF (current path) and ON (new columnar path) and asserts the dicts are IDENTICAL, key for key, under BOTH `GOLDENMATCH_NATIVE` states.

```python
import os
import polars as pl
import pytest
from goldenmatch.core.cluster import build_clusters


def _adversarial_pairs():
    # Cluster A: singleton id 0 (no pairs). Cluster B: 2-member {1,2}.
    # Cluster C: fully-connected {3,4,5}. Cluster D: weak chain {6,7,8}
    # (one weak edge -> triggers weak). Cluster E: oversized that SPLITS
    # (a barbell: two dense triangles joined by one weak bridge, > max_cluster_size=5).
    # Cluster F: score-tied edges (bottleneck tie-break). Plus duplicate canonical pair.
    pairs = [
        (1, 2, 0.95),
        (3, 4, 0.9), (4, 5, 0.92), (3, 5, 0.88),
        (6, 7, 0.99), (7, 8, 0.40),                 # weak: avg-min large
        # barbell F-side oversized (ids 10..16, 7 members > max 5):
        (10, 11, 0.99), (11, 12, 0.99), (10, 12, 0.99),
        (14, 15, 0.99), (15, 16, 0.99), (14, 16, 0.99),
        (12, 14, 0.31),                              # weak bridge -> splits
        (20, 21, 0.5), (20, 22, 0.5),                # score ties -> bottleneck first-occurrence
        (1, 2, 0.95),                                # duplicate canonical pair
    ]
    all_ids = list(range(0, 23))
    return pairs, all_ids


def _norm(cinfo: dict) -> dict:
    # members compared as a SET (order is hash-arbitrary + the columnar path runs a
    # separate UF -> list order legitimately differs; see spec). Everything else
    # byte-identical, incl. confidence float.
    out = {k: v for k, v in cinfo.items() if k not in ("members", "_was_split")}
    out["members"] = frozenset(cinfo["members"])
    return out


@pytest.mark.parametrize("native", ["1", "0"])
def test_columnar_build_byte_identical(monkeypatch, native):
    import goldenmatch.core.cluster as _cmod
    pairs, all_ids = _adversarial_pairs()
    monkeypatch.setenv("GOLDENMATCH_NATIVE", native)

    monkeypatch.setenv("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", "0")
    off = build_clusters(pairs, all_ids=all_ids, max_cluster_size=5,
                         weak_cluster_threshold=0.3, auto_split=True)

    # Spy to prove the columnar path actually runs with the gate ON (the RED in
    # Step 2/3: pre-impl the spy is never called).
    calls = []
    real = _cmod._build_clusters_via_frames
    monkeypatch.setattr(_cmod, "_build_clusters_via_frames",
                        lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    monkeypatch.setenv("GOLDENMATCH_COLUMNAR_CLUSTER_BUILD", "1")
    on = build_clusters(pairs, all_ids=all_ids, max_cluster_size=5,
                        weak_cluster_threshold=0.3, auto_split=True)
    assert calls, "columnar path (_build_clusters_via_frames) did not run with gate ON"

    assert on.keys() == off.keys()
    for cid in off:
        assert _norm(on[cid]) == _norm(off[cid]), f"cluster {cid} differs:\n on={on[cid]}\n off={off[cid]}"
```

(Cluster ids are deterministic and identical gate-on/off: both sort by `min(members)` + enumerate(start=1) pre-split, and splits assign `max(result.keys())+1` -- so `on.keys() == off.keys()`. The only legitimately-differing field is `members` LIST order, which `_norm` collapses to a set. `pair_scores`, `confidence`, `bottleneck_pair`, `cluster_quality`, `oversized` are compared exactly.)

ALSO add a **can't-split oversized cluster** to `_adversarial_pairs` (spec requires it): a DENSE oversized clique (e.g. 7 members 30..36 with ALL pairs at 0.99, no weak bridge) -> `split_oversized_cluster` returns it unchanged -> left `oversized`, quality stays (not "split"). This exercises the no-progress guard (:508-512).

- [ ] **Step 2: Run it — verify FAIL**

Run: `cd packages/python/goldenmatch && ../../../.venv/Scripts/python.exe -m pytest tests/test_columnar_cluster_build_parity.py -v`
Expected: FAIL — the gate ON currently does nothing (or `_build_clusters_via_frames` not defined). It should fall through to the same path and PASS trivially until you wire the gate to the new path; to get a real RED, ALSO assert (via a monkeypatch spy on `_build_clusters_via_frames`) that the columnar path actually ran with the gate on. Add that spy assertion so Step 2 is genuinely red.

- [ ] **Step 3: Add the gate + dispatch (gate ON still calls the OLD path)**

Add `_columnar_cluster_build_enabled()` (env `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD`, default `"0"`, enabled when `!= "0"` — mirror the identity `_batch_fingerprint_enabled` pattern). In `build_clusters`, AFTER the Ray short-circuit (:394) and the all_ids derivation, add: `if _columnar_cluster_build_enabled(): return _build_clusters_via_frames(...)`. Initially make `_build_clusters_via_frames` just call the existing body (extract the existing :419-548 body into a `_build_clusters_dict_path(...)` helper that BOTH the gate-off path and the temporary `_build_clusters_via_frames` call). Run the test — the spy now sees the columnar fn called, and parity passes trivially (both run the same body). Commit: `refactor(cluster): extract dict-path body + add columnar gate (no-op)`.

- [ ] **Step 4: Implement the real `_build_clusters_via_frames`**

Per the spec routing. Build `pairs_df` from `pairs` using the canonical schema:
`import` `PAIR_STREAM_SCHEMA` from `goldenmatch.core.scorer` (columns `id_a:i64,
id_b:i64, score:f64`) -- the kernel validates these dtypes (cluster.rs) and raises
on mismatch. Then:
1. **UF (source differs native vs off-native -- ISSUE 3):**
   - **Native** (`native_enabled("clustering")` and the arrow kernel is exposed):
     `frames = build_clusters_arrow_native(pairs_df, all_ids=all_ids,
     max_cluster_size=max_cluster_size)` -> `ClusterFrames`. The kernel does UF ONLY
     (no auto-split, verified) -> assignments + metadata are PRE-split. Get raw
     member sets per UF-component from `frames.assignments`.
   - **Off-native:** do NOT call `build_clusters_arrow_native` -- its
     `build_clusters_v2_columnar` fallback runs the FULL `build_clusters` incl.
     auto-split (POST-split frames). Instead source UF membership DIRECTLY from
     `connected_components(list(pairs), all_ids)` (or the Python `UnionFind`), the
     same pre-split UF the dict path uses (:419-434).
   Then, for BOTH: build the SAME pre-split `result` dict the dict path has at :466
   -- sort the UF member sets by `min(members)`, enumerate(start=1) for cluster ids,
   `members = list(members)` (set/UF order; the parity gate compares members as a
   SET so order need not match the dict path), `size`, `oversized = size >
   max_cluster_size`, `pair_scores = {}`. `member_to_cid` from this.
2. **pair_scores (ORDER-PRESERVING):** add a `cluster_id` column to `pairs_df` via `pl.col("id_a").replace_strict(member_to_cid)` (NOT a join). Iterate the resulting frame IN ORDER to build per-cluster `pair_scores` dicts (same as the dict path's :468-471, in pairs order). Duplicate canonical pairs overwrite (last wins) exactly as the dict path's `result[cid]["pair_scores"][(a,b)] = score`.
3. **confidence + bottleneck:** native -> read `confidence` + `(bottleneck_pair_a, bottleneck_pair_b)` from `frames.metadata` per cluster_id (map `(0,0) -> None`). off-native -> call `compute_cluster_confidence(pair_scores, size)` per cluster (bit-identical). Compute these on the ORIGINAL (pre-split) clusters, matching the dict path's ordering (confidence at :477-481 is BEFORE auto-split at :494).
4. **auto-split:** identical to the dict path's :494-528 loop, reusing `split_oversized_cluster(members, pair_scores)` + the edge-work-budget / no-progress guards. (The simplest correct approach: build the SAME `result` dict shape the dict path has just before :494, then run the dict path's auto-split + quality code verbatim. This guarantees parity for the split/quality logic — the columnar win is in steps 1-3.)
5. **cluster_quality:** identical to :531-546 (split if `_was_split`; else weak if `avg_edge - min_edge > weak_cluster_threshold` with `confidence *= 0.7`; else strong). Reuse the dict path's code.
6. **adapter / emit:** `result` IS the byte-identical output. `_finalize_clusters`
   (below) already calls `_emit_cluster_profile(result)` (it's at :548, inside the
   extracted range) -- do NOT call it again here (would double-emit). Just return
   what `_finalize_clusters` returns.

KEY: steps 4-5-6 should literally REUSE the dict path's code (extract :494-548 --
the auto-split loop + cluster_quality + the `_emit_cluster_profile(result)` call --
into a shared `_finalize_clusters(result, max_cluster_size, weak_cluster_threshold,
auto_split) -> result` helper that BOTH the dict path and the columnar path call).
The extraction is clean: :494-548 reads only `result` + the 3 params + module-level
helpers (`member_to_cid`/`sorted_clusters`/`pairs` are already `del`'d at :474-475).
The columnar path only changes HOW `result` is built up to :493 (UF + pair_scores +
confidence). This minimizes the parity surface to steps 1-3.

- [ ] **Step 5: Run the parity test — verify PASS (both native states)**

Run: `../../../.venv/Scripts/python.exe -m pytest tests/test_columnar_cluster_build_parity.py -v`
Expected: PASS for `native=1` AND `native=0`. If a cluster differs (other than members-set), PRINT `on[cid]` vs `off[cid]` and fix the columnar build for that field — do NOT relax the assertion beyond the members-set already in `_norm` (byte-identical on the rest is the durability invariant feeding golden/identity). Likely culprits: pair_scores order (must be pairs order), confidence (native metadata vs dict), id numbering, the `(0,0)` sentinel.

**LOCAL NATIVE CAVEAT (ISSUE 2):** the in-tree `_native.pyd` is STALE -- it lacks `build_clusters_arrow` (and `mst_split_components`), and it CANNOT be rebuilt locally (the pinned Rust toolchain is unavailable, `build_native.py` fails). So LOCALLY, `build_clusters_arrow_native` falls back to the columnar path even with `native=1`, meaning the `native=1` parity run exercises the OFF-NATIVE source, NOT the real Arrow kernel. The real native kernel path is validated ONLY in CI (fresh native build). Confirm `getattr(native_module(), "build_clusters_arrow", None)` locally; if None, note in your report that the native-kernel path is CI-only-validated. Do NOT block on it locally.

- [ ] **Step 6: Run the existing cluster tests with the gate ON (regression)**

Run: `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1 ../../../.venv/Scripts/python.exe -m pytest tests/test_cluster.py tests/test_golden.py tests/test_native_parity.py -q` — confirm no regressions with the columnar path forced on. NOTE: `test_native_parity.py::test_split_oversized_cluster_parity` may FAIL LOCALLY on a stale `_native.pyd` (AttributeError `mst_split_components`) — that is a PRE-EXISTING local artifact, ignore it; flag only NEW failures.

- [ ] **Step 7: ruff + commit**

ruff check the changed files. Commit: `feat(cluster): columnar cluster-build via _build_clusters_via_frames (byte-identical, gated)`.

---

## Task 2: measure-first bench

**Files:**
- Create: `packages/python/goldenmatch/scripts/bench_columnar_cluster_build.py`
- Create: `.github/workflows/bench-columnar-cluster-build.yml`

- [ ] **Step 1: Bench script**

Model on the spike `scripts/bench_build_clusters_arrow_spike.py` (it has the deterministic pair generator `_make_pairs_df`). Bench `build_clusters(pairs, ...)` gate-OFF vs gate-ON (via `os.environ["GOLDENMATCH_COLUMNAR_CLUSTER_BUILD"]`) at `--np 1000000,5000000`, median of 3, wall + peak RSS, with a dict-equality parity assertion on a small N first. Print a markdown table to `$GITHUB_STEP_SUMMARY`. ASCII only. Smoke locally at `--np 50000`.

- [ ] **Step 2: Workflow**

`bench-columnar-cluster-build.yml`: workflow_dispatch, `runs-on: large-new-64GB`, build native (`uv run python scripts/build_native.py`), run the bench. Model on `bench-build-clusters-arrow-spike.yml`.

- [ ] **Step 3: Commit**

Commit: `bench(cluster): columnar build vs dict measure-first harness`.

- [ ] **Step 4 (orchestrator, NOT a subagent step): dispatch + decide default-on**

Merge (the workflow must be on main to dispatch), dispatch at 1M/5M, read wall + RSS. Ship default-on only if the columnar build beats the dict build net of the adapter; else keep gated as the SP2-enabling foundation. Record the numbers in the spec.

---

## Final validation (orchestrator)

1. `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1 pytest tests/test_cluster.py tests/test_golden.py` + the parity test — green both native states.
2. Open the PR; CI runs the goldenmatch lane (cluster tests). Bench is workflow_dispatch.
3. Dispatch the bench; fold numbers into the spec; decide the gate default.

## Notes for the implementer

- **Byte-identical is the durability invariant** (cluster output feeds golden/identity). The parity test gates it native-on AND off. If a field can't be made identical, STOP and report the diff — do not relax.
- **Minimize the parity surface:** reuse the dict path's auto-split + quality + emit code verbatim (extract `_finalize_clusters`); the columnar path only changes the UF + pair_scores + confidence build (steps 1-3). DRY.
- **Order matters:** pair_scores in pairs order (`replace_strict` + in-order iterate, NOT a Polars join); members in UF order; confidence computed pre-split.
- **No new Rust kernel** — reuse the confidence/bottleneck on `ClusterFrames.metadata` (native) and `compute_cluster_confidence` (off-native).
- **Gate after the Ray short-circuit** (never intercept Ray datasets).
- **Skill:** @superpowers:test-driven-development per task.
