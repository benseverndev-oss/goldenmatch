# Lazy cluster pair-score view + identity/golden migration (Phase 2 SP2) — design

**Date:** 2026-06-02
**Status:** design (approved by user, pre-spec-review)
**Decision context:** Phase-2 SP1 (PR #673, columnar cluster-build core + dict
adapter, gated `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD`) SHIPPED but the measure-first
bench found the columnar path LOSES net of the adapter (0.77-0.82x, ~2x RSS) — the
eager per-cluster `dict[int,dict]` (esp. the per-cluster `pair_scores` dicts)
materialization swamps the fast Arrow UF. So the gate stays default-OFF. The win
(spike: 2.4-3.1x UF-only) lands only when consumers migrate OFF the dict adapter.
SP2 takes the first, highest-value slice: a **lazy pair-score view** (kills the
eager per-cluster `pair_scores` dict) + migrate the two durability/output-critical
`pair_scores` consumers — `identity/resolve.py` (evidence edges) and `golden.py`
`confidence_majority` survivorship — onto it. unmerge/explain/compare/display
consumers stay on the dict adapter (SP3+).

## Problem

`build_clusters() -> dict[int, dict]` materializes, per cluster, a
`pair_scores: dict[(a,b)->score]`. That eager materialization is the SP1
adapter cost. The two `pair_scores` consumers SP2 targets:

- **`identity/resolve.py`** — emits ONE evidence edge per within-cluster scored
  pair by iterating `info["pair_scores"].items()` (`resolve.py:396-398` and
  `:558-560`), plus a bottleneck-pair score lookup
  (`info.get("pair_scores", {}).get((min,max))`, `:601-602`). This feeds the
  identity graph (entity-ids + evidence edges) — the **durability invariant**.
- **`golden.py` `_confidence_majority`** (`golden.py:235+`) — sums `pair_scores`
  of agreeing edges per candidate value for survivorship. Only reached when the
  golden strategy is `confidence_majority` (a slow-path strategy via
  `build_golden_records_batch`; the columnar `build_golden_records_df` fast path
  never uses pair_scores). So golden's pair_scores dependence is NARROW.

`ClusterFrames` (assignments + metadata) deliberately carries NO `pair_scores`.
SP2 provides the scores via a lazy view over a cluster-tagged pair frame instead
of N per-cluster dicts.

## Goal

A `ClusterPairScores` lazy view, sourced from a cluster-tagged pair frame the
columnar build already nearly produces, that `identity/resolve.py` and
`golden.py::_confidence_majority` consume — with **byte-identical evidence edges
and golden records** vs the dict path, behind the existing
`GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` gate, measured against the dict path.

## Architecture: lazy view, gated columnar consumers

