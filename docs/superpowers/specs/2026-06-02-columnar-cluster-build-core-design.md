# Columnar cluster-build core + dict adapter (Phase 2 SP1) — design

**Date:** 2026-06-02
**Status:** design (approved, pre-plan)
**Decision context:** #663-B measure-first spike (run 26794944441) found the Arrow
cluster UF kernel `build_clusters_arrow_native` is 2.4-3.1x faster than the
dict-shaped `build_clusters` with byte-identical membership -- but that was
UF-only vs UF+post-processing. The dict path's ~1.4s/1M is the Python
`pair_scores` fill + per-cluster `compute_cluster_confidence`. Realizing the win
needs the post-processing columnar too (Phase 2 / #624 "cluster representation
columnar"). Phase 2 decomposes into SP1 (THIS spec: columnar build core + a
`dict[int,dict]` adapter so the ~25 cluster consumers stay untouched) and SP2..N
(incremental consumer migration off the adapter). Clustering is ~32% of wall at
25M, so the full win is high-leverage.

## Problem

`core/cluster.py::build_clusters(pairs) -> dict[int, dict]` (the production path,
12 call sites, ~25 consumers) does, after the Union-Find:
- `pair_scores` fill: a Python loop assigning every pair's score to its cluster's
  `pair_scores` dict (cluster.py:468-471);
- per-cluster `compute_cluster_confidence` (:477-481);
- auto-split of oversized clusters via MST (:494-528);
- `cluster_quality` weak/split assignment (:531-541).

The Arrow UF (`build_clusters_arrow_native -> ClusterFrames`) only does the
Union-Find; the post-processing is the dict-floor cost. SP1 makes the WHOLE build
columnar while preserving the exact `dict[int,dict]` output via an adapter.

## Goal

`build_clusters` produces a byte-identical `dict[int,dict]` via a columnar core
(Arrow UF + columnar `pair_scores`/confidence/quality, auto-split unchanged) +
a `ClusterFrames -> dict` adapter, behind a gate, with the columnar path measured
against the current one. No consumer changes (SP2+).

## Architecture: columnar core, dict adapter, gated

New internal path `_build_clusters_via_frames(pairs_df, all_ids, max_cluster_size,
weak_cluster_threshold, auto_split) -> dict[int, dict]` (name chosen to NOT collide
with the existing `build_clusters_columnar` :891 [df->list->build_clusters] and
`build_clusters_v2_columnar` :1162 -- both different; wire the gate into NEITHER).
`build_clusters` keeps its signature and dispatches to it behind the gate; OFF ->
the current path verbatim. **The gate is read only on the in-memory list/DataFrame
branch -- AFTER the Ray-Dataset short-circuit at the top of `build_clusters`
(:376-394), so it never intercepts the distributed path.**

1. **Arrow Union-Find** -> `ClusterFrames` (assignments `cluster_id <-> member_id`;
   metadata `size`, `oversized`). The 2.4-3.1x kernel (`build_clusters_arrow_native`,
   already built; falls back to `build_clusters_v2_columnar` off-native).
2. **`pair_scores` (ORDER-PRESERVING, no join)** -- map `cluster_id` onto
   `pairs_df` via `pl.col("id_a").replace_strict(member_to_cid)` in a
   `with_columns` (NOT a Polars `join` -- a join does not preserve left-frame row
   order, which would silently break the sequential-sum + bottleneck tie-break
   parity). Both endpoints share a cluster post-UF, so keying on `id_a` matches the
   dict path (:470) and the Rust kernel (cluster.rs:482). The result is a flat
   `(cluster_id, id_a, id_b, score)` frame IN PAIR-FILL ORDER. The adapter
   materializes per-cluster `pair_scores` dicts from it (order preserved).
3. **Confidence + bottleneck -- REUSE the batched confidence the Arrow kernel
   ALREADY emits (no new kernel; decision 2026-06-02 refined by spec-review).**
   `compute_cluster_confidence`'s `avg_edge = sum/len` is a SEQUENTIAL left-fold;
   a vectorized Polars `group_by().mean()` (SIMD/pairwise) differs by ~1e-13 and
   breaks byte-identical. The fix does NOT need a new kernel: the EXISTING
   `build_clusters_arrow` Rust kernel (cluster.rs:392-548) ALREADY computes
   per-cluster confidence + bottleneck in Rust, in pair-INPUT order, via the same
   `cluster_confidence` logic (cluster.rs:478-486 buckets edges per cluster by a
   single-pass push -- NOT a group_by/sort -- then folds), and returns them on
   `ClusterFrames.metadata` (`confidence`, `bottleneck_pair_a/b`). That IS the
   batched-native, bit-identical confidence we want.
   - **Native path:** take `confidence` + `bottleneck_pair` straight off the
     `ClusterFrames.metadata` that step 1's `build_clusters_arrow_native` already
     produced. No second computation, no new kernel.
   - **Off-native path:** `build_clusters_arrow_native` falls back to
     `build_clusters_v2_columnar`; compute confidence via the existing per-cluster
     `compute_cluster_confidence` loop (Python, bit-identical; off-native is the
     non-perf path). STRICT byte-identical in BOTH states; no float tolerance.
   - `bottleneck_pair` sentinel: `ClusterFrames.metadata` carries `(0,0)` for
     "no bottleneck"; the adapter maps `(0,0) <-> None`. No real edge is `(0,0)`
     (a pair has two distinct ids), so it is collision-free.
   - **Verify at plan time:** that the metadata `confidence`/`bottleneck` from
     `build_clusters_arrow` are bit-identical to the dict path on the parity
     fixture (the kernel uses the same `cluster_confidence` in input order, so they
     should be -- the parity gate proves it). If a gap is found, the fallback is a
     `cluster_confidence_batch` kernel, but reusing the existing metadata is the
     first choice (no new native surface).
