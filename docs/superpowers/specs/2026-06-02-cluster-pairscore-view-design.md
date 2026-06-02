# Lazy cluster pair-score view + identity migration (Phase 2 SP2) — design

**Date:** 2026-06-02
**Status:** design (approved by user; re-scoped to IDENTITY-ONLY after plan review
found golden's pair_scores is a phantom — see "Re-scope" below)
**Decision context:** Phase-2 SP1 (PR #673, columnar cluster-build core + dict
adapter, gated `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD`) SHIPPED but its measure-first
bench found the columnar path LOSES net of the eager per-cluster `dict[int,dict]`
materialization (0.77-0.82x, ~2x RSS), so the gate stays default-OFF. The win
(spike: 2.4-3.1x UF-only) lands only when consumers migrate OFF the dict adapter
AND the build produces a columnar pair frame natively. SP2 is the first,
durability-critical slice: a **lazy `ClusterPairScores` view** + migrate the
`identity/resolve.py` evidence-edge consumer onto it, byte-identical. It is an
ABSTRACTION + de-risking step, not (yet) a perf win — see "Measure-first / gate".

## Re-scope (plan review, 2026-06-02)

The original spec also migrated golden `confidence_majority`. Plan review found
that **the pipeline never feeds `pair_scores` to golden**:
`build_golden_records_batch` (`golden.py:662`) takes no `pair_scores` arg, the
per-cluster `merge_field` call (~`:810`) passes none, and `_confidence_majority`
(`:242`) silently falls back to count-majority when `pair_scores is None`. So a
"byte-identical golden migration onto the view" is vacuous (nothing to migrate;
the parity test would be a false-green no-op). That latent golden-survivorship bug
(confidence_majority never actually runs end-to-end) is filed as **issue #678**
and deferred to its own workstream (it's a behavior change, not byte-identical).
**SP2 is therefore identity-only.**

## Problem

`build_clusters() -> dict[int, dict]` materializes, per cluster, a
`pair_scores: dict[(a,b)->score]`. That eager materialization is the SP1 adapter
cost. The `pair_scores` consumer SP2 targets:

- **`identity/resolve.py`** — emits ONE evidence edge per within-cluster scored
  pair. TWO emit sites in the same `for cluster_id, info in clusters.items()`
  loop: the in-memory slow path (`:559-560`) and the postgres bulk fast path
  (`:396-398`); plus a weak-cluster bottleneck-pair score lookup
  (`info.get("pair_scores", {}).get((min,max))`, `:601-602`). These feed the
  identity graph (entity-ids + evidence edges) — the **durability invariant**.

`ClusterFrames` (assignments + metadata) deliberately carries NO `pair_scores`.
SP2 provides the scores via a lazy view instead of the per-cluster dict.

## Goal

A `ClusterPairScores` lazy view, sourced from the FINAL (post-split) cluster
partition, that `identity/resolve.py` consumes at all three sites — with
**byte-identical evidence edges + bottleneck score** vs the dict path, behind the
existing `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` gate, native AND off-native.

## Architecture: lazy view, gated identity consumer

**Gate reuse.** Reuse the existing `GOLDENMATCH_COLUMNAR_CLUSTER_BUILD` gate (no
new env var). When ON, the pipeline builds the view from the returned cluster
dict and threads it into the resolver; when OFF, today's dict path verbatim.
`build_clusters() -> dict` is UNCHANGED for all other consumers (SP3+).

### Component 1: `ClusterPairScores` (the lazy view)

New module `core/cluster_pairscores.py` (keep `cluster.py` from growing). Sourced
from the FINAL post-split `result` dict's per-cluster `pair_scores` (see
"Post-split keying"). SP1's `_build_clusters_via_frames` step-3 `tagged` frame
(`pl.col("id_a").replace_strict(member_to_cid)`) is the **shape precedent ONLY**;
SP2 does NOT plumb that pre-split, `del`'d-internally frame out. API:

- `from_cluster_dict(clusters) -> ClusterPairScores` — copy each cluster's
  `pair_scores` (already final-partition, pairs-input order, last-wins on dup
  canonical pairs).
- `for_cluster(cid) -> dict[(int,int),float]` — that cluster's scores (empty for
  singleton/absent). Used for the bottleneck lookup and the per-cluster edge set.
- `iter_clusters() -> Iterator[(cid, Iterable[(a,b,score)])]` — clusters WITH
  pairs, pairs in row order. For the evidence-edge emit loop (one pass).
- `score_for(cid, a, b) -> float | None` — canonical-pair `(min,max)` lookup.

**Internal repr is a dict-of-dicts** (the simplest byte-identical-safe backing
given the build still produces the dict). The spec's "flat frame" is the
conceptual model; the `iter_clusters` interface is frame-ready so a FUTURE SP can
swap the backing to a Polars `partition_by` once the BUILD produces the frame
natively (and drops the dict) — that future SP is where the perf win + the
columnar pair frame land. SP2 deliberately does not add a premature flatten.

### Component 2: identity migration (durability-critical)