**Gate reuse.** Extend the existing `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` gate
(no new env var). When ON, the pipeline produces `(ClusterFrames,
ClusterPairScores)` from the columnar build and routes identity + golden through
columnar consumers; when OFF, today's dict path verbatim. `build_clusters() ->
dict` is UNCHANGED for all other consumers (SP3+).

### Component 1: `ClusterPairScores` (the lazy view)

Backed by a **cluster-tagged pair frame** `(cluster_id, id_a, id_b, score)`.
SP1's `_build_clusters_via_frames` already builds a structurally-identical frame
at `cluster.py` step 3 (`tagged = pairs_df.with_columns(pl.col("id_a").
replace_strict(member_to_cid).alias("__cid__"))`) — that is the **shape
precedent** ONLY. SP2 does NOT plumb that transient pre-split frame out (it is
keyed by PRE-split cluster_id and is `del`'d inside the columnar core); SP2
SOURCES the view from the FINAL post-split `result` partition instead (see
"Post-split keying" below — this is byte-identical-load-bearing, not cosmetic).
The win is that the CONSUMERS stop holding N per-cluster `pair_scores` dicts, not
that the build stops computing the partition. New module
`core/cluster_pairscores.py` (keep `cluster.py` from growing):

- `ClusterPairScores(tagged_frame)` — wraps the frame; cheap to construct.
- `iter_clusters() -> Iterator[tuple[int, Iterable[tuple[int,int,float]]]]` —
  ONE `partition_by("cluster_id", maintain_order=True)` pass yielding each
  cluster's pairs IN ROW ORDER. For identity's "every within-cluster pair -> one
  evidence edge" — one columnar pass, no per-cluster dict.
- `for_cluster(cid) -> dict[(int,int), float]` — that cluster's pairs as a dict
  on demand (filter + build), for golden's per-cluster `confidence_majority` and
  the bottleneck-score lookup. Last-wins on duplicate canonical pairs (matches
  the dict path's `result[cid]["pair_scores"][(a,b)] = score` overwrite).

**Order + dedup invariants (load-bearing for byte-identical):** pairs in
ROW/pairs-input order; duplicate canonical pairs overwrite last-wins; cluster id
numbering = SP1's sort-by-min-member. The pre-split mapping is correct because
identity/golden operate on the FINAL (post-split) clusters — see "Post-split
keying" below.

### Component 2: identity migration (durability-critical)

`resolve.py` currently reads each cluster's `pair_scores` dict from the cluster
dict. Migrate the columnar path to consume `ClusterPairScores`:
- Evidence edges (`:396-398`, `:558-560`): pull the cluster's pairs from the
  view IN THE SAME ORDER and emit the SAME edges. The resolver's edge-emit loop
  must produce a byte-identical edge set + order (the `evidence_edges` UNIQUE
  constraint + replay idempotency mean order isn't semantically load-bearing,
  but we gate it byte-identical anyway to prove no drift).
- Bottleneck score lookup (`:601-602`): `view.for_cluster(cid).get((min,max))`
  or a dedicated `score_for(cid,a,b)` — identical value.
- **Hard gate:** byte-identical evidence-edge set (+ bottleneck score), native
  AND off-native, gate ON vs OFF. This is the entity-id durability proof.

### Component 3: golden migration

`pipeline.py` golden build (`:1591-1615`) passes each multi-member cluster's
`pair_scores` to `build_golden_records_batch` (slow path) which forwards to
`_confidence_majority`. Migrate the columnar path to source each cluster's
`pair_scores` from `view.for_cluster(cid)` instead of `cluster["pair_scores"]`.
The narrow surface: among built-in strategies ONLY `confidence_majority` reads
pair_scores; the columnar `build_golden_records_df` fast path is untouched.
(`custom:` plugin strategies are ALSO forwarded `pair_scores` at `golden.py:82,
:345` — they are covered transparently: the view feeds the SAME per-cluster dict
via `for_cluster(cid)`, so any pair_scores-reading strategy is byte-identical
without per-strategy work.) **Hard gate:** byte-identical golden records on a
`confidence_majority` fixture, gate ON vs OFF.

### Post-split keying (correctness invariant)

Auto-split renumbers oversized clusters (new cids, partitioned pair_scores).
identity/golden consume FINAL clusters, so the view must key by FINAL cluster_id,
not the pre-split tag. Resolution: build the view's tagged frame AFTER
`_finalize_clusters`, by re-tagging from the final `member_to_cid` (post-split).
The split sub-clusters' pair_scores partition is exactly what `split_oversized_
cluster` already computes (`sub_pairs`); the view must reflect that partition.
**Simplest correct approach:** derive the view's tagged frame from the FINAL
`result` dict's per-cluster `pair_scores` (one pass flattening to
`(cid, a, b, score)`), so the view is provably consistent with the dict path's
final partition — the columnar win is removing the eager dict from the
CONSUMERS, while the build still computes the canonical partition. (If profiling
shows the final-dict flatten is itself the cost, a later SP pushes the split
partition fully columnar; out of scope for SP2.)

**Construction point (pin for the planner):** the view is built at the
`build_clusters` call site / pipeline level FROM the `_finalize_clusters` output
(the returned `result` dict's per-cluster `pair_scores`), NOT by reaching into
`_build_clusters_via_frames` internals (which `del`s its tagged frame and is
pre-split). This keeps view-construction post-split and out of the columnar core.

## Measure-first

This is where the adapter-free win should appear. Bench identity-resolve +
golden (confidence_majority) end-to-end, columnar (gate ON) vs dict (gate OFF),
1M/5M, wall + peak RSS, with the byte-identical evidence-edge + golden parity
asserted. Flip `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` default-ON only if the
columnar identity+golden path wins net (per user: SP2 IS allowed to flip the gate
default-on if the bench wins). Else stay gated; SP3 migrates the remaining HARD
consumers before re-measuring.

## Edge cases / invariants

- **Singletons / empty:** no pairs -> empty per-cluster slice -> identical to
  today's empty `pair_scores`.
- **Duplicate canonical pairs:** last-wins in `for_cluster`; iter order in
  pairs-input order for `iter_clusters`.
- **Off-native:** the view sources from the same final `result` partition; no
  perf claim off-native (non-perf path), byte-identical gate runs both.
- **Bottleneck `(0,0)`/None sentinel:** unchanged from SP1 (handled in the build).
- **Identity replay idempotency:** unchanged (`has_run_event`, UNIQUE edges).

## Testing

- **Byte-identical evidence edges (HARD, durability):** run the pipeline's
  identity resolve gate ON vs OFF on an adversarial fixture (multi-member,
  oversized-split, singletons, dup pairs); assert the emitted `evidence_edges`
  (entity_id, record_a, record_b, kind, score) are byte-identical, native AND
  off-native. Mirror the existing identity test harness.
- **Byte-identical golden (HARD):** a `confidence_majority`-strategy fixture;
  assert golden records identical gate ON vs OFF, native AND off-native.
- **View unit tests:** `iter_clusters` order, `for_cluster` last-wins dedup,
  empty/singleton, post-split keying (view's per-cluster pairs == the dict path's
  final `pair_scores`).
- **Measure-first bench:** identity+golden columnar vs dict at 1M/5M, parity
  asserted; decides the gate default.

## Scope boundary (YAGNI)

- ONLY `core/cluster_pairscores.py` (the view), the columnar branches in
  `identity/resolve.py` + the pipeline golden build, and the gate plumbing.
- NO new Rust kernel. NO `ClusterFrames` schema change. NO migration of
  unmerge/explain/compare/display consumers (SP3+). NO change to the dict path
  or to `build_clusters`'s default return.
- The view sources from the FINAL `result` partition (provably consistent);
  pushing the split partition fully columnar is a later SP.

## References

- SP1: `docs/superpowers/specs/2026-06-02-columnar-cluster-build-core-design.md`
  (PR #673). `core/cluster.py`: `_build_clusters_via_frames` (tagged frame at
  step 3), `_finalize_clusters`, `split_oversized_cluster` (`sub_pairs`).
- Consumers: `identity/resolve.py:396-398,558-560,601-602`;
  `golden.py:_confidence_majority` (`:235+`), `build_golden_records_batch`
  (`:662`), `build_golden_records_from_frames` (`:1049`); `pipeline.py` clustering
  `:1455`, golden `:1591-1615`, identity `:1714`.
- Related: [[project_663_arrow_kernels]], [[project_identity_graph_v2]],
  [[project_stable_record_fingerprint]].
