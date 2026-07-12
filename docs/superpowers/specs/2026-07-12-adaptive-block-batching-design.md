# Adaptive block-batching in the parallel fuzzy scorer

**Date:** 2026-07-12
**Status:** Design approved, pending spec review
**Author:** Ben Severn (with Claude)
**Area:** `packages/python/goldenmatch/goldenmatch/core/scorer.py`, `.../core/blocker.py`

## Problem

`score_blocks_parallel` (scorer.py) submits **one `ThreadPoolExecutor` future per
block**. On a 500K zero-config dedupe the committed config produces ~62,000 tiny
blocks (median 1 row, P99 ~250, max ~50 for the soundex name key), so the executor
orchestrates ~62,000 futures.

Measured on the green post-#1680 `bench-zero-config` run (run 29162502432, 500K x3,
`large-new-64GB`, median wall 455.67s), cProfile top by cumtime:

| cumtime | ncalls  | func |
|---------|---------|------|
| 391.88s | 62,194  | `concurrent/futures/_base.py:199:as_completed` |
| 304.37s | 43,163  | `threading.py:637:wait` |
| 276.01s | 483,579 | `<method 'acquire' of '_thread.lock'>` |
| 272.50s | 62,190  | `scorer.py:1327:_score_one_block` |

The scoring math is trivial: blocks are <=50 rows, and within each block
`find_fuzzy_matches` uses `rapidfuzz.cdist` (which releases the GIL). The wall is
**per-block executor orchestration** (`as_completed` + per-future lock acquire +
`_score_one_block` Python setup), not scoring and not coarse blocking. Cluster
output for that run: `cluster_count=434,572`, `multi_member_cluster_count=59,242`,
`oversized_cluster_count=0`.

**Perf-expectation note (cProfile cumtime is inclusive).** The `as_completed`
391.88s is cumulative time that *wraps* the worker scoring it waits on
(`_score_one_block` is itself 272.50s cumtime). Batching eliminates the per-future
orchestration and the 483,579 lock acquisitions (~276s), NOT the ~272s of actual
`_score_one_block` scoring, which persists inside the batches. So the realistic
wall floor is around the scoring time (~270-300s region), not near-zero. A landing
in that range is a success, not a shortfall — do not read the 391.88s as fully
recoverable.

This is a pre-existing O(N)-in-block-count overhead, not a regression. It is the
already-identified but deferred "Track 1 Fix B" noted in the package CLAUDE.md
("batch many small blocks per cdist call") originally surfaced by the 5M bucket
bench (1.67M blocks, `bucket_score` 42 min of wall).

## Goal

Collapse the ~62,000 futures into a few dozen work units so `as_completed`/lock
overhead drops by orders of magnitude, while producing **byte-identical cluster
output**. Scoring math is unchanged; only the grouping of blocks into futures
changes.

Non-goal: the native fast-path decline (`_resolve_score_pair_callable('ensemble')
is None`, per-pair Python scoring) is a separate, deprioritized lever. Non-goal:
the `bucket` and `ray` backends (bucket already partitions into 64 units).

## Design

### 1. Shared batch planner

New helper in `scorer.py`:

```
_plan_block_batches(blocks: list[BlockResult], max_workers: int) -> list[list[BlockResult]]
```

Adaptive grouping:

- **Big blocks** — candidate pairs `n*(n-1)//2 >= _SOLO_BLOCK_MIN_PAIRS` — become
  their own single-element batch. This preserves full parallelism where the
  scoring work is actually large (mixed-size and 5M fixtures with occasional
  thousand-row blocks).
- **Small blocks** — everything below the threshold — are distributed by greedy
  LPT (longest-processing-time-first) bin-packing into `min(len(small_blocks),
  max_workers * _BATCH_BINS_PER_WORKER)` bins, balancing each bin by summed
  candidate-pair count. Each bin is one batch.

