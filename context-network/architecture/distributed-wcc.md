# Distributed WCC (Ray) — randomized contraction + the recall-complete Phase-5 path

How the Ray Phase-5 pipeline gets correct clustering at 100M+: a relational
randomized-contraction connected-components pass that replaces the per-partition
Union-Find once scoring crosses partition boundaries. The Ray-side answer to the
same WCC-at-scale problem the [Sail tier](sail-tier.md) solves on its own track.

**Status:** BOTH specs SHIPPED (2026-06-10). Spec 1 = the WCC algorithm (PR #851,
`0aa1051f`); Spec 2 = wiring it into the Phase-5 e2e pipeline (PR #852, `d551f537`).
Opt-in (`GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE=1`), default unchanged. Only the
operator-side binding 100M run + the default-flip remain (need a BYO multi-node Ray
cluster). **Specs/plans:** `docs/superpowers/specs|plans/2026-06-10-distributed-wcc-*`.
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
algorithm="randomized_contraction")`; otherwise the default `local_cc_assignments`.
The new `algorithm` kwarg overrides the env selector so the at-scale path can't
route to `two_phase` (which head-wedges). The join + golden tail is unchanged —
both clustering routes emit the same `{member_id, cluster_id, cluster_size,
oversized}` contract that `_join_assignments_distributed` + distributed golden
consume. Below the 50M-pair threshold `build_clusters_distributed` still uses
driver-side scipy (correct, bounded); the distributed WCC fires at scale.

## Operator run (deferred — needs a BYO cluster)
`GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE=1` +
`GOLDENMATCH_DISTRIBUTED_WCC=randomized_contraction` +
`GOLDENMATCH_DISTRIBUTED_WCC_SCRATCH=gs://<bucket>/...` (shared storage is
load-bearing — node-local breaks the cross-node parquet reads). Bench via the
`run_phase5_bench` leg of `bench-distributed-stack.yml` (RAY_ADDRESS +
`bench_100000000.parquet`); the sim leg (`run_phase5_simulated`, 4 workers in one
runner) asserts recall improves vs the per-partition baseline and fails on no
signal. GCP cluster recipe in `docs/distributed-ray-cluster-setup.md`.

## Relationship to the Sail tier
This is the **Ray** answer to WCC-at-scale; the [Sail tier](sail-tier.md) (decision
0004) is the **Spark-Connect** answer that ultimately retires Ray. Parallel tracks
on the same problem; whichever binds its real 100M run first is the go-forward.

---
**Classification:** architecture/active • **Last updated:** 2026-06-10