`identity/resolve.py` reads each cluster's `pair_scores` from the cluster dict at
THREE sites in the `for cluster_id, info` loop. Thread an optional
`pair_score_view: ClusterPairScores | None` into the resolver; when provided, all
three sites read from it (`for_cluster(cluster_id)` for the edge sets,
`score_for(cluster_id, a, b)` for the bottleneck), else `info["pair_scores"]`:

- in-memory slow path evidence edges (`:559-560`),
- postgres bulk fast path evidence edges (`:396-398`),
- weak-cluster bottleneck score (`:601-602`).

Because `for_cluster(cid) == info["pair_scores"]` by construction, behavior is
byte-identical; the parity gate proves no drift.

**Hard gate:** byte-identical evidence-edge set (+ bottleneck score), native AND
off-native, gate ON vs OFF — the entity-id durability proof. The SQLite parity
test exercises the slow path (`:559`); the postgres bulk path (`:396`) takes the
IDENTICAL one-line `info["pair_scores"] -> view.for_cluster(cluster_id)`
substitution in the same loop reading the same `info`, so it is covered by code
parity (a postgres CI lane is not required for SP2; note it explicitly).

### Post-split keying (correctness invariant)

Auto-split renumbers oversized clusters and re-partitions `pair_scores` (cross-cut
edges dropped). identity consumes FINAL clusters, so the view MUST key by FINAL
cluster_id. Resolution: build the view from the FINAL `result` dict (post-split)
— provably consistent, since `for_cluster(cid)` returns exactly `result[cid]
["pair_scores"]`. **Construction point (pin for the planner):** built at the
pipeline level FROM the returned cluster dict, NOT by reaching into
`_build_clusters_via_frames` internals.

## Measure-first / gate

SP2 is identity-only and the view is sourced from the still-materialized dict
(adds a copy), so it is NOT expected to win perf — it is an abstraction +
de-risking of the durability-critical identity path. Therefore SP2 does NOT flip
the gate default (stays OFF) and ships no dedicated perf bench: a dict-copy
abstraction has no win to chase, and a regression guard rides on SP1's existing
`bench-columnar-cluster-build` workflow (which already exercises `build_clusters`
gate on/off). The perf win + default-on decision belong to a FUTURE SP that makes
the build produce the columnar pair frame natively and drops the per-cluster dict
— at which point identity already consumes the view, so that SP needs no further
identity-durability re-validation.

## Edge cases / invariants

- **Singletons / empty:** no pairs -> empty per-cluster slice -> identical to
  today's empty `pair_scores`.
- **Duplicate canonical pairs:** the source dict already collapsed them
  (last-wins); the view copies as-is.
- **Off-native:** the view sources from the same final `result`; byte-identical
  gate runs native AND off-native.
- **Bottleneck `(0,0)`/None:** unchanged (handled in the build / `bottleneck_pair`).
- **Identity replay idempotency:** unchanged (`has_run_event`, UNIQUE edges).

## Testing

- **Byte-identical evidence edges (HARD, durability):** run identity resolve on
  an adversarial cluster fixture (multi-member, oversized-split, singletons, dup
  pairs) against a SQLite store, gate ON vs OFF; assert the read-back
  `evidence_edges` (entity_id, record_a, record_b, kind, score) are byte-identical,
  parametrized `GOLDENMATCH_NATIVE` `["1","0"]` (skip native=1 when the native
  cluster kernel is absent, mirroring `test_columnar_cluster_build_parity.py`).
  Include an oversized-split cluster so the post-split partition is exercised.
- **View unit tests:** `for_cluster` == dict, `iter_clusters` order, `score_for`
  canonical lookup, empty/singleton/absent.
- **Regression:** existing `tests/identity/` green with the gate ON (no new
  failures vs OFF).

## Scope boundary (YAGNI)

- ONLY `core/cluster_pairscores.py` (the view), the three gated read sites in
  `identity/resolve.py`, and the pipeline gate plumbing.
- NO golden migration (golden's pair_scores is a phantom — issue #678, separate
  workstream). NO new Rust kernel. NO `ClusterFrames` schema change. NO migration
  of unmerge/explain/compare/display (SP3+). NO change to `build_clusters`'s
  default return or the dict path. NO dedicated perf bench / gate-default flip
  (deferred to the future columnar-frame SP).

## References

- SP1: `docs/superpowers/specs/2026-06-02-columnar-cluster-build-core-design.md`
  (PR #673). `core/cluster.py`: `_columnar_cluster_build_enabled()` (`:436`),
  `_build_clusters_via_frames` (`:621`), `_finalize_clusters` (`:530`).
- Consumers: `identity/resolve.py` evidence edges `:396-398` (bulk) + `:559-560`
  (slow), bottleneck `:601-602`, loop `for cluster_id, info` (`:333`),
  `resolve_clusters` (`:209`); `pipeline.py` clustering `:1455`, `_resolve_identities`
  (`:245`), identity call `:1714`.
- Golden phantom (deferred): issue #678; `golden.py:662,242`, `pipeline.py:1600-1612`.
- Related: [[project_663_arrow_kernels]], [[project_identity_graph_v2]],
  [[project_stable_record_fingerprint]].
