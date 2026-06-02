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

New internal path `_build_clusters_columnar_core(pairs_df, all_ids, max_cluster_size,
weak_cluster_threshold, auto_split) -> dict[int, dict]`. `build_clusters` keeps its
signature and dispatches to it behind the gate; OFF -> the current path verbatim.

1. **Arrow Union-Find** -> `ClusterFrames` (assignments `cluster_id <-> member_id`;
   metadata `size`, `oversized`). The 2.4-3.1x kernel (`build_clusters_arrow_native`,
   already built; falls back to `build_clusters_v2_columnar` off-native).
2. **Columnar `pair_scores`** -- join `pairs_df` to the member->cluster assignment
   (from ClusterFrames.assignments) to get a per-cluster edge frame
   `(cluster_id, id_a, id_b, score)`. Canonical `(min,max)` orientation already
   holds upstream. Replaces the Python loop at :468-471.
3. **Columnar confidence + bottleneck** -- group-by `cluster_id` aggregations over
   the edge frame, reproducing `compute_cluster_confidence` EXACTLY:
   - size <= 1: `connectivity=1.0, confidence=1.0`, `min_edge/avg_edge/bottleneck_pair=None`.
   - else: `min_edge=min(score)`, `avg_edge=mean(score)`,
     `connectivity = n_edges / (size*(size-1)/2)`,
     `bottleneck_pair = (id_a,id_b)` of the argmin-score edge,
     `confidence = 0.4*min_edge + 0.3*avg_edge + 0.3*connectivity`.
   **Bottleneck tie-break is the parity crux:** the dict path scans `pair_scores.items()`
   and the FIRST minimum wins (insertion order = the order pairs were filled =
   `pairs` iteration order). The columnar argmin MUST reproduce that exact
   first-occurrence-by-pair-order tie-break (e.g. stable sort by `(score, original
   pair index)` then take first per cluster). A different tie-break moves
   `bottleneck_pair` on score-tied clusters -> parity fail.
4. **Auto-split (UNCHANGED, oversized-only)** -- for clusters with
   `size > max_cluster_size` (when `auto_split`), materialize that cluster's
   `members` + `pair_scores` DICT and call the existing
   `split_oversized_cluster(members, pair_scores)` under the SAME edge-work-budget
   + no-progress guards (:494-528, the #661 dense-cluster pathology guard).
   Oversized clusters are rare, so the per-oversized dict materialization is cheap.
   Split sub-clusters get new ids, `_was_split=True`, recompute `oversized` per
   sub-cluster size; re-enqueue still-oversized subs (same loop as today).
5. **Columnar `cluster_quality`** -- `split` if `_was_split`; else `weak` (with
   `confidence *= 0.7`) when `size > 1 and pair_scores and avg_edge - min_edge >
   weak_cluster_threshold`; else `strong`. Vectorizable for the non-split clusters;
   split flag comes from step 4.
6. **dict adapter** -- materialize `dict[int, dict]` byte-identical to the current
   output: per cluster `{members: list[int], size, oversized, pair_scores:
   dict[(a,b)]->score, confidence, bottleneck_pair, cluster_quality}` (and the
   transient `_was_split` where the current code sets it). Cluster id numbering +
   ordering must match the current path (clusters sorted by `min(members)`,
   id-anchored, start=1 -- see :436-450; the Arrow UF membership is identical, so
   apply the same sort+enumerate to assign ids).

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
- **Off-native:** Arrow UF degrades to `build_clusters_v2_columnar`; the columnar
  post-processing is pure Polars (no native dependency) so it still runs; the dict
  adapter output is identical. (Confidence has a native `cluster_confidence` kernel
  used by the dict path; the columnar path computes the SAME formula in Polars --
  the parity gate covers both-native-states.)

## Testing

- **Byte-identical dict gate (HARD):** an adversarial cluster fixture --
  singletons, multi-member, a fully-connected cluster, a weak chain (triggers
  `weak`), an oversized cluster that splits (triggers `split` + new ids), an
  oversized cluster that CAN'T split (budget/no-progress -> left oversized),
  score-tied edges (bottleneck tie-break), and a cluster with duplicate canonical
  pairs. Assert `build_clusters(pairs, ...)` with the gate ON `==` the gate-OFF
  output, key for key, including `pair_scores` dicts, `confidence` (exact float),
  `bottleneck_pair`, `cluster_quality`, `oversized`, and the cluster id numbering.
  Run with `GOLDENMATCH_NATIVE` on AND off.
- **Confidence parity unit test:** the columnar confidence aggregation matches
  `compute_cluster_confidence` for each fixture cluster, incl. the size<=1 case and
  a score-tie bottleneck.
- **Measure-first bench:** columnar-core+adapter vs current dict `build_clusters` at
  1M/5M pairs on `large-new-64GB` (fresh native), wall + peak RSS, with the
  membership/dict parity asserted. Reuse the spike's pair generator
  (`scripts/bench_build_clusters_arrow_spike.py`). Records whether SP1's columnar
  post-processing beats the Python loops net of the dict adapter; ship default-on
  only if it wins, else keep gated as the SP2-enabling foundation.

## Scope boundary (YAGNI)

- ONLY `core/cluster.py` (`build_clusters` internals + the columnar core + adapter
  + gate). NO consumer changes (SP2+). NO new auto-split algorithm (reuse
  `split_oversized_cluster`). NO new native kernel (Arrow UF + cluster_confidence
  exist). Don't change the `ClusterFrames` schema.

## References

- #663-B / #624 (Phase 2). Roadmap `docs/superpowers/specs/2026-05-31-arrow-native-roadmap.md`.
- `core/cluster.py`: `build_clusters` (:361-541), `compute_cluster_confidence`
  (:552), `build_clusters_arrow_native` (:1216), `build_clusters_v2_columnar`
  (:1162), `ClusterFrames` (:1009), `split_oversized_cluster`. Spike:
  `scripts/bench_build_clusters_arrow_spike.py` + `bench-build-clusters-arrow-spike.yml`.
- Related: [[project_663_arrow_kernels]], [[project_build_clusters_dense_split_pathology]] (#661),
  [[project_arrow_native_finish_line]].
