# Drop eager per-cluster pair_scores from the columnar build (Phase 2 SP4) — design

**Date:** 2026-06-02
**Status:** design (approved by user, pre-spec-review)
**Decision context:** The headline of the Phase-2 columnar-cluster roadmap. SP1
(#673) shipped the columnar `build_clusters` core but its measure-first bench
LOST (0.77-0.82x, ~2x RSS) because the columnar path STILL materializes a
per-cluster `pair_scores` dict (the dict adapter) on top of the Arrow UF — the Arrow
UF's 2.4-3.1x was swamped by that eager N-dict cost. SP2 (#679, identity via a
ClusterPairScores view), SP3 (#680, `DedupeResult.scored_pairs` from the
pre-cluster stream), and #681 (`unmerge_record` optional `scored_pairs`) migrated
all the durability/output-critical `pair_scores` consumers OFF the cluster dict, so
the dict can now drop `pair_scores` without breaking them. SP4 makes the columnar
build STOP materializing the per-cluster dicts — where the win finally lands.

## Goal

The gated columnar `build_clusters` returns `dict[int,dict]` with `pair_scores={}`
uniformly (no eager per-cluster dict). Scores are exposed via a `ClusterPairScores`
view built at the pipeline level from SP3's `scored_pairs` + the clusters'
membership. Everything else stays byte-identical. Measure-first: flip the gate
default-ON if the columnar path now wins.

## Architecture

### Component 1: columnar build stops materializing per-cluster dicts

`core/cluster.py::_build_clusters_via_frames` (SP1) today: (step 1) Arrow UF ->
member sets; (step 2) sort-by-min + result dict with `pair_scores={}`; (step 3)
fills per-cluster `pair_scores` via `replace_strict` + in-order iterate (THE eager
cost); (step 4) `compute_cluster_confidence` per cluster; (step 5)
`_finalize_clusters` (auto-split + quality + emit). SP4 changes:

- **Step 3 — REMOVE the eager fill.** Leave `pair_scores={}` on every cluster.
  The `pairs_df` / tagged-frame work that fed the dict is dropped from the result
  path (the frame is still needed transiently for confidence/split — see below).
- **Step 4 — confidence from the native kernel metadata (perf path).** The Arrow
  kernel `build_clusters_arrow` already emits per-cluster `confidence` +
  `bottleneck_pair_a/b` on `frames.metadata`, computed in pair-INPUT order via the
  same `cluster_confidence` logic — bit-identical to `compute_cluster_confidence`
  (SP1's spec asserted this; SP1 deferred USING it and computed via
  `compute_cluster_confidence` on the now-removed dicts). SP4 reads
  `confidence`/`bottleneck_pair` straight off `frames.metadata`, keyed by
  `cluster_id`. The kernel's sort-by-min-member `cluster_id` equals the Python
  canonical cid (both sort-by-min, enumerate start=1), so the mapping is direct;
  `(0,0)` bottleneck sentinel -> `None` as in SP1. **Off-native** (no kernel /
  v2 fallback): compute confidence via a TRANSIENT per-cluster `pair_scores` fill in
  PAIRS-INPUT ORDER (the SP1 step-3 fill) + `compute_cluster_confidence`, then
  DISCARD that fill (the returned dict still carries `pair_scores={}`). **Do NOT
  compute off-native confidence from a `scored_pairs`-member-filtered map:**
  `compute_cluster_confidence`'s `avg_edge = sum/len` is a sequential left-fold, and
  `scored_pairs` is sorted by `(a,b)` (SP3 `dedup_pairs_max_score`) — a different
  iteration order would drift the float sum ~1e-13 and break EXACT-float parity. The
  pairs-input-order transient fill matches the dict path's order exactly. Off-native
  is the non-perf path, so paying the transient fill there is fine. Native's metadata
  confidence is ALSO pairs-input order (the kernel buckets edges by `id_a` in input
  order), so both states are STRICT byte-identical to the dict path.
