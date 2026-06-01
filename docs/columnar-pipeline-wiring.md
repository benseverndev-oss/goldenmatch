# Wiring the columnar pair-stream path into the default pipeline

Status: **Phase 0 (analysis + parity harness)**. This note records the 2026-06-01
`profile-hotspots` finding, corrects the lever attribution, and lays out a phased,
parity-gated plan to route the default dedupe pipeline through the columnar
pair-stream path. No default behavior changes in Phase 0.

## What the 1M profile measured

`profile-hotspots` (n=1,000,000, 131,166,381 pairs, `realistic_person` fixture):

| target | wall (cProfile) | wall (pyinstrument) |
|---|---:|---:|
| **columnar** (`score_blocks_columnar` -> `build_clusters_columnar`) | **359 s** | **353 s** |
| list (`score_blocks_parallel` -> `build_clusters`) | 575 s | 575 s |

The columnar path is ~38% faster at 1M (216 s saved). The cumtime top-10 is
dominated by `threading.join` / `_thread.lock.acquire` (~390 s) -- that is the
`ThreadPoolExecutor` teardown wait wrapping the parallel rapidfuzz scorer, i.e.
genuine GIL-released compute, not Python overhead. Stripping it, the timed body
splits roughly:

- `score_blocks_columnar` ~195 s (~54%) -- fuzzy scoring of 131M pairs.
- `build_clusters_columnar` ~164 s (~46%) -- by subtraction.

## Corrected lever attribution

A reading of `core/cluster.py` shows **`build_clusters_columnar` is a thin wrapper**:
it converts the pair DataFrame to a list via `_pairs_df_to_list_numpy` and calls the
same `build_clusters`. The cluster *algorithm* (Union-Find + the `pair_scores` dict
fill + confidence + MST split) is therefore **identical** between the list and
columnar paths.

So the measured 38% win is **the scorer**, not the cluster build:

- `score_blocks_parallel` builds a Python `list[tuple]` of 131M pairs (per-pair tuple
  append). `score_blocks_columnar` (#634 direct-DataFrame emit, #639 native columnar
  inner loop) emits the pair stream columnar. At 131M pairs the list construction is
  the difference -- ~411 s (list) vs ~195 s (columnar) on the scoring half.
- The ~164 s cluster build is the same in both paths. It is **not** improved by
  `build_clusters_columnar`; reducing it needs the native Arrow-C `build_clusters`
  kernel (#645) and/or the `ClusterFrames` two-frame representation (#632/#635) that
  avoids the 131M-entry `pair_scores` dict entirely. That is a separate lever.

**Conclusion:** the realizable win today is wiring the **columnar scorer + cluster
path** into the default pipeline. The win is inseparable from staying columnar
end-to-end -- converting the columnar df back to a list re-incurs the 131M-tuple
build and erases the gain.

## Why this is a phased refactor, not a swap

`core/pipeline.py::_run_dedupe_pipeline` aggregates `all_pairs` as a single Python
`list[tuple]` across **multiple scorers**: exact matching (Step 2), fuzzy blocks
(Step 3, the per-matchkey loop), and probabilistic / Fellegi-Sunter (Phase 2b).
Between scoring and clustering, `_apply_postflight(collected_df, config, all_pairs)`:

- is a **no-op** unless auto-config ran (`config._preflight_report` set), but
- when active, computes postflight **signals** from the pair list and applies a
  **threshold filter** (`[p for p in all_pairs if p[2] >= adj.to_value]`).

So a correct end-to-end columnar pipeline must make the scorer aggregation,
postflight signals, and threshold filter df-native -- the multi-phase Arrow roadmap.
A blind swap of just the cluster call does not capture the win (the scorer is the
lever) and would break the list-coupled postflight.

## Parity contract (Phase 0 deliverable)

`tests/test_columnar_pipeline_parity.py` locks the invariant every wiring phase must
preserve: for the same blocks + matchkey,
`score_blocks_columnar -> build_clusters_columnar` produces clusters whose membership
partition, per-pair `pair_scores`, and per-cluster `size` / `oversized` / `confidence`
are **identical** to `score_blocks_parallel -> build_clusters`. Verified at n in
{500, 2000, 5000}, with and without `auto_split`, plus the empty-pair (all-singleton)
case. (Empirically also confirmed identical at n=10000.)

## Phased plan

Each phase is gated and ships default-off until soaked + benched.

- **Phase A -- gated end-to-end columnar fast-path (narrow, safe).**
  Add `GOLDENMATCH_COLUMNAR_PIPELINE` (default off). When on **and** the config is
  eligible -- exactly one `weighted`/`fuzzy` matchkey, no exact/probabilistic
  matchkeys, not `across_files_only`, no auto-config `_preflight_report` (postflight
  is a no-op), backend in {parallel, polars-direct} -- route Step 3 + Step 4 through
  `score_blocks_columnar -> build_clusters_columnar`, skipping the list aggregation.
  Golden (Step 5) consumes the same `clusters` dict, unchanged. Parity test above is
  the gate. This captures the ~38% win for the common single-fuzzy-matchkey shape with
  zero risk to existing paths.
- **Phase B -- df-native postflight.** Make postflight signals + threshold filter
  operate on the pair DataFrame so the fast-path can engage when auto-config ran.
- **Phase C -- multi-scorer columnar aggregation.** Emit exact + probabilistic pairs
  columnar and `pl.concat` the per-scorer DataFrames, retiring the `all_pairs` list as
  the default. This is the point the columnar path becomes the default scorer output.
- **Phase D -- native cluster kernel.** Wire the Arrow-C `build_clusters` (#645) /
  `ClusterFrames` (#632/#635) path to attack the ~164 s cluster build (the
  `pair_scores` dict fill), gated by `native_enabled("clustering")` with the
  pure-Python path as the byte-for-byte reference.

Separately (not on this path): the ~195 s scoring is genuine compute over 131M pairs;
its levers are batched `cdist` for small blocks (deferred "Track 1 Fix B") and tighter
blocking to cut the pair count.