4. **Auto-split (UNCHANGED, oversized-only)** -- for clusters with
   `size > max_cluster_size` (when `auto_split`), materialize that cluster's
   `members` + `pair_scores` DICT and call the existing
   `split_oversized_cluster(members, pair_scores)` under the SAME edge-work-budget
   + no-progress guards (:494-528, the #661 dense-cluster pathology guard).
   Oversized clusters are rare, so the per-oversized dict materialization is cheap.
   Split sub-clusters get new ids, `_was_split=True`, recompute `oversized` per
   sub-cluster size; re-enqueue still-oversized subs (same loop as today).
5. **`cluster_quality`** -- `split` if `_was_split` (from step 4); else `weak` (with
   `confidence *= 0.7`) when `size > 1 and pair_scores and avg_edge - min_edge >
   weak_cluster_threshold`; else `strong`. The current code RECOMPUTES `min_edge`/
   `avg_edge` here (:535-538) from `pair_scores.values()` -- same edge order, same
   sequential sum as step 3. REUSE the batch kernel's per-cluster `min_edge`/
   `avg_edge` for the weak test (bit-identical, avoids a second pass). Split
   clusters skip the weak branch (quality set to `split` first), matching today.
6. **dict adapter** -- materialize `dict[int, dict]` byte-identical to the current
   output: per cluster `{members: list[int], size, oversized, pair_scores:
   dict[(a,b)]->score, confidence, bottleneck_pair, cluster_quality}` (and the
   transient `_was_split` where the current code sets it). It persists ONLY
   `confidence` + `bottleneck_pair` from the confidence step -- do NOT add
   `min_edge`/`avg_edge`/`connectivity` keys (the current dict drops them; extra
   keys fail the key-for-key gate). `pair_scores` MUST be fully materialized (the
   existing `cluster_frames_to_dict` :1157 sets `pair_scores={}` -- do NOT reuse it;
   `_emit_cluster_profile` :548 reads `pair_scores`). Cluster id numbering +
   ordering must match the current path (clusters sorted by `min(members)`,
   id-anchored, start=1 -- see :436-450). **`members` is `list(members)` in the UF
   iteration order (deliberately UNSORTED, PR #598) -- derive it from the SAME UF
   output the dict path uses; do NOT re-group independently** (off-native the UF
   member order must come from the same source). The Arrow UF membership is
   identical, so apply the same sort-by-min-member + enumerate to assign ids.
7. **`_emit_cluster_profile`** (:548) is still called on the final adapter dict
   (reads size/confidence/members/pair_scores) -- unchanged.

### Gate
`GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` (default OFF until the measure-first bench),
read in `build_clusters`, mirroring the `GOLDENMATCH_NATIVE`/identity env pattern.

### Rejected
- **Per-cluster confidence in a loop on the columnar path:** keeps the Python
  per-cluster cost we're removing -- no post-processing win.
- **Migrate consumers now:** that's SP2+; SP1 keeps the dict adapter so the ~25
  consumers (golden/identity/lineage/unmerge/dashboard/compare/...; CLI/web/TUI/
  MCP/API/db) are untouched and the change is independently shippable.

## Edge cases / invariants

- **Cluster id parity:** ids must match the current sort-by-min-member + enumerate
  (start=1). The Arrow UF returns identical membership; re-apply the same sort.
- **Singletons / empty:** size<=1 -> confidence 1.0 path; empty pairs -> empty/all-
  singleton result, same as today.
- **Score-tie edges + bottleneck tie:** covered by step 3's first-occurrence rule.
- **Oversized + budget-tripped:** the auto-split budget/no-progress guards (#661)
  must behave identically -- leave oversized, warn, exclude from golden downstream.
- **Off-native:** Arrow UF degrades to `build_clusters_v2_columnar`; confidence
  falls back to the existing per-cluster `compute_cluster_confidence` sequential
  loop (bit-identical, no perf claim off-native -- the non-perf path). pair_scores
  grouping is pure Polars. The dict adapter output is byte-identical in BOTH native
  states; the parity gate runs both.
- **`id=0` record:** a singleton id `0` -> `bottleneck_pair=None` -> `(0,0)` in the
  frame -> back to `None` in the adapter. Collision-free; the fixture includes it.

## Testing

- **`members` parity = SET-equality (decision 2026-06-02, plan-review).** The
  columnar path runs a SEPARATE Union-Find (`build_clusters_arrow_native`) from the
  dict path's `connected_components`; two independent UF groupings iterate set/HashMap
  in DIFFERENT member order, and the dict path itself does NOT sort members (PR #598
  removed it for perf). So byte-identical `members` LIST order is unachievable
  without re-sorting both paths (re-incurring the #598 cost). The gate compares
  `members` as a SET (`frozenset`); EVERYTHING ELSE (`pair_scores`, `confidence`,
  `bottleneck_pair`, `cluster_quality`, `oversized`, cluster ids) stays STRICT
  byte-identical. Safe: every consumer treats members as a set / re-sorts, and the
  dict path's order is already hash-arbitrary.
- **Off-native UF sourcing (plan-review ISSUE 3):** off-native,
  `build_clusters_arrow_native` falls back to `build_clusters_v2_columnar` which runs
  the FULL `build_clusters` INCLUDING auto-split -> its frames are POST-split. The
  columnar path needs PRE-split UF membership. So off-native, source UF membership
  from `connected_components(pairs, all_ids)` (or the Python `UnionFind`) DIRECTLY --
  NOT `build_clusters_arrow_native` -- then columnar pair_scores + per-cluster
  `compute_cluster_confidence` + `_finalize_clusters` (which does the single
  auto-split). Native: `build_clusters_arrow_native` is UF-only (the kernel does NOT
  split, verified), so its assignments/metadata are pre-split -- use them.
- **Byte-identical dict gate (HARD, STRICT -- no float tolerance, members as SET):** an adversarial
  cluster fixture -- singletons (incl. id `0`), multi-member, a fully-connected
  cluster, a weak chain (triggers `weak`), an oversized cluster that splits
  (triggers `split` + new ids), an oversized cluster that CAN'T split
  (budget/no-progress -> left oversized), score-tied edges (bottleneck tie-break),
  a cluster with 3+ edges (the sequential-sum float case), and duplicate canonical
  pairs. Assert `build_clusters(pairs, ...)` with the gate ON `==` the gate-OFF
  output, key for key, including `pair_scores` dicts, `confidence` (EXACT float --
  the batch kernel's sequential sum makes this bit-identical), `bottleneck_pair`,
  `cluster_quality`, `oversized`, and cluster id numbering + `members` order. Run
  with `GOLDENMATCH_NATIVE` on AND off (off exercises the per-cluster fallback).
- **Confidence-batch parity unit test:** `cluster_confidence_batch` (native) matches
  the existing per-cluster `compute_cluster_confidence` bit-for-bit across the
  fixture clusters, incl. size<=1, a score-tie bottleneck, and a 3+-edge cluster
  (sequential-sum order). Also a native-parity test mirroring `test_native_parity.py`.
- **Measure-first bench:** columnar-core+adapter vs current dict `build_clusters` at
  1M/5M pairs on `large-new-64GB` (fresh native), wall + peak RSS, with the
  membership/dict parity asserted. Reuse the spike's pair generator
  (`scripts/bench_build_clusters_arrow_spike.py`). Records whether SP1's columnar
  post-processing beats the Python loops net of the dict adapter; ship default-on
  only if it wins, else keep gated as the SP2-enabling foundation.

## Scope boundary (YAGNI)

- ONLY `core/cluster.py` (`build_clusters` internals + the `_build_clusters_via_frames`
  columnar core + dict adapter + gate). **NO new native kernel** -- the existing
  `build_clusters_arrow` already emits batched bit-identical confidence/bottleneck
  on `ClusterFrames.metadata`; reuse it. NO consumer changes (SP2+). NO new
  auto-split algorithm (reuse `split_oversized_cluster`). Reuse the existing Arrow
  UF (`build_clusters_arrow_native`). Don't change the `ClusterFrames` schema. (If
  plan-time parity testing finds the metadata confidence is NOT bit-identical, the
  fallback is a `cluster_confidence_batch` kernel -- but reusing existing infra is
  the design.)

## References

- #663-B / #624 (Phase 2). Roadmap `docs/superpowers/specs/2026-05-31-arrow-native-roadmap.md`.
- `core/cluster.py`: `build_clusters` (:361-541), `compute_cluster_confidence`
  (:552), `build_clusters_arrow_native` (:1216), `build_clusters_v2_columnar`
  (:1162), `ClusterFrames` (:1009), `split_oversized_cluster`. Spike:
  `scripts/bench_build_clusters_arrow_spike.py` + `bench-build-clusters-arrow-spike.yml`.
- Related: [[project_663_arrow_kernels]], [[project_build_clusters_dense_split_pathology]] (#661),
  [[project_arrow_native_finish_line]].