- **Step 5 — auto-split materializes dicts ONLY for oversized clusters.** The
  shared `_finalize_clusters` auto-split needs a per-cluster `pair_scores` dict for
  `split_oversized_cluster` (MST). For each cluster with `size > max_cluster_size`,
  build that ONE cluster's `pair_scores` by filtering `scored_pairs` to its members
  (= exactly its scores, the #681 single-cluster argument), split, then RESET the
  resulting sub-clusters' `pair_scores` to `{}` so the final dict is uniformly
  score-free. Oversized clusters are rare (size > 100 default), so this is cheap.
  The quality (weak/split) step reuses the kernel min/avg or
  `compute_cluster_confidence` values already available — it does NOT need the dict.

The `_finalize_clusters` auto-split loop is shared with the dict path; SP4 must NOT
change the dict-path (gate-OFF) behavior. The cleanest split: the columnar caller
passes the oversized-only materialized `pair_scores` into the split, while the dict
path keeps its full dicts. (Plan-time: extract the per-oversized materialization so
the dict path is untouched — or pass `scored_pairs` into `_finalize_clusters` and
have it materialize per-oversized only when the cluster's `pair_scores` is empty.)
**FOOTGUN (sequencing):** the auto-split loop meters `edge_work += len(cinfo
["pair_scores"])` (the #661 dense-cluster budget guard) and passes `pair_scores`
into `split_oversized_cluster`. The per-oversized materialization MUST happen
BEFORE that `edge_work` line (else, with an empty dict, `edge_work` is always 0 and
the budget guard never trips on a dense cluster — a behavior change vs the dict
path). Materialize the oversized cluster's `pair_scores` first, then meter + split.

### Component 2: ClusterPairScores view at the pipeline level

New `ClusterPairScores.from_scored_pairs(scored_pairs, clusters)`: build
`member_to_cid` from `clusters` membership, then per cluster collect the
`scored_pairs` whose BOTH endpoints are in that cluster (canonical `(min,max)` key)
-> exactly that cluster's old `pair_scores` (byte-identical, the #681 argument). The
pipeline (`_run_dedupe_pipeline`), when `_columnar_cluster_build_enabled()`, builds
the view from the result's `scored_pairs` (SP3) + `clusters`, and passes it to
identity as `pair_score_view` (replacing SP2's `from_cluster_dict(clusters)`, which
would now yield an EMPTY view since the dict's `pair_scores` is `{}`). Off-gate,
identity keeps reading `cluster["pair_scores"]` (the dict path still fills it).
**FOOTGUN (sequencing):** `scored_pairs` is currently computed AFTER the identity
block in `_run_dedupe_pipeline` (~:1737-1742), but the `pair_score_view` is built
BEFORE identity (~:1720-1725). The plan MUST move the `scored_pairs` computation
ABOVE the view build so the gate-on branch has `scored_pairs` available to build
`from_scored_pairs`.

### Component 3: measure-first + gate flip

Bench columnar (gate ON, no eager dicts) vs dict (gate OFF) at 1M/5M on
`large-new-64GB` (fresh native), wall + peak RSS, with the parity asserted. **This
is where the 2.4-3.1x should appear** (the eager N-dict was the SP1 loss). If
columnar wins net, flip `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` **default-ON** (kill-
switch `=0`) — the roadmap payoff. If it does NOT win, keep gated and record why.

## Parity gates (HARD)

- **Dict-minus-pair_scores byte-identical:** an adversarial fixture (singletons incl
  id 0, multi-member, fully-connected, weak chain -> weak, oversized that splits,
  oversized that can't split, score ties, dup pairs). Assert gate-ON dict `==`
  gate-OFF dict on EVERY field EXCEPT `pair_scores` (members as a SET per SP1/#681
  native member-order nondeterminism; `size`, `oversized`, `confidence` EXACT float,
  `bottleneck_pair`, `cluster_quality`, cluster ids strict). Gate-ON `pair_scores`
  is `{}` for every cluster. Run native AND off-native.
- **View carries the dropped scores:** assert
  `view.for_cluster(cid) == off_gate_dict[cid]["pair_scores"]` for every cluster
  (the view built `from_scored_pairs` reproduces exactly what the dict dropped).
- **Confidence from metadata == compute_cluster_confidence:** a unit test that the
  native `frames.metadata` confidence/bottleneck equals `compute_cluster_confidence`
  per cluster on the fixture (bit-identical; the thing SP1 deferred). CI-native.
- **Consumer suites green gate-ON:** `tests/identity/`, `test_golden.py`,
  `tests/test_unmerge_scored_pairs.py`, `test_scored_pairs_decouple.py` — all pass
  with `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD=1` (they consume the view / scored_pairs,
  already migrated).
- **Measure-first bench** (decides the gate default).

## Edge cases / invariants

- **Graceful-degraders see `{}`:** `tui/tabs/matches_tab.py` (correction modal pair
  score) and `core/explain.py::explain_cluster_nl` read `cluster["pair_scores"]`
  tolerantly (`.get`); on the columnar path they get `{}` -> lose the score
  display, do NOT break. Documented behavior change for the gated path; they can
  adopt the view in a later SP. NOT in SP4 scope.
- **Off-native:** no native kernel -> confidence via per-cluster
  `compute_cluster_confidence` on a `scored_pairs`-filtered map; view + dict shape
  identical to native. Byte-identical parity runs both states.
- **Native CI-only:** the in-tree `_native.pyd` lacks `build_clusters_arrow`; the
  native metadata-confidence path is CI-only-validated (as SP1). Local exercises the
  off-native path. (Also: the dev box currently hangs on `import goldenmatch` ->
  validate via ruff/py_compile + CI.)
- **`build_clusters` signature unchanged** (still `-> dict[int,dict]`); the view is
  built at the pipeline level, NOT returned from `build_clusters` (no 12-call-site
  churn).

## Scope boundary (YAGNI)

- `core/cluster.py` columnar internals (steps 3-5 of `_build_clusters_via_frames` +
  the oversized-only split materialization), `core/cluster_pairscores.py`
  (`from_scored_pairs`), the pipeline view-construction switch, the bench. NO new
  Rust kernel (reuse the existing `build_clusters_arrow` metadata). NO dict-path
  (gate-OFF) behavior change. NO graceful-degrader migration (tui/explain stay
  tolerant; later SP). **#5 (distributed `materialize_cluster_dict` pair_scores)
  DEFERRED** — separate path/gate, parallel. NO ClusterFrames schema change.

## References

- SP1 `2026-06-02-columnar-cluster-build-core-design.md` (#673): `_build_clusters_via_frames`,
  `_finalize_clusters`, native metadata confidence (deferred). SP2 `2026-06-02-cluster-pairscore-view-design.md`
  (#679): `ClusterPairScores`. SP3 `2026-06-02-scored-pairs-decouple-design.md` (#680):
  `scored_pairs` on the result. #681: per-cluster member-filter == pair_scores.
- `core/cluster.py`: `_build_clusters_via_frames`, `_finalize_clusters`,
  `split_oversized_cluster`, `build_clusters_arrow_native` (+ `frames.metadata`).
  `core/cluster_pairscores.py`: `ClusterPairScores`. `core/pipeline.py`: view build
  + `scored_pairs` capture + identity.
- Related: [[project_663_arrow_kernels]], [[project_arrow_native_finish_line]],
  [[project_identity_graph_v2]].
