# Cluster post-UF orchestration kernel — design

**Status:** Draft, 2026-05-30
**Targets:** v34 attribution: 60% of cluster's 75s wall in Python orchestration

## Problem

v34 sub-stage attribution of `core/cluster.py::build_clusters`:

| sub-stage | s | % | language |
|---|---|---|---|
| `cluster_connected_components` | 18.0 | 24% | Rust (native UF) |
| `cluster_sort_clusters` | 1.6 | 2% | Python |
| `cluster_member_to_cid` | 1.3 | 2% | Python (10M ops) |
| `cluster_result_dict_init` | 21.7 | 29% | Python (2M dict allocs) |
| `cluster_pair_scores_fill` | 23.3 | 31% | Python (20M tuple+dict ops) |
| `cluster_compute_confidence` | 7.5 | 10% | 2M FFI hops |
| `cluster_quality_assignment` | 2.0 | 3% | Python |

The native UF is fast. Everything around it is Python orchestration over
native data structures: dict allocations, tuple-key inserts, FFI ping-pong
for per-cluster confidence. Combined hot loop: ~55s of the 75s.

## Goal

Push the post-UF orchestration into Rust. A single FFI hop returns the
ready-to-use `dict[int, dict]` shape that downstream code expects.

## API design

```rust
#[pyfunction]
pub fn build_clusters_native(
    py: Python<'_>,
    pairs: Vec<(i64, i64, f64)>,   // pre-canonicalized? doesn't matter; we'll canonicalize internally
    all_ids: Vec<i64>,
    max_cluster_size: usize,
    weak_cluster_threshold: f64,
) -> PyResult<PyObject>;
// returns dict[int, dict] with the existing shape:
//   {cluster_id: {
//      "members":  list[int],            # unsorted (per #598)
//      "size":     int,
//      "oversized": bool,
//      "pair_scores": dict[tuple[int,int], float],
//      "confidence": float,
//      "bottleneck_pair": tuple[int,int] | None,
//   }}
```

What it subsumes (vs current Python):
- `connected_components` (already Rust, reuse internally)
- `sorted(clusters, key=lambda s: min(s))`
- `member_to_cid` build
- `result` dict allocation per cluster
- `pair_scores` dict-of-tuples fill
- `compute_cluster_confidence` per cluster (already Rust, batched here)

What stays in Python:
- `auto_split` loop and `split_oversized_cluster` (calls graph.py; out of scope)
- `cluster_quality_assignment` (small loop; 2s wall; depends on auto_split outcome)
- The `_emit_cluster_profile(result)` instrumentation

## pyo3 conversion strategy — the perf risk

Returning `dict[int, dict[str, dict[tuple, float]]]` materializes 2M+ Python
dicts and 20M+ Python tuples regardless of which language constructs them.
The win has to come from doing fewer Python-bytecode-interpreted operations
between the inserts — not from avoiding the inserts.

Best-case estimate: pyo3's `IntoPyDict` is a C-level loop, ~3-5x faster
than CPython bytecode for the same insert volume. Realistic save: 20s on
pair_scores fill alone. Plus the ~7s of compute_confidence FFI overhead
becomes one hop. **Total realistic save: 25-35s.**

If the dict-construction cost in pyo3 is similar to Python's, the win
shrinks. Worth measuring before celebrating.

## Backward-compatibility contract

Audit of `pair_scores` readers across the codebase:

- `cli/compare.py`, `cli/label.py` — `.items()` iteration
- `core/explain.py` — `.values()`, `.get((a,b))`
- `core/graph.py` — `.get(key)` with tuple key
- `core/_profile_helpers.py::transitivity_rate` — random access via tuple
- `core/cluster.py::split_oversized_cluster` — `min(p, key=lambda)` over keys
- `core/cluster.py::cluster_quality_assignment` — `.values()`

All consumers depend on the **dict-with-tuple-key** shape. Cannot switch
to a flat list without updating all readers; out of scope for this PR.

The kernel must return the exact same shape Python builds today.

## Parity testing

`tests/test_native_cluster_orchestration_parity.py` (new):

For three workload shapes (small / medium / oversized):

1. Build clusters via the existing Python path (force-disable native via
   `GOLDENMATCH_NATIVE=0`).
2. Build clusters via the new native kernel.
3. Assert deep-equal on the result dict, including:
   - Same `cluster_id` for each member (order may differ; compare by
     `frozenset(members) -> cluster_id`)
   - Same `pair_scores` dict contents (canonicalized keys)
   - Same `confidence` within `1e-6`
   - Same `bottleneck_pair` (handle None / either tuple ordering)
   - `oversized` agreement
   - `quality` field assigned identically AFTER Python wrapper

## Implementation steps

1. **Spec → PR**: spec lives at this path (gitignored).
2. **Rust kernel** (`cluster.rs`): build the orchestrator. Reuse the
   internal `find`/`union` and `cluster_confidence` helpers.
3. **Python wiring** (`core/cluster.py::build_clusters`): when
   `native_enabled("clustering")`, route through the new kernel. The
   auto_split + quality loops stay in Python on the returned dict.
4. **Parity tests**: as above.
5. **Bench**: v40 (10M bucket) baseline is 488s with cluster at 75s.
   Target post-PR cluster wall: 30-40s. Total wall improvement: 5-7%.

## Risks

1. **pyo3 dict-construction cost may dominate.** First profile of the
   kernel should split out FFI handoff time vs internal compute time.
   If FFI dominates, the win is smaller than projected and we should
   reconsider whether to ship.
2. **Memory peak may rise.** Rust builds the full result structure
   before returning. Bucket peak at 10M was 35-36 GB; this kernel may
   transiently double-buffer cluster data. Bench with care.
3. **Auto-split contract**: the Python auto-split loop reads
   `cinfo["pair_scores"]` mid-loop and rebuilds clusters. The native
   kernel returns the dict but auto-split mutates it. As long as the
   returned dict is a normal Python dict (not a wrapper), this works.

## Out of scope

- Batched soundex / dice / jaccard cdist variants (separate Rust spec).
- PPRL bloom-filter native kernel (separate spec).
- Identity Graph bulk fingerprint kernel (separate spec — #2 on the
  priority list).

## Decision needed

Approve scope → implement. PR-sized lift (Rust kernel + Python wiring
+ parity tests + bench).