The planner reads each block's size from `BlockResult.n_rows` (see §2). It MUST NOT
call `.collect()` / `.select(pl.len()).collect()` per block — that is the documented
OOM leak (PRs #295/#301/#303: 62K-1.67M individual collects accumulate Polars arena
memory and OOM-kill the runner). When `block.n_rows is None`, the planner treats the
block as small and falls back to size-agnostic round-robin binning across
`max_workers * _BATCH_BINS_PER_WORKER` bins. This still collapses the future count;
it just loses the big-block-solo adaptivity for that path.

Constants (module-level, env-overridable):

- `_SOLO_BLOCK_MIN_PAIRS` (default chosen by the bench sweep, starting hypothesis
  ~10,000 pairs ~= a 140-row block). Env: `GOLDENMATCH_SCORER_SOLO_BLOCK_MIN_PAIRS`.
- `_BATCH_BINS_PER_WORKER` (default 4). Env: `GOLDENMATCH_SCORER_BATCH_BINS_PER_WORKER`.

### 2. Cheap size sourcing on `BlockResult`

`BlockResult` (blocker.py:151) gains one field:

```
n_rows: int | None = None
```

Populate it at the construction sites where the size is **already computed for
free** — the group-materialization paths compute `size = len(group_df)`
(blocker.py:290), `size = len(block_df)` (blocker.py:404, 545), etc. Thread that
value into the `BlockResult(...)` constructor at those sites. No new materialization
is introduced; a value already in hand is carried forward instead of discarded.

Sites that cannot cheaply know the size (e.g. a pure-lazy fallback) leave `n_rows`
as `None`, and the planner degrades gracefully (§1).

**Site nuance (ANN sub-blocks).** At the ANN construction site (blocker.py:~485)
the sub-block is `sub_df = block_df[member_list]`, so the correct free value is
`len(member_list)` (already checked at ~481) — NOT the parent-block `size` from
line 404. Threading the parent size there would populate `n_rows` with the wrong
(larger) value and mis-route those sub-blocks to solo batches.

### 3. Uniform batch work function

```
_score_block_batch(batch: list[BlockResult], mk, frozen_exclude, across_files_only, source_lookup) -> list[tuple[int,int,float]]
```

Loops `_score_one_block` over each block in the batch and concatenates the returned
pairs. Because a big block is a batch of one, the executor path is uniform: submit
one `_score_block_batch` future per batch, collect via `as_completed`.

