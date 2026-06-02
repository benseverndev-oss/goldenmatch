# Decouple DedupeResult.scored_pairs from cluster pair_scores (Phase 2 SP3) — design

**Date:** 2026-06-02
**Status:** design (approved by user, pre-spec-review)
**Decision context:** Phase-2 SP1 (#673, gated columnar build) + SP2 (#679,
`ClusterPairScores` view + identity migration) shipped. The remaining goal is a
future SP4 that makes `build_clusters` emit a columnar pair frame natively and
DROP the per-cluster `pair_scores` dict (where the 2.4-3.1x lands). A blocker
trace found that dropping `pair_scores` from the build's returned dict would break
several consumers, most of which only want the flat scored-pair list and
reconstruct it from cluster `pair_scores` via `_api.py::_extract_pairs`. SP3
removes blockers #2-#4 (the `scored_pairs`-reconstruction family) by sourcing
`scored_pairs` from the pipeline's pre-cluster scored-pair stream instead. #1
(unmerge, post-hoc) and #5 (distributed build) are deferred.

## Blocker trace (for reference)

Reads of `clusters[cid]["pair_scores"]` that would break if the build dropped it:
1. `core/cluster.py:994` `unmerge_record` — re-clusters from pair_scores (direct
   subscript -> KeyError). POST-HOC. **Deferred (not SP3).**
2. `_api.py::_extract_pairs` (`:1135`, used at `:300`/`:473`) — builds
   `DedupeResult.scored_pairs`. **SP3 fixes.**
3. `cli/label.py:43-45` — sole source of candidate pairs for `goldenmatch label`.
   **SP3 fixes.**
4. `web/routers/run.py:127` + `web/preview.py:186` — rebuild scored_pairs to feed
   `build_lineage`. **SP3 fixes.**
5. `distributed/clustering.py` — part of the distributed build itself. **Deferred.**
Graceful degraders (NOT blockers, unchanged by SP3): `tui/tabs/matches_tab.py`,
`core/explain.py::explain_cluster_nl`, `core/cluster.py::unmerge_cluster`,
`cli/compare.py`. `core/lineage.py` reads only `members` — unaffected.

## Problem

