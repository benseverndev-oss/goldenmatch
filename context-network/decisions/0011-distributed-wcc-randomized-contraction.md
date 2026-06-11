# 0011 — Distributed WCC: randomized contraction over driver-collect / min-propagation

**Status:** accepted (2026-06-10, PRs #851 Spec 1 + #852 Spec 2)
**Evidence:** pure-Polars reference green vs `scipy.csgraph` on 425 random graphs + chain/star/cycle fixtures (a 1024-node chain converges in 9 rounds, not 1024); the Ray orchestration green on the `distributed` CI lane (the ray path produces the same components as the scipy-verified pure driver). The binding multi-node 100M run is operator-deferred.

## Context
#844: the Phase-5 distributed pipeline (`GOLDENMATCH_DISTRIBUTED_PIPELINE=2`)
under-merged at scale. PR #845's opt-in blocking-aware shuffle co-locates
duplicates, but making components cross input-partition boundaries breaks the
per-partition `local_cc_assignments` Union-Find. A real distributed WCC was
needed, and both existing implementations died at 100M: `two_phase_wcc`
driver-collects members + boundary edges and runs a cpython-loop UnionFind on the
head (wedges — proven on a real GCP run while workers sat idle); `distributed_wcc`
(min-label + pointer-jump) deadlocks Ray's streaming executor on its iterative
`Dataset.join` loop. GraphFrames maintainer Sem Sinchenko's advice was decisive:
identity graphs are chain-heavy (min-propagation's worst case), so use Two-Phase
(Kiveris 2014) or Randomized-Contraction (2018), implemented relationally — no
cpython UF, no O(N) driver dict.

## Decision
1. **Algorithm: randomized contraction** (Bögeholz–Brand–Todor 2018,
   arXiv:1802.09478) — relational, chain-robust, O(log|V|) rounds, no driver
   union-find. A pure-Polars reference is the correctness gate (vs `scipy`); the
   Ray path mirrors it.
2. **Ray-execution fix: per-round parquet checkpoint** of the shrinking edge set,
   truncating the lazy lineage that deadlocks the streaming executor (what
   `.materialize()` alone did not). It also gives the joins clean `ReadParquet`
   inputs — Ray Data's hash-shuffle join rejects both same-name keys AND
   `map_batches`-derived inputs.
3. **Opt-in, wired but not default.** `GOLDENMATCH_DISTRIBUTED_BLOCK_SHUFFLE=1`
   turns on the whole recall-complete path (shuffle scoring + WCC clustering, kept
   a unit by sharing the detection predicate). A new `algorithm` kwarg on
   `build_clusters_distributed` lets the pipeline force `randomized_contraction`
   so the at-scale path can't route to `two_phase`. Default stays
   `local_cc_assignments`.
4. **Decompose into two specs.** Spec 1 = the algorithm as a validated drop-in;
   Spec 2 = the e2e wiring + ready-to-run bench. The binding 100M run + the
   default-flip wait on a BYO cluster.

### Rejected alternatives
- **Fix `two_phase_wcc`'s driver collect.** The boundary super-graph is itself
  large at 100M (tens of millions of distinct local roots); a driver-side Python
  UF over it is the wedge. Sem named this exact antipattern.
- **Fix `distributed_wcc`'s deadlock with `.materialize()`.** Already tried;
  materialize doesn't truncate the iterative lineage enough — and min-propagation
  is chain-fragile regardless.
- **Two-Phase / large-star-small-star (Kiveris 2014).** Sem's other recommendation;
  randomized contraction is "slightly better," and a literal large-star was found
  buggy on the Sail tier (caught only by a plan-review hand-trace).
- **Flip the default now.** The binding proof is a multi-node 100M run we cannot
  run autonomously; flipping before it would be unproven. Opt-in until the
  operator's run.

## Consequences
- Two un-locally-testable Ray Data join rules are now load-bearing knowledge
  (distinct-keyed joins + `ReadParquet` inputs); recorded in
  [../architecture/distributed-wcc.md](../architecture/distributed-wcc.md).
- The `distributed` CI job gained a blocking ray gate for the new tests; its
  `timeout-minutes` went 20 → 30 to fit it (the added ~3.5 min step had cancelled
  the job).
- The Ray broad-coverage `test_phase5_pipeline_*` failures (`KeyError '__row_id__'`
  in a Ray `hash_partition`) are PRE-EXISTING and `continue-on-error`; verify
  against a recent `main` run before treating a broad-coverage failure as new.
- Parallel to the [Sail tier](../architecture/sail-tier.md)
  ([decision 0004](0004-sail-tier-scope.md)) — the Spark-Connect track that
  retires Ray. Whichever binds its 100M run first is the go-forward; this keeps the
  Ray path viable in the meantime.

---
**Classification:** decision/accepted • **Last updated:** 2026-06-10
