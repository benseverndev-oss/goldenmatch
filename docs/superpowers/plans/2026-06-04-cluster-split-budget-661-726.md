# Cluster split efficiency + edge-work budget (#661 + #726) Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split an oversized cluster all the way down to `max_cluster_size` from a SINGLE max-spanning-tree build (kills the O(E·k) re-MST pathology, #661), scale the auto-split edge-work budget to dataset size + make it config-tunable, and turn the budget-exhaustion path into a loud, actionable warning that keeps the oversized clusters in the output flagged rather than silently losing them (#726).

**Architecture:** A new pure-Python `split_oversized_cluster_to_size(members, pair_scores, max_size)` builds the MST once, then repeatedly cuts the weakest tree edge of any still-oversized component (a sub-tree of a max spanning tree IS the max spanning tree of its induced sub-graph — proven below — so cutting original tree edges reproduces today's per-component re-MST cut decisions, yielding the **identical membership partition**). The two callers (`_finalize_clusters` dict/columnar path and the `build_cluster_frames` inline loop) drop their per-pass re-MST loop and call the batch function once per top-level oversized cluster, charging `edge_work` once per top-level cluster. The budget becomes `max(5_000_000, n_rows * C)` with precedence config-field > env > auto-scaled.

**What "byte-identical" means here (read carefully — this scopes the whole plan).** The BINDING invariant is the **membership partition** (which records co-cluster) — that is what the cycle-property proof guarantees and what the #528 quality gate + the columnar/dict parity gate enforce. Integer **cluster-id labels** for split clusters are NOT a cross-version durability contract: identity entity_ids are content-based stable record fingerprints (the `:h1:` scheme), not the ephemeral `cluster_id`, so a label reshuffle for split clusters does not move entity_ids. What IS required for labels: (a) **run-to-run determinism** (guaranteed by sort-by-min-member), and (b) **the dict path and the columnar/frames path produce the SAME labels as each other** (the existing `test_columnar_drop_pairscores_byte_identical` gate, which both paths satisfy because both adopt the same batch function and the same caller labeling). To keep (b) and (a) intact, both callers MUST iterate top-level oversized clusters in the same order and the batch function MUST emit sub-clusters deterministically (specified in Task 2). The plan does NOT claim — and does not need — label equality against the pre-#661 release.

**Tech Stack:** Python 3.12, polars, numpy, pytest; optional Rust native kernel (`mst_split_components`) stays available but is no longer on the oversized-split hot path. uv workspace; run tests via `D:/show_case/gm-661/.venv/Scripts/python.exe -m pytest` (Windows local) per `packages/python/CLAUDE.md`.

---

## Why the single-MST batch split is byte-identical (read before coding)

Today (`split_oversized_cluster`, `core/cluster.py:157`): for each oversized cluster the caller filters that cluster's induced pairs (`ps = {(a,b):s for a,b,s in pairs_list if a in ms and b in ms}`, O(E)), builds the max spanning tree from `ps` (`_build_mst`, Kruskal score-desc + stable sort, O(E log E)), removes the SINGLE weakest tree edge (`min(mst, key=e[2])`, first-minimum tie-break), and re-unions. The caller re-enqueues each still-oversized sub-cluster and **repeats the whole filter + MST build** — O(E·k) for k cuts (#661).

**Key theorem (cycle property):** Let `T` be a maximum spanning tree of `G`. Remove an edge of `T`, splitting it into components `C1, C2`. Then `T` restricted to `C1` is a maximum spanning tree of the induced sub-graph `G[C1]`. Proof: if some edge `e=(u,v)` with both endpoints in `C1`, `e ∉ T`, were heavier than the lightest edge on the `T`-path between `u` and `v` (a path lying entirely in `C1`), then `T` would not be a maximum spanning tree of `G` (cycle property violated). No such `e` exists, so `T[C1]` is already maximum for `G[C1]`. □

Therefore cutting weakest **tree edges of the one original MST** produces the SAME components, with the SAME first-minimum tie-break, as today's per-component re-MST. Tie-break order is preserved because the induced edge sort is a subsequence of the full edge sort (same relative order), Kruskal selects the identical tree-edge set, so iteration order — and thus `min(...)`'s first-minimum — matches. The batch function reuses the existing `compute_cluster_confidence` / pair-partition code per final sub-cluster, so confidence + bottleneck_pair are unchanged. This guarantees the **membership partition** is identical to the old loop's; see the Architecture note above for why cluster-id label equality vs the old release is NOT claimed.

**The automated backstops:**
- NEW old-vs-new MEMBERSHIP equivalence test (Task 1): a faithful reference that runs the REAL old algorithm (repeated single-edge `split_oversized_cluster` + re-enqueue) and asserts the batch function returns the SAME set of `frozenset(members)` with the same per-component confidence/bottleneck. This locks the partition against the actual pre-#661 behavior (NOT tautological — the reference uses the unchanged single-edge function, a genuinely different code path from the batch loop).
- `tests/test_columnar_drop_pairscores_parity.py::test_columnar_drop_pairscores_byte_identical` (columnar vs dict path: members-as-set, size, oversized, confidence EXACT float, bottleneck_pair, cluster_quality, AND cluster ids byte-identical) — this enforces label invariant (b): the two live paths agree. Extend with fixtures that have ≥2 oversized top-level clusters and a ≥3-component split (Task 3).
- `tests/test_native_parity.py::test_split_oversized_cluster_parity` (single-edge `split_oversized_cluster` native-vs-python) — must still pass; `split_oversized_cluster` is NOT removed.
- The #528 in-house quality gate (CI) — the co-clustering / F1 backstop on real benchmark data.

## Out of scope (per spec)
- Min-cut quality upgrade (#661 optional). Changes co-clustering, breaks invariance gate. Deferred.
- EM non-convergence (#726 hypothesis 3). Parked as a follow-up note on #726.

## File structure

- `packages/python/goldenmatch/goldenmatch/core/cluster.py` — add `split_oversized_cluster_to_size`; change `_split_edge_work_budget` signature; rewrite the two split loops in `_finalize_clusters` (`:873-923`) and `build_cluster_frames` (`:588-669`) to call it once per top-level oversized cluster; thread `n_rows` + budget override; upgrade the WARNING text. `split_oversized_cluster` (`:157`) STAYS.
- `packages/python/goldenmatch/goldenmatch/config/schemas.py` — add `split_edge_budget: int | None = None` to `GoldenRulesConfig` (`:439`).
- `packages/python/goldenmatch/goldenmatch/core/pipeline.py` — unpack `split_edge_budget` from `config.golden_rules` (`:1455-1461`) and pass it + `n_rows=len(all_ids)` to the three build call sites (`:1497`, `:1504`, `:1519`).
- `packages/python/goldenmatch/goldenmatch/core/cluster.py` build-function signatures — the FULL dispatch chain gains `split_edge_budget: int | None = None` (keyword, default None → unchanged behavior): public `build_clusters` (`:361`), `build_cluster_frames` (`:463`), `build_clusters_columnar` (`:1568`); AND the private wrappers `build_clusters` dispatches into — `_build_clusters_dict_path` (`:756`, calls `_finalize_clusters` at `:831`) and `_build_clusters_via_frames` (`:967`, calls `_finalize_clusters` at `:1044`); AND `_finalize_clusters` (`:836`) itself. `build_clusters_v2_columnar` (`:1849`) and `build_clusters_arrow_native` (`:1903`) forward into `build_clusters` and are NOT on the pipeline cluster-step path; leave them at the auto-scale default (do not thread the field) — note this in the plan so it's a deliberate omission, not a miss.
- Tests: extend `tests/test_columnar_drop_pairscores_parity.py`; new `tests/test_cluster_split_to_size.py` (equivalence + perf + budget + failure-mode).

---

### Task 1: `split_oversized_cluster_to_size` — single-MST batch split (#661 root fix)

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/cluster.py` (add function after `split_oversized_cluster`, ~`:224`)
- Test: `packages/python/goldenmatch/tests/test_cluster_split_to_size.py` (create)

The function runs the SAME cut decisions the callers run today, but builds the MST ONCE and filters pairs ONCE. It returns final sub-clusters in a deterministic order (sort-by-min-member at each cut, oversized re-enqueued LIFO) so the two live callers assign labels consistently. Global `next_cid` labeling stays with the caller — this function only produces the partition + per-sub confidence/bottleneck.

- [ ] **Step 1: Write the membership-equivalence test (failing) — faithful OLD reference via the unchanged single-edge function**

```python
# tests/test_cluster_split_to_size.py
"""#661: split_oversized_cluster_to_size builds the MST once and splits all the
way to max_size, producing the SAME membership partition (and per-component
confidence/bottleneck) as the old repeated single-weakest-edge loop. The
reference below drives the UNCHANGED single-edge split_oversized_cluster, so it
is a genuinely different code path from the batch loop (not tautological)."""
from __future__ import annotations

import pytest
from goldenmatch.core.cluster import (
    split_oversized_cluster,
    split_oversized_cluster_to_size,
)


def _old_partition(members, pair_scores, max_size):
    """Reference: the pre-#661 algorithm. Repeatedly call the unchanged
    single-edge split_oversized_cluster on any still-oversized component,
    re-filtering that component's induced pairs each pass (exactly what
    _finalize_clusters:880-915 did). Returns the FINAL list of sub-cluster
    dicts. No cluster-id labeling here — this test locks the PARTITION, not
    labels (label parity across the two live paths is locked separately by
    test_columnar_drop_pairscores_byte_identical)."""
    work = [list(members)]
    final = []
    while work:
        comp = work.pop()
        if len(comp) <= max_size:
            final.append(comp)
            continue
        ms = set(comp)
        ps = {(a, b): s for (a, b), s in pair_scores.items() if a in ms and b in ms}
        subs = split_oversized_cluster(comp, ps)
        if len(subs) <= 1:
            final.append(comp)        # unsplittable: stays as-is, oversized
            continue
        for sc in subs:
            work.append(sc["members"])
    return final


def _dense_clique(nodes, score=0.99):
    return {(a, b): score for i, a in enumerate(nodes) for b in nodes[i + 1:]}


@pytest.mark.parametrize("native", ["0", "1"])
def test_batch_split_membership_matches_old(monkeypatch, native):
    monkeypatch.setenv("GOLDENMATCH_NATIVE", native)
    # Splits into 3 components at max_size=2: three cliques joined by 2 weak bridges.
    members = list(range(10, 19))
    ps = {}
    ps.update(_dense_clique([10, 11, 12]))
    ps.update(_dense_clique([13, 14, 15]))
    ps.update(_dense_clique([16, 17, 18]))
    ps[(12, 13)] = 0.30   # weak bridge 1
    ps[(15, 16)] = 0.25   # weak bridge 2
    got = split_oversized_cluster_to_size(members, ps, max_size=2)
    want = _old_partition(members, ps, max_size=2)
    # Membership partition is the binding invariant: compare as a set of frozensets.
    assert {frozenset(s["members"]) for s in got} == {frozenset(c) for c in want}
    # Per-sub confidence/bottleneck must match the single-edge path too. Index by
    # frozenset(members) since order is not the contract here.
    from goldenmatch.core.cluster import compute_cluster_confidence
    for s in got:
        ms = set(s["members"])
        induced = {(a, b): v for (a, b), v in ps.items() if a in ms and b in ms}
        ref = compute_cluster_confidence(induced, len(ms))
        assert round(s["confidence"], 12) == round(ref["confidence"], 12)
        assert s["bottleneck_pair"] == ref["bottleneck_pair"]
```

- [ ] **Step 2: Run it to confirm it fails (function undefined)**

Run: `D:/show_case/gm-661/.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_cluster_split_to_size.py -v`
Expected: FAIL — `ImportError: cannot import name 'split_oversized_cluster_to_size'`.

- [ ] **Step 3: Implement `split_oversized_cluster_to_size`**

Build the MST once, run the loop over tree-edge subsets (NOT re-filtered pairs), partition pair_scores once at the end per final component. Reuse `_build_mst`, `UnionFind`, `compute_cluster_confidence`.

```python
def split_oversized_cluster_to_size(
    members: list[int],
    pair_scores: dict[tuple[int, int], float],
    max_size: int,
) -> list[dict]:
    """Split a cluster down to ``max_size`` from a SINGLE MST build (#661).

    Repeatedly cuts the weakest tree edge of any component still larger than
    ``max_size``. A sub-tree of a maximum spanning tree IS the maximum spanning
    tree of its induced sub-graph (cycle property), so cutting original tree
    edges reproduces the old per-component re-MST cut decisions (same membership
    partition, same first-minimum tie-break). Returns final sub-clusters in a
    DETERMINISTIC order (sort-by-min-member at each cut, oversized components
    re-enqueued LIFO) so the two live callers label consistently and run-to-run.

    Components that cannot be cut further (no remaining cuttable tree edge) are
    returned still oversized (``oversized=True``)."""
    if len(members) <= max_size or len(members) <= 1 or not pair_scores:
        size = len(members)
        return [{"members": sorted(members), "size": size,
                 "oversized": size > max_size, "pair_scores": pair_scores,
                 **_confidence_fields(pair_scores, size)}]

    # Build the MST ONCE. Native single-edge kernel is NOT used here (it does one
    # cut per call); the proven sub-tree property lets us do all cuts on the one
    # Python MST. split_oversized_cluster (single-edge) stays for its callers.
    tree_edges = _build_mst(members, pair_scores)
    if not tree_edges:
        size = len(members)
        return [{"members": sorted(members), "size": size,
                 "oversized": size > max_size, "pair_scores": pair_scores,
                 **_confidence_fields(pair_scores, size)}]

    # Loop mirrors _finalize_clusters:877-915. A "work item" = (member set, its
    # tree-edge list). Cut the weakest tree edge; the two resulting components'
    # tree edges are the partition of the remaining tree edges (the cut edge is
    # dropped). sort-by-min-member + LIFO re-enqueue reproduce creation order.
    out_order: list[tuple[frozenset[int], list]] = []
    work: list[tuple[set[int], list]] = [(set(members), list(tree_edges))]
    while work:
        node_set, edges = work.pop()
        if len(node_set) <= max_size or not edges:
            out_order.append((frozenset(node_set), edges))
            continue
        weakest = min(edges, key=lambda e: e[2])  # first-minimum, same as today
        remaining = [e for e in edges if e is not weakest]
        uf = UnionFind()
        uf.add_many(list(node_set))
        for a, b, _s in remaining:
            uf.union(a, b)
        comps = uf.get_clusters()           # 2 components
        # bucket remaining tree edges to their component
        rep_to_edges: dict[int, list] = {}
        node_to_rep = {n: uf.find(n) for n in node_set}
        for e in remaining:
            rep_to_edges.setdefault(node_to_rep[e[0]], []).append(e)
        sub_items = [(c, rep_to_edges.get(uf.find(next(iter(c))), [])) for c in comps]
        # creation order: sort by min member; oversized ones go back on the stack
        sub_items.sort(key=lambda ci: min(ci[0]))
        for c, ce in sub_items:
            if len(c) > max_size:
                work.append((set(c), ce))
            else:
                out_order.append((frozenset(c), ce))

    # Partition pair_scores once across the final components (single O(E) pass).
    final_sets = [s for s, _e in out_order]
    member_to_idx: dict[int, int] = {}
    for idx, s in enumerate(final_sets):
        for m in s:
            member_to_idx[m] = idx
    sub_pairs: list[dict] = [{} for _ in final_sets]
    for (a, b), sc in pair_scores.items():
        ia = member_to_idx.get(a)
        if ia is not None and ia == member_to_idx.get(b):
            sub_pairs[ia][(a, b)] = sc

    result = []
    for idx, s in enumerate(final_sets):
        sc_list = sorted(s)
        size = len(sc_list)
        result.append({
            "members": sc_list, "size": size, "oversized": size > max_size,
            "pair_scores": sub_pairs[idx],
            **_confidence_fields(sub_pairs[idx], size),
        })
    return result


def _confidence_fields(pair_scores: dict, size: int) -> dict:
    conf = compute_cluster_confidence(pair_scores, size)
    return {"confidence": conf["confidence"], "bottleneck_pair": conf["bottleneck_pair"]}
```

> NOTE for implementer: `_DEFAULT_SPLIT_EDGE_WORK_BUDGET` (`:34`) and `compute_cluster_confidence` (`:1211`) are retained — do not delete them. The batch function MUST route confidence through `compute_cluster_confidence` (no alternate recompute), or the EXACT-float assertion in `test_columnar_drop_pairscores_byte_identical` breaks. The membership-equivalence test (Step 1) is the binding partition lock; if it fails, the cut decisions diverged — do NOT "fix" by changing tie-break or enqueue order to chase it, debug the MST sub-tree handling.

- [ ] **Step 4: Run the membership-equivalence test — expect PASS (native=0 and native=1)**

Run: `D:/show_case/gm-661/.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_cluster_split_to_size.py::test_batch_split_membership_matches_old -v`
Expected: PASS for both `native` params.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/cluster.py packages/python/goldenmatch/tests/test_cluster_split_to_size.py
git commit -m "feat(cluster): single-MST batch split_oversized_cluster_to_size (#661)"
```

---

### Task 2: Wire both split loops to the batch function (once per top-level cluster)

Replace the per-pass re-MST + re-enqueue in `_finalize_clusters` (`:877-915`) and `build_cluster_frames` (`:588-669`) with a single `split_oversized_cluster_to_size` call per top-level oversized cluster, charging `edge_work` once per top-level cluster. The global `next_cid` labeling stays in the caller, assigned over the batch function's returned order.

**Label-parity requirement (BOTH callers MUST match each other — gated by `test_columnar_drop_pairscores_byte_identical`):**
- Iterate top-level oversized clusters in a SINGLE fixed order in BOTH callers. The old `_finalize_clusters` drained `to_split` LIFO; to keep new-dict == new-columnar, pick ONE order and use it in both — recommended: iterate `sorted(oversized_cids)` ascending (simplest, deterministic, identical in both callers). Whatever you choose, it MUST be the same in `_finalize_clusters` and `build_cluster_frames`.
- For each top-level oversized cluster, after `subs = split_oversized_cluster_to_size(...)`, assign sequential cids to `subs` IN THE ORDER THE BATCH FUNCTION RETURNS THEM (it is already sorted-by-min-member deterministic), starting at `max(live_cids, default=0) + 1`. Do this identically in both callers.
- The parity gate (Task 3) is the proof these two paths agree; if it fails, the two callers' iteration/labeling diverged.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/cluster.py:877-915` (`_finalize_clusters` split loop; warning `:916-923`)
- Modify: `packages/python/goldenmatch/goldenmatch/core/cluster.py:588-669` (`build_cluster_frames` inline loop)

- [ ] **Step 1: Rewrite `_finalize_clusters` split loop**

Build `oversized_cids = sorted(cid for cid, c in result.items() if c["oversized"])` (when `auto_split`). For each cid in that order: materialize its `pair_scores` (dict path already has them; columnar path materializes from `raw_pairs` as today), charge `edge_work += len(pair_scores)` ONCE, check budget (break, leaving this + remaining cids oversized in `result`), then `subs = split_oversized_cluster_to_size(cinfo["members"], cinfo["pair_scores"], max_cluster_size)`. If `len(subs) <= 1` leave intact (`cinfo["oversized"] = cinfo["size"] > max_cluster_size`; keep in `result`). Else `result.pop(cid)`, then `next_cid = max(result.keys(), default=0) + 1` and emit each sub in returned order: `sc["_was_split"] = True`, `result[next_cid] = sc`, `next_cid += 1`. NO re-enqueue — the batch function already split fully; a sub returned `oversized=True` (uncuttable dense blob) stays flagged and is NOT re-enqueued.

- [ ] **Step 2: Rewrite `build_cluster_frames` inline loop (`:588-669`) the same way**

Same shape and SAME top-level order (`sorted(oversized)`): per top-level oversized cid, filter `ps` once, charge once, call the batch function, then label sequentially via `next_cid` over `live_cids` in the batch function's returned order. Preserve `quality="split"`, the `drop_cids` set, and the materialize-rows-at-end structure. A returned-oversized sub stays in `split_result` flagged (its metadata row carries `oversized=True`). Drop the inner `while to_split` re-enqueue (the batch function owns the full split now).

- [ ] **Step 3: Run the byte-identical parity test (the hard backstop)**

Run: `D:/show_case/gm-661/.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_columnar_drop_pairscores_parity.py -v`
Expected: PASS (native 1 and 0). This asserts cluster ids + all fields byte-identical across paths after the rewrite.

- [ ] **Step 4: Run the columnar cluster-build + pipeline parity suites**

Run: `D:/show_case/gm-661/.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_columnar_cluster_build_parity.py packages/python/goldenmatch/tests/test_columnar_pipeline_parity.py packages/python/goldenmatch/tests/test_native_parity.py -v`
Expected: PASS — partition + labels + native-vs-python all unchanged.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/cluster.py
git commit -m "refactor(cluster): both split loops call batch split once per top-level cluster (#661)"
```

---

### Task 3: Add ≥2-oversized-top-level + ≥3-component fixtures to the byte-identical parity test

The existing `_adversarial_pairs` has a dense clique that can't split and a barbell that splits once. Add (a) a cluster that splits into ≥3 components, and (b) a SECOND oversized top-level cluster, so the parity gate exercises the batch loop's repeated cuts AND the two callers' top-level iteration/labeling order agreement (the Task 2 label-parity requirement).

**Files:**
- Modify: `packages/python/goldenmatch/tests/test_columnar_drop_pairscores_parity.py` (`_adversarial_pairs`)

- [ ] **Step 1: Add two new oversized groups to `_adversarial_pairs`**

First check the `max_cluster_size` the test passes to `build_clusters` (read the test body; the parametrized call sets it — likely 5). Add, sized to exceed it:
- Group A (nodes 40..48): three dense cliques `{40,41,42}`, `{43,44,45}`, `{46,47,48}` joined by weak bridges `(42,43)=0.30`, `(45,46)=0.25` — splits into 3.
- Group B (nodes 50..57): two dense cliques `{50,51,52,53}`, `{54,55,56,57}` joined by weak bridge `(53,54)=0.28` — a SECOND oversized top-level cluster that splits into 2.

Append `list(range(40,49)) + list(range(50,58))` to `all_ids`. Two oversized top-level clusters means the test now catches any divergence in top-level iteration order / cid assignment between the dict and columnar paths.

- [ ] **Step 2: Run it — expect PASS (columnar == dict, native 1 and 0)**

Run: `D:/show_case/gm-661/.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_columnar_drop_pairscores_parity.py -v`
Expected: PASS — the 3-way split has byte-identical members/ids/confidence across paths.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/tests/test_columnar_drop_pairscores_parity.py
git commit -m "test(cluster): dense 3-component split fixture in byte-identical parity gate (#661)"
```

---

### Task 4: Perf guard — MST built once per top-level oversized cluster (#661)

Lock the #661 fix: on a dense cluster that peels into many components, `_build_mst` must be called ONCE per top-level oversized cluster, not once per cut.

**Files:**
- Test: `packages/python/goldenmatch/tests/test_cluster_split_to_size.py` (extend)

- [ ] **Step 1: Write the perf-invariant test (failing if loop re-MSTs)**

```python
def test_single_mst_build_per_top_level_cluster(monkeypatch):
    """#661: a dense cluster peeling into k components builds the MST ONCE,
    not k times. Instrument _build_mst call count."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    import goldenmatch.core.cluster as cl
    calls = {"n": 0}
    orig = cl._build_mst
    monkeypatch.setattr(cl, "_build_mst",
                        lambda m, ps: (calls.__setitem__("n", calls["n"] + 1), orig(m, ps))[1])
    members = list(range(100, 130))           # 30-node dense-ish cluster
    ps = {(a, b): 0.99 for i, a in enumerate(members) for b in members[i + 1:]}
    # add a few weak bridges so it actually peels into several components
    subs = cl.split_oversized_cluster_to_size(members, ps, max_size=5)
    assert calls["n"] == 1                     # ONE MST build, not O(k)
    assert all(s["size"] <= 5 or s["oversized"] for s in subs)


def test_caller_invokes_batch_once_per_top_level_cluster(monkeypatch):
    """#661: the build path calls split_oversized_cluster_to_size exactly once
    per oversized TOP-LEVEL cluster (no per-pass re-call)."""
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    import goldenmatch.core.cluster as cl
    calls = {"n": 0}
    orig = cl.split_oversized_cluster_to_size
    monkeypatch.setattr(cl, "split_oversized_cluster_to_size",
                        lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), orig(*a, **k))[1])
    # ONE oversized top-level cluster (15-node clique) -> exactly ONE batch call.
    members = list(range(100, 115))
    pairs = [(a, b, 0.99) for i, a in enumerate(members) for b in members[i + 1:]]
    cl.build_clusters(pairs, all_ids=members, max_cluster_size=5)
    assert calls["n"] == 1
```

- [ ] **Step 2: Run both — expect PASS**

Run: `D:/show_case/gm-661/.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_cluster_split_to_size.py -k "single_mst_build or caller_invokes" -v`
Expected: PASS — one `_build_mst` per batch call, one batch call per top-level oversized cluster.

- [ ] **Step 3: Commit**

```bash
git add packages/python/goldenmatch/tests/test_cluster_split_to_size.py
git commit -m "test(cluster): assert single MST build + one batch call per oversized cluster (#661)"
```

---

### Task 5: Budget scaling + config field (#726)

`_split_edge_work_budget` becomes `_split_edge_work_budget(n_rows, override=None)` with precedence **override (config field) > `GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET` env > `max(5_000_000, n_rows * C)`** (C=5). Thread `split_edge_budget` + `n_rows` through the build functions.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/cluster.py:37-45` (`_split_edge_work_budget`)
- Modify: `packages/python/goldenmatch/goldenmatch/config/schemas.py:443-446` (`GoldenRulesConfig`)
- Modify: `packages/python/goldenmatch/goldenmatch/core/cluster.py` build-fn signatures + `_finalize_clusters` calls
- Modify: `packages/python/goldenmatch/goldenmatch/core/pipeline.py:1455-1524`
- Test: `packages/python/goldenmatch/tests/test_cluster_split_to_size.py` (extend)

- [ ] **Step 1: Write budget tests (failing)**

```python
def test_budget_autoscales_with_n_rows():
    from goldenmatch.core.cluster import _split_edge_work_budget
    assert _split_edge_work_budget(1000) == 5_000_000           # floor
    assert _split_edge_work_budget(2_000_000) == 10_000_000     # n_rows * 5

def test_budget_env_overrides_autoscale(monkeypatch):
    from goldenmatch.core.cluster import _split_edge_work_budget
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET", "777")
    assert _split_edge_work_budget(2_000_000) == 777

def test_budget_config_override_beats_env(monkeypatch):
    from goldenmatch.core.cluster import _split_edge_work_budget
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET", "777")
    assert _split_edge_work_budget(2_000_000, override=12345) == 12345
```

- [ ] **Step 2: Run — expect FAIL (signature takes no args)**

Run: `D:/show_case/gm-661/.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_cluster_split_to_size.py -k budget -v`
Expected: FAIL — `TypeError: _split_edge_work_budget() takes 0 positional arguments`.

- [ ] **Step 3: Implement the new budget function**

```python
_SPLIT_EDGE_BUDGET_PER_ROW = 5  # C: linear edge-work allowance per input row

def _split_edge_work_budget(n_rows: int, override: int | None = None) -> int:
    """Cumulative-edge-work cap for the auto-split loop.

    Precedence: explicit ``override`` (GoldenRulesConfig.split_edge_budget) >
    GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET env > max(5M, n_rows * C). With the
    single-MST batch split (#661) exhaustion is rare; this makes the rare case
    scale-appropriate and tunable."""
    if override is not None:
        return max(1, int(override))
    raw = os.environ.get("GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET")
    if raw is not None:
        try:
            return max(1, int(raw))
        except ValueError:
            pass
    return max(_DEFAULT_SPLIT_EDGE_WORK_BUDGET, int(n_rows) * _SPLIT_EDGE_BUDGET_PER_ROW)
```

- [ ] **Step 4: Add `split_edge_budget` to `GoldenRulesConfig`**

In `schemas.py` after `weak_cluster_threshold` (`:446`):
```python
    # #726: cap on cumulative auto-split edge-work. None => auto-scaled
    # max(5_000_000, n_rows * 5). Raise this (or set
    # GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET) if a loud "clusters left oversized"
    # warning fires on a legitimately dense dataset. Precedence: this field >
    # env > auto-scaled.
    split_edge_budget: int | None = None
```

- [ ] **Step 5: Thread `n_rows` + `split_edge_budget` through the FULL dispatch chain**

`build_clusters` does NOT call `_finalize_clusters` directly — it dispatches to `_build_clusters_dict_path` / `_build_clusters_via_frames`. Thread through every hop or the field silently collapses to the 5M floor (`n_rows=0`):
- `build_clusters` (`:361`): add keyword `split_edge_budget: int | None = None`; pass it on BOTH dispatch calls — `_build_clusters_via_frames(..., split_edge_budget=split_edge_budget)` (`:427`) and `_build_clusters_dict_path(..., split_edge_budget=split_edge_budget)` (`:431`).
- `_build_clusters_dict_path` (`:756`): add keyword `split_edge_budget: int | None = None`; at the `_finalize_clusters` call (`:831`) pass `n_rows=len(all_ids), split_edge_budget=split_edge_budget`.
- `_build_clusters_via_frames` (`:967`): add keyword `split_edge_budget: int | None = None`; at the `_finalize_clusters` call (`:1044`) pass `n_rows=len(all_ids), split_edge_budget=split_edge_budget`.
- `_finalize_clusters` (`:836`): add keyword params `n_rows: int = 0` and `split_edge_budget: int | None = None`; change `edge_budget = _split_edge_work_budget()` (`:875`) → `_split_edge_work_budget(n_rows, split_edge_budget)`.
- `build_cluster_frames` (`:463`): add keyword `split_edge_budget: int | None = None`; in its inline loop `edge_budget = _split_edge_work_budget(len(all_ids), split_edge_budget)` (`all_ids` is in scope, derived at `:494`).
- `build_clusters_columnar` (`:1568`): add keyword `split_edge_budget: int | None = None`; forward it on the `build_clusters(...)` call (`:1617`).
- `pipeline.py`: in the unpack block (`:1455-1461`) read `split_edge_budget = getattr(config.golden_rules, "split_edge_budget", None)`; pass `split_edge_budget=split_edge_budget` to the three build call sites (`:1497` columnar, `:1504` frames, `:1519` dict).
- Do NOT thread it into `build_clusters_v2_columnar` / `build_clusters_arrow_native` (off the pipeline cluster-step path; auto-scale default is correct there).

- [ ] **Step 6: Run budget tests + a full-pipeline smoke**

Run: `D:/show_case/gm-661/.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_cluster_split_to_size.py -k budget packages/python/goldenmatch/tests/test_columnar_drop_pairscores_parity.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/cluster.py packages/python/goldenmatch/goldenmatch/config/schemas.py packages/python/goldenmatch/goldenmatch/core/pipeline.py packages/python/goldenmatch/tests/test_cluster_split_to_size.py
git commit -m "feat(cluster): scale + config-tune auto-split edge budget (#726)"
```

---

### Task 6: Loud, non-silent budget-exhaustion failure (#726 DX)

The clusters are already KEPT and flagged oversized today; the gap is the warning doesn't name the knob. Upgrade both warnings (`_finalize_clusters:916-923` and `build_cluster_frames:638-640`) to name the count + the exact knobs (config field + env). No exception, no silent drop.

**Files:**
- Modify: `packages/python/goldenmatch/goldenmatch/core/cluster.py` (both `_clog.warning` call sites)
- Test: `packages/python/goldenmatch/tests/test_cluster_split_to_size.py` (extend)

- [ ] **Step 1: Write the failure-mode test (failing)**

```python
def test_budget_exhaustion_warns_and_keeps_clusters(monkeypatch, caplog):
    """#726: tiny budget + a dense cluster -> WARNING names the knob AND the
    oversized clusters stay in the output, flagged (NOT dropped)."""
    import logging
    monkeypatch.setenv("GOLDENMATCH_NATIVE", "0")
    monkeypatch.setenv("GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET", "1")  # exhaust immediately
    from goldenmatch.core.cluster import build_clusters
    members = list(range(200, 215))
    pairs = [(a, b, 0.99) for i, a in enumerate(members) for b in members[i + 1:]]
    with caplog.at_level(logging.WARNING, logger="goldenmatch.cluster"):
        clusters = build_clusters(pairs, all_ids=members, max_cluster_size=5)
    # cluster(s) present and flagged oversized — not silently dropped
    assert any(c["oversized"] for c in clusters.values())
    assert sum(c["size"] for c in clusters.values()) == len(members)
    msg = " ".join(r.message for r in caplog.records)
    assert "split_edge_budget" in msg and "GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET" in msg
```

- [ ] **Step 2: Run — expect FAIL on the knob-name assertion**

Run: `D:/show_case/gm-661/.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_cluster_split_to_size.py::test_budget_exhaustion_warns_and_keeps_clusters -v`
Expected: FAIL — current warning lacks `split_edge_budget` / env name.

- [ ] **Step 3: Upgrade both warnings**

```python
_clog.warning(
    "build_clusters: auto-split edge-work budget (%d) exhausted; %d cluster(s) "
    "left OVERSIZED and flagged (kept in output, excluded from golden "
    "downstream). Raise GoldenRulesConfig.split_edge_budget or env "
    "GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET to split them.",
    edge_budget, n_oversized,
)
```
(and the `build_cluster_frames` twin, with its `n_oversized` count computed from the metadata frame.)

- [ ] **Step 4: Run — expect PASS**

Run: `D:/show_case/gm-661/.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_cluster_split_to_size.py::test_budget_exhaustion_warns_and_keeps_clusters -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add packages/python/goldenmatch/goldenmatch/core/cluster.py packages/python/goldenmatch/tests/test_cluster_split_to_size.py
git commit -m "feat(cluster): loud budget-exhaustion warning names the knob, keeps clusters flagged (#726)"
```

---

### Task 7: Targeted regression run, dense-pathology check, PR

**Files:** none (verification + PR)

- [ ] **Step 1: Run the cluster + golden + pipeline test files locally**

Run: `D:/show_case/gm-661/.venv/Scripts/python.exe -m pytest packages/python/goldenmatch/tests/test_cluster_split_to_size.py packages/python/goldenmatch/tests/test_columnar_drop_pairscores_parity.py packages/python/goldenmatch/tests/test_columnar_cluster_build_parity.py packages/python/goldenmatch/tests/test_columnar_pipeline_parity.py packages/python/goldenmatch/tests/test_native_parity.py -v`
Expected: ALL PASS. (Per memory `feedback_avoid_full_suite_oom`: run targeted files locally; the FULL suite runs in GitHub Actions, never locally.)

- [ ] **Step 2: Verify the known dense-split pathology no longer hangs**

The `project_build_clusters_dense_split_pathology` fixture (auto-split peeling 1 node/pass on a large dense cluster) should now finish from one MST build. If a dedicated fixture exists, run it; otherwise the Task 4 perf test + Task 6 dense fixture cover the regression. Confirm no test exceeds the CI `--timeout=120`.

- [ ] **Step 3: Push branch + open PR (auth dance per memory)**

```bash
git push -u origin feat/661-726-cluster-split
# gh auth switch --user benzsevern  (benseverndev-oss uses personal account)
# gh pr create against main; body references #661 + #726
```
PR body: single-MST batch split (#661 root), auto-scaled+config edge budget (#726), loud-warn-keep-flagged failure mode (#726). Note byte-identical clustering invariant held (parity gates green). Switch auth back to `benzsevern-mjh` after push.

- [ ] **Step 4: Poll CI to green; ensure `python (goldenmatch)` + `synthetic_benchmarks` (#528) + `ci-required` pass**

Use the poll loop from CLAUDE.md: `while gh pr checks <N> | grep -qE "pending|in_progress"; do sleep 30; done`. The #528 in-house quality gate is the co-clustering invariance backstop — it MUST be green (any drop means the split partition changed).

- [ ] **Step 5: Merge on green (squash, delete branch), close #661 + #726**

---

## Risks (carried from spec)
- **Component 1 silently changes co-clustering** (primary). Backstops: the cycle-property proof above, the membership-equivalence test against the real single-edge path (Task 1), the columnar/dict parity gate + new ≥2-oversized / ≥3-component fixtures (Tasks 2-3), and the #528 quality gate (Task 7).
- **The two live callers (dict vs columnar/frames) assign DIFFERENT cluster-id labels for split clusters.** Mitigation: identical top-level iteration order (`sorted(oversized)`) + identical sequential labeling over the batch function's deterministic return order in both callers; `test_columnar_drop_pairscores_byte_identical` (extended with two oversized top-level clusters) is the proof. Note: label equality vs the PRE-#661 release is NOT a requirement (entity_ids are content-based fingerprints, not cluster_id; run-to-run determinism + cross-path parity + membership are the binding invariants).
- **Native/Python divergence.** Mitigation: batch path is pure Python; `test_split_oversized_cluster_parity` keeps the single-edge native kernel honest; the parity gate runs native 1 and 0.
- **Genuinely huge dense cluster still expensive.** Mitigation: one MST build bounds the cost; the scaled budget + loud-warn-and-keep handle the residual pathological case without a hang.
