# Distributed WCC (Ray) — randomized contraction + the recall-complete Phase-5 path

How the Ray Phase-5 pipeline gets correct clustering at 100M+: a relational
randomized-contraction connected-components pass that replaces the per-partition
Union-Find once scoring crosses partition boundaries. The Ray-side answer to the
same WCC-at-scale problem the [Sail tier](sail-tier.md) solves on its own track.

**Status:** FINISH LINE (2026-06-11). WCC algorithm (PR #851, `0aa1051f`), e2e
wiring (PR #852, `d551f537`), and the #864 follow-ups (`b63af6f3`) all merged.
**Validated end-to-end at 100M on a real 5-node GCP cluster: full recall-complete
dedupe in 554.5 s (9.2 min, under the 30-min kill), 20,000,000 clusters recovered
exactly, driver RSS 0.36 GB — no head-wedge, no Ray deadlock.** Default FLIPPED to
recall-complete-on (PR #867). **Specs/plans:**
`docs/superpowers/specs|plans/2026-06-10-distributed-wcc-*`.
**Decision:** [../decisions/0011-distributed-wcc-randomized-contraction.md](../decisions/0011-distributed-wcc-randomized-contraction.md).

## The problem (#844)
The Phase-5 distributed pipeline under-merged at scale. PR #845 added an opt-in
blocking-aware shuffle to scoring (so true duplicates co-locate), but once pairs
cross input-partition boundaries the per-partition `local_cc_assignments`
Union-Find under-merges. A real distributed WCC was needed — and the two existing
ones both died at 100M: `two_phase_wcc` collected members + boundary edges to the
driver and ran a cpython-loop UnionFind there (head-wedge, proven on a real GCP
run); `distributed_wcc` (min-label + pointer-jump) deadlocked Ray's streaming
executor on its iterative `Dataset.join` loop. Two independent failure axes — a
chain-fragile algorithm AND an iterative-join deadlock — had to be fixed at once.

## The algorithm
`randomized_contraction_wcc` (`distributed/clustering.py`) implements
Bögeholz–Brand–Todor (2018, arXiv:1802.09478, "In-database connected component
analysis") — the algorithm GraphFrames maintainer Sem Sinchenko recommended for
chain-heavy identity graphs (min-propagation's worst case). Each round: a random
affine hash `h(x)=(A·x+B) mod p` (p = 2^31-1, i64-safe); each vertex's
representative is the min-hash vertex in its closed neighbourhood; edges contract
to reps and self-loops drop. The edge set shrinks geometrically (O(log|V|) rounds
w.h.p.), with no driver union-find and no O(N) driver dict. A pure-Polars
reference (`_rc_wcc_polars`) is the correctness gate (validated vs
`scipy.csgraph` on 425 random graphs + chain/star/cycle fixtures); the Ray path
mirrors it.

## The two Ray-execution gotchas (cost CI rounds; now load-bearing)
Ray Data's hash-shuffle `Dataset.join` (pyarrow Acero under the hood) is finicky:
1. **Distinct-named keys only.** Same-name keys on both sides (`edges.v == rep.v`)
   raise `ArrowInvalid: ... multiple matches for key field reference`. The rep
   table is keyed on `node`, so every join is distinct-keyed (v/node, w/node,
   cur/node) — the working `distributed_wcc` joins are distinct-keyed for the same
   reason.
2. **ReadParquet inputs only.** A `map_batches`-derived dataset as a join input
   fails the same way; checkpoint the intermediate to parquet so the join's inputs
   are clean `ReadParquet` datasets. Diagnose from the CI log's "Execution plan of
   Dataset" lines (working joins show `ReadParquet -> Join`; the failing one showed
   `InputDataBuffer -> Join`).

The per-round parquet checkpoint (the deadlock fix) doubles as the clean scratch
those joins need.

## The recall-complete Phase-5 path (Spec 2)
`_run_phase5_pipeline` (`distributed/pipeline.py`) step 3 branches via
`_phase5_cluster(raw_pairs_ds, cfg)`: when `_block_shuffle_enabled() and
_has_colocation_plan(cfg)` (the SAME predicate `score_blocks_distributed` uses, so
scoring and clustering stay a unit) it routes to
`build_clusters_distributed(raw_pairs_ds, all_ids=None,
algorithm="randomized_contraction")`; otherwise (block-shuffle off via
`GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE=0`, or no co-location plan)
`local_cc_assignments`. As of the finish line block-shuffle is default-ON, so the
WCC branch is the normal path.
The new `algorithm` kwarg overrides the env selector so the at-scale path can't
route to `two_phase` (which head-wedges). The join + golden tail is unchanged —
both clustering routes emit the same `{member_id, cluster_id, cluster_size,
oversized}` contract that `_join_assignments_distributed` + distributed golden
consume. Below the 50M-pair threshold `build_clusters_distributed` still uses
driver-side scipy (correct, bounded); the distributed WCC fires at scale.

## The 100M validation run (DONE 2026-06-11) + the finish-line fixes (#864)
The binding run was executed on a self-provisioned 5-node `e2-standard-16` GCP
cluster against a 100M synthetic phase-5 dataset in GCS. The WCC itself was first
validated in isolation (a 200M-edge graph straight into
`build_clusters_distributed(algorithm="randomized_contraction")`: 266 s, driver
RSS 358 MB, all 20M components, no wedge/deadlock). Getting the FULL e2e to pass
surfaced three issues separate from the WCC, all fixed in #864:
- **(a) auto-config crashed on a `__row_id__`-carrying input** — `_add_row_ids`
  re-added the column unconditionally (`DuplicateError`), so every auto-config
  iteration errored → RED → `ControllerNotConfidentError`. Guarded to reuse an
  existing global id.
- **(c) the e2e bench had no explicit-config path**, so it always auto-configured
  (~40 full-dataset sample reads + a degenerate RED config at 100M). Added
  `--config` (built-in `phase5-synth` preset / YAML) + `--allow-red-config`.
- **(b) the real e2e wall: per-group scoring, not the WCC.**
  `_score_colocated_groups` looped `group_by([__keyid__, __block_key__])` and ran
  the full per-partition kernel ONCE PER GROUP — ~20M fixed-overhead invocations
  at 100M (0 of 64 score-tasks finished in 25 min). The loop was redundant (the
  `bucket` backend already groups by the blocking key), so it now scores the
  whole partition in one vectorized pass (drop the co-location cols, dedup by
  `__row_id__`, single `_score_partition_with_config` call). Parity-tested. THAT
  single change took the e2e from non-viable to **9.2 min** at 100M.

`(b)` secondary (deferred, optional): the explode copies the full record per
co-location key, so the shuffle moves more columns than scoring needs — project
to scoring columns before the shuffle (a win on wide records, not needed for
viability). Noted on `_score_blocks_block_shuffle`.

**Operator env (unchanged):** `GOLDENMATCH_DISTRIBUTED_WCC_SCRATCH=gs://<bucket>/...`
is load-bearing on multi-node (node-local breaks the cross-node parquet reads);
`randomized_contraction_wcc` now RAISES on a multi-node cluster with a node-local
scratch (the `_assert_scratch_shared_if_multinode` guard, added with the
default-flip). GCP cluster recipe in `docs/distributed-ray-cluster-setup.md`.

## Relationship to the Sail tier
This is the **Ray** answer to WCC-at-scale; the [Sail tier](sail-tier.md) (decision
0004) is the **Spark-Connect** answer that ultimately retires Ray. Parallel tracks
on the same problem; whichever binds its real 100M run first is the go-forward.

---
**Classification:** architecture/active • **Last updated:** 2026-06-11