`DedupeResult.scored_pairs` is reconstructed from the clusters dict's
`pair_scores` (`_extract_pairs` flattens every cluster's `pair_scores` in
cluster-iteration order). Three consumers (#2/#3/#4) depend on that
reconstruction. This couples the public `scored_pairs` surface to the build
carrying per-cluster `pair_scores` dicts — blocking SP4.

The pipeline ALREADY has the scored-pair stream independently, at the cluster
stage: `all_pairs` (list path) / `_columnar_pairs_df` (columnar path), both at
`pipeline.py:1447-1461` (also the source of the `scored_pair_count` metric).

## Goal

Source `DedupeResult.scored_pairs` from the pipeline's pre-cluster scored-pair
stream, stored once on the result, so the `scored_pairs`-consuming surfaces
(#2/#3/#4) no longer read cluster `pair_scores`. This unblocks SP4 for those
consumers. `DedupeResult.clusters[cid]["pair_scores"]` is UNCHANGED (still
present) — only the SOURCE of `scored_pairs` moves.

## Not byte-identical (decided 2026-06-02)

The pre-cluster stream is NOT byte-identical to today's `scored_pairs`:
- **Order:** scoring order, vs today's cluster-grouped order.
- **Content:** auto-split drops cross-cut edges from post-split clusters'
  `pair_scores` (SP1 finding), so today's `scored_pairs` is MISSING them; the
  pre-cluster stream INCLUDES every scored pair. So the stream is a SUPERSET
  whenever an oversized cluster splits.

Decision: accept the diff. `scored_pairs` becomes "all scored pairs, in scoring
order." It equals today's as a MULTISET on the no-split case (the common case at
default `max_cluster_size=100`); it is a superset (gains cross-cut edges) +
reordered only when splits occur. This is arguably more complete (the full scored
set), and order is not semantically load-bearing for the consumers (candidate-pair
lists, lineage explanations). It is a documented behavior change to the public
`scored_pairs` field.

## Architecture

1. **Pipeline capture (`core/pipeline.py`, `_run_dedupe_pipeline`).** After the
   cluster stage, store the scored-pair stream on the `results` dict as
   `scored_pairs: list[tuple[int,int,float]]`:
   - list path: `all_pairs` (already `list[(a,b,score)]`).
   - columnar path: materialize `_columnar_pairs_df` to the same list shape
     (`zip(id_a, id_b, score)`), matching the list path.
   Set it once, near where `results` is assembled (`:1732`). The match pipeline
   builds no clusters and is unchanged (returns no `scored_pairs`).
2. **`_api.py`.** Replace `scored_pairs=_extract_pairs(result)` at `:300`
   (`dedupe`) and `:473` (`dedupe_df`) with
   `scored_pairs=result.get("scored_pairs", [])`. Keep `_extract_pairs` ONLY as a
   fallback for results that predate the field (or remove if no other caller).
   Update the `DedupeResult.scored_pairs` field docstring (`:76`,`:84`) to note it
   is the full scored-pair set in scoring order.
3. **Consumer reads.**
   - `cli/label.py:43-45`: read `result.get("scored_pairs", [])` (or
     `DedupeResult.scored_pairs`) instead of looping `cinfo["pair_scores"]`.
   - `web/routers/run.py:127` + `web/preview.py:186`: same — read the stored
     `scored_pairs` to feed `build_lineage`, instead of rebuilding from
     `cinfo["pair_scores"]`.

## Edge cases / invariants

- **No clusters / empty result:** `scored_pairs = []` (the stream is empty).
- **Columnar vs list path:** both must produce the IDENTICAL list shape +
  contents (the columnar path already asserts cluster parity with the list path
  via `tests/test_columnar_pipeline_parity.py`; the scored-pair stream is the same
  input, so materializing `_columnar_pairs_df` matches `all_pairs`).
- **Duplicate canonical pairs:** the pre-cluster stream is already canonicalized
  `(min,max)` + deduped upstream (`dedup_pairs`); store it as-is.
- **`DedupeResult.clusters` unchanged:** still carries `pair_scores`; unmerge (#1)
  + graceful degraders keep working off it. SP3 does NOT drop it.

## Testing

- **No-split multiset parity:** on a fixture with NO oversized clusters, assert
  the new `result["scored_pairs"]` multiset-equals the legacy `_extract_pairs`
  output (same `(a,b,score)` set). Locks "common case unchanged."
- **Split superset:** on an oversized-splitting fixture, assert `scored_pairs`
  INCLUDES the cross-cut edges that auto-split drops from cluster `pair_scores`
  (the documented new behavior) — i.e. `scored_pairs` ⊋ flattened cluster
  pair_scores.
- **Columnar == list:** assert `scored_pairs` is identical (as a multiset) on the
  columnar pipeline path vs the list path for the same input.
- **Consumer smoke:** `goldenmatch label` produces candidate pairs from the new
  source; web run/preview lineage still populates pairs.
- **Update existing tests:** any `test_api` / label test asserting `scored_pairs`
  order or exact content adjusts to multiset / new-behavior expectations.

## Scope boundary (YAGNI)

- ONLY the pipeline scored-pair capture + the three consumer reads (#2/#3/#4) +
  the `DedupeResult.scored_pairs` docstring.
- NOT unmerge (#1, post-hoc — separate SP), NOT distributed (#5), NOT dropping
  `pair_scores` from the build (SP4). `DedupeResult.clusters` keeps `pair_scores`.
- NO new gate (this is an unconditional source change for `scored_pairs`, not
  gated — the behavior change is accepted, not opt-in).

## References

- SP1 `docs/superpowers/specs/2026-06-02-columnar-cluster-build-core-design.md`
  (#673); SP2 `docs/superpowers/specs/2026-06-02-cluster-pairscore-view-design.md`
  (#679). Golden phantom: issue #678.
- `pipeline.py`: scored-pair stream + cluster stage `:1440-1461`, results assembly
  `:1732`. `_api.py`: `_extract_pairs` `:1135`, `DedupeResult` `:76-84`, sites
  `:300`/`:473`. Consumers: `cli/label.py:43`, `web/routers/run.py:127`,
  `web/preview.py:186`.
- Related: [[project_663_arrow_kernels]], [[project_identity_graph_v2]].
