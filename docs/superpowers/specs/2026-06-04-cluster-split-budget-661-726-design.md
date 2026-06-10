# Cluster split efficiency + edge-work budget (#661 + #726) -- design

Date: 2026-06-04
Issues: benseverndev-oss/goldenmatch#661 (root), #726 (symptom + DX)
Status: design (approved in brainstorming, pending spec review)

## Problem

`split_oversized_cluster` (`core/cluster.py:157`) builds a max-weight MST (Kruskal
over all O(E) edges) and removes the SINGLE weakest edge -> 2+ components, marking
them all `oversized: False`. The CALLER (the split loop in `build_cluster_frames`
~607-640 and the `_finalize` tail) re-checks each subcluster's size, re-enqueues
the still-oversized ones, and **re-calls `split_oversized_cluster`, rebuilding the
MST over O(E) edges each time**. Splitting a dense cluster into k pieces is
**O(E*k)** (#661).

That redundant work is charged against the hard-coded **5M `edge_work` budget**
(`_split_edge_work_budget`, `:37`): each split call does `edge_work += len(ps)`
(`:614`), and when `edge_work > 5_000_000` the loop stops and leaves clusters
oversized, which are then **excluded from golden downstream** (`:32`) -- silently
losing true-match clusters (#726). A probabilistic matchkey at ~1M rows emits a
denser edge set -> bigger oversized clusters -> more split passes -> budget blown
-> 849 clusters dropped (#726's report). #491 (just shipped) makes probabilistic
selectable, so this now bites real datasets.

**#661 is the root cause; #726 is the symptom + a silent-drop DX hole.**

## Design

### Component 1 -- efficient split: build the MST ONCE (#661 root fix)

Replace the per-call re-MST with a single function that splits a cluster all the
way down to `max_cluster_size` in ONE MST build:

`split_oversized_cluster_to_size(members, pair_scores, max_size) -> list[dict]`:
1. Build the MST once (`_build_mst`, or the native MST).
2. Operate on the n-1 tree edges. Repeatedly: find a component still
   `> max_size`, cut its **weakest tree edge** (same decision as today), update a
   union-find over the tree edges. Repeat until no component exceeds `max_size`
   OR a component has no remaining cuttable tree edge (leave it oversized).
3. Return all final subclusters (member lists + partitioned pair_scores +
   confidence), marking `oversized = size > max_size`.

Cost: O(E) for the one MST build + O(tree cuts) -- not O(E*k).

**INVARIANCE (the hard requirement):** this must preserve the EXACT same cut
decisions as today (cut the weakest edge within each still-oversized component,
recurse) -- just without rebuilding the MST. It is NOT the issue's looser "remove
the k weakest tree edges globally" (which could cut inside already-small
components and change co-clustering). The clustering quality-invariance gate must
stay byte-identical.

**Native path:** the existing `native_module().mst_split_components` does
single-edge removal. Component 1 is implemented in PURE PYTHON (build MST once,
cut tree edges); it does NOT require a Rust kernel change. Keep the native
single-edge kernel available for any remaining single-split callers, but the
oversized-split path routes through the new Python batch function. A native-parity
test already exists (`test_native_parity`) -- ensure it still passes (the batch
function's per-cut decision matches the kernel's weakest-edge choice).

**Caller change:** the split loop (`build_cluster_frames` ~607-640 and the
`_finalize` tail) calls `split_oversized_cluster_to_size(...)` ONCE per top-level
oversized cluster instead of the re-enqueue loop. `edge_work` is charged
`len(ps)` once per top-level oversized cluster (not per re-split), so the budget
(#726) is no longer exhausted by redundant MST rebuilds.

### Component 2 -- budget scaling + config (auto-scale AND config field) (#726)

The edge-work budget becomes `max(5_000_000, n_rows * C)` (C default ~5, per the
issue), with precedence: **explicit config field > `GOLDENMATCH_CLUSTER_SPLIT_EDGE_BUDGET`
env > auto-scaled default**.

**Threading reality (~5 edit sites, confirmed by review):** `build_clusters` /
`build_cluster_frames` / `build_clusters_columnar` do NOT receive
`GoldenRulesConfig` -- the pipeline unpacks loose scalars (`max_cluster_size`,
`auto_split`, `weak_cluster_threshold`) at `pipeline.py:~1455-1461` and passes them
to all three call sites (`:1497`, `:1504`, `:1519`). So Component 2 needs: (a) a new
`split_edge_budget: int | None = None` field on `GoldenRulesConfig`
(`config/schemas.py:439`, where `auto_split`/`max_cluster_size` live); (b) thread it
+ `n_rows` through the unpack block + the three build functions; (c) change
`_split_edge_work_budget()` (`:37`) to `_split_edge_work_budget(n_rows, override=None)`
applying precedence override > env > `max(5M, n_rows*C)`. `n_rows = len(all_ids)`
is in scope inside `build_cluster_frames` (`:494`) and `_finalize_clusters`. With
Component 1, exhaustion is rare; this makes the rare case scale-appropriate and
tunable.

### Component 3 -- loud, non-silent failure (#726 DX)

When the (scaled) budget is GENUINELY exhausted:
- Upgrade the existing log to a loud, actionable `WARNING`: the count of clusters
  left oversized AND the knob to raise the budget (config field / env var).
- **Keep the oversized clusters in the output, flagged** (`cluster_quality`/
  `oversized` marker) -- do NOT silently drop them. The oversized-clusters-excluded-
  from-golden behavior itself stays (a 30K-member cluster should not collapse to one
  golden record), but the loss is now visible and actionable rather than silent.
- (No exception by default -- partial-but-labeled output beats crashing the whole
  run over a few dense clusters. Mirrors the "surface, don't silently degrade"
  lesson from #715 without the hard raise, which the user chose for this case.)

## Out of scope
- The min-cut quality upgrade (#661 optional: Henzinger 1708.06127 / Karger) --
  changes co-clustering, breaks the invariance gate, its own project. Deferred.
- EM non-convergence (#726 hypothesis 3: em_iterations default / last-iter
  weights) -- a separate probabilistic-scorer concern; Components 1+2 resolve the
  exhaustion regardless. Parked as a follow-up note on #726.

## Testing and validation
- **Byte-identical parity backstop (HARD):** the REAL backstop is the
  function/path parity test family, NOT the manual `scripts/quality_invariant_scale.py`
  F1 harness (which is a separate doc-level scale check, not an automated
  co-clustering gate). Component 1's equivalence test EXTENDS
  `tests/test_columnar_drop_pairscores_parity.py::test_columnar_drop_pairscores_byte_identical`
  -- it already exercises an oversized-that-splits cluster AND a dense-clique-that-
  can't-split at `max_cluster_size=5`, asserting members/size/oversized/confidence/
  bottleneck/cluster_quality byte-identical. Add a dense-multi-split fixture (needs
  >=3 components) so the batch path's repeated cuts are exercised. This is the
  mandatory backstop -- the one thing that catches a wrong batch implementation.
- **Native parity:** `tests/test_native_parity.py::test_split_oversized_cluster_parity`
  (5 MST-split fixtures incl. tie-scored + dense-clique) must still pass -- the
  batch path's per-cut weakest-edge decision + first-minimum tie-break must match
  the native kernel.
- **Perf (#661):** on the known dense-cluster pathology fixture
  (`project_build_clusters_dense_split_pathology`), assert the MST is built ONCE
  per top-level oversized cluster (e.g. instrument `_build_mst` call count, or
  assert wall/edge_work scales O(E) not O(E*k)). The hang is gone.
- **Budget (#726):** auto-scale test (`n_rows` large -> budget > 5M); config-field
  override; env precedence (config > env > default).
- **Failure mode (#726):** force budget exhaustion (tiny budget + a dense cluster)
  -> assert the WARNING fires with count + knob AND the oversized clusters are
  present in the output (flagged), NOT dropped.
- **Equivalence unit test:** for a dense cluster, `split_oversized_cluster_to_size`
  returns the same final partition as the old iterative re-split loop (lock the
  invariance at the function level, not just the gate).

## Risks
- **Component 1 silently changes co-clustering** (primary). Mitigation: preserve
  exact per-component-weakest-edge decisions; the quality-invariance gate +
  function-level equivalence test are the backstops.
- **Native/Python divergence**: the batch path is Python; ensure decisions match
  the native kernel's weakest-edge choice (parity test).
- **Budget scaling makes a genuinely huge dense cluster expensive**: Component 1
  bounds the split cost; the cap + the loud-warn-and-keep failure mode handle the
  residual pathological case without a hang.