The columnar twin gets a mirror `_score_block_batch_columnar` looping
`_score_one_block_columnar` and concatenating the per-block `pl.DataFrame`s. It MUST
preserve the existing per-block `matched_pairs.add(...)` ordering that
`score_blocks_columnar` performs before `pl.concat` (scorer.py:~1545-1552, kept "so
order is consistent with the list path") — the batch fn does the same per-block
`matched_pairs` update in block order so the mirror does not drift from the list
path.

### 4. Wiring into `score_blocks_parallel` and `score_blocks_columnar`

Both functions currently:

```
with ThreadPoolExecutor(max_workers=max_workers) as executor:
    for block in blocks:
        future = executor.submit(_score_one_block, block, ...)   # one per block
    for future in as_completed(...):
        pairs = future.result(); all_pairs.extend(pairs); matched_pairs.add(...)
```

become:

```
batches = _plan_block_batches(blocks, max_workers)
with ThreadPoolExecutor(max_workers=max_workers) as executor:
    for batch in batches:
        future = executor.submit(_score_block_batch, batch, ...)  # one per batch
    for future in as_completed(...):
        pairs = future.result(); all_pairs.extend(pairs); matched_pairs.add(...)
```

- The `len(blocks) <= 2` sequential shortcut stays untouched.
- The `target_ids` post-filter stays in the collection loop, applied to each
  batch's returned pairs (per-pair predicate, batching-independent).
- The existing `_CANDIDATE_COUNT_SKIP_THRESHOLD` candidate-count loop is unchanged
  and unaffected — the planner does not depend on it (it reads `BlockResult.n_rows`,
  not that loop's `total_candidates`).
- Progress logging switches from per-block to per-batch cadence.

### 5. Byte-identical output invariant

Batching changes neither the set of `_score_one_block` calls nor their inputs:

- Every block is scored exactly once, by the same `_score_one_block`, with the same
  `mk`, `across_files_only`, `source_lookup`.
- All batches read the same pre-loop `frozen_exclude = frozenset(matched_pairs)`
  snapshot. Today no block sees another block's in-run matches either (threads hold
  the frozen snapshot; `matched_pairs` is mutated only in the main collection
  thread and never feeds back into scoring). Batching preserves this exactly,
  including for blocks grouped into the same batch (the batch fn passes the same
  `frozen_exclude` to every `_score_one_block`).

The only observable change is the **order** pairs land in `all_pairs` (batch-
completion order vs block-completion order — both already nondeterministic run to
run). Downstream clustering canonicalizes pairs to `(min, max)` and is set-based
(Union-Find), so the cluster assignment is invariant to pair order. Therefore the
committed contract is **identical clusters**, not identical `all_pairs` ordering.

## Testing

All at-scale testing is remote (GitHub `bench-zero-config`) — the local box OOMs on
500K.

Unit (local, small fixtures):

1. `test_batched_equals_per_block` — on a fixture with mixed block sizes (a few
   big blocks + many singletons/pairs), assert
   `sorted(score_blocks_parallel(...)) == sorted(<per-block reference>)`. The
   reference is today's behavior captured by forcing one-block-per-batch (e.g.
   `_SOLO_BLOCK_MIN_PAIRS=0` + `_BATCH_BINS_PER_WORKER` large, or a direct
   per-block loop).
2. `test_plan_block_batches` — big blocks become batches of 1; small blocks pack
   into `<= max_workers * K` bins; `n_rows=None` blocks fall to round-robin;
   empty input → empty plan; `<=2` blocks path unaffected.
3. Columnar twin parity: `score_blocks_columnar` batched output equals its
   per-block reference (same pair set).
4. `BlockResult.n_rows` is populated (non-None) on the standard `build_blocks`
   group path for a small fixture.

Bench (remote, `bench-zero-config` at 500K):

5. Cluster identity: `cluster_count == 434572` and
   `multi_member_cluster_count == 59242` (byte-identical to the #1680 baseline).
6. Wall drop: median wall materially below 455s (target: the ~391s `as_completed`
   overhead largely eliminated; exact figure set by the run).
7. A mixed-size sanity run to confirm big-block solo parallelism isn't regressed.

## Risks and mitigations

- **Reintroducing the OOM leak** via per-block `.collect()` in the planner —
  mitigated by sourcing size from `BlockResult.n_rows` (populated where already
  computed) and forbidding `.collect()` in the planner; `None` degrades to
  round-robin, never to a collect.
- **A construction path leaves `n_rows=None`**, silently losing adaptivity —
  acceptable (still collapses futures); a unit test asserts the main path sets it.
- **Straggler batch** if one bin gets disproportionate work — mitigated by LPT
  bin-packing on candidate-pair count plus big-block-solo extraction.
- **Non-identical clusters** — guarded by the byte-identical invariant (§5) and the
  bench cluster-count assertion; if clusters differ, the change is wrong and blocks
  merge.

## Rollout

Behavior-preserving, no config/API surface change, no env flag required to benefit
(the batching is the default path). The two tunables are env-overridable escape
hatches. No default-flip gate needed since output is byte-identical; the
`bench-zero-config` cluster-count assertion is the standing correctness check and
the wall is the perf proof.

The plan MUST pin a concrete `_SOLO_BLOCK_MIN_PAIRS` default from the bench sweep
before merge rather than shipping the ~10,000 starting hypothesis as-is.
