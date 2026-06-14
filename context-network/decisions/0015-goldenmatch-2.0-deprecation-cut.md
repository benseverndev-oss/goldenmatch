# 0015 — GoldenMatch 2.0: cut the deprecation-window code, keep the universal pair path

**Status:** accepted (2026-06-14, Ben) • **Shipped:** PR #942 (2.0.0, merged `93193ccb`), docs sync PR follow-up • **Migration:** [docs-site/goldenmatch/migrating-to-v2.mdx](../../docs-site/goldenmatch/migrating-to-v2.mdx) • **Builds on:** [../../docs/adr/0006-telemetry-gated-rule-deprecation.md](../../docs/adr/0006-telemetry-gated-rule-deprecation.md)

## Context
GoldenMatch held at `1.30.0` with four pieces of code that had each shipped a 1.x
deprecation runway and were waiting on the first major to be removed: the legacy
`:hash:` identity-lookup bridge + its `GOLDENMATCH_IDENTITY_ID_SCHEME` kill-switch,
the `GOLDENMATCH_CLUSTER_FRAMES_OUT` gate + the pre-frames `dict[int,dict]` cluster
pipeline branch, and two dead internal shims (`AutoConfigHistory.cheapest_healthy`,
`_scale_aware_backend`). Semver is strict after 1.0.0, so removing any of them
required a major bump. The question was scope: which "legacy" paths are genuinely
retireable versus load-bearing.

## Decision
1. **Cut exactly the four prepared items, nothing more.** Each had a runway and a
   zero-caller (or telemetry-gated) property. The 2.0.0 commits are `feat(v2)!:`
   breaking, intentional.
2. **The scorer `list[tuple]` pair path is NOT a deprecation — it stays.** It is
   the permanent universal pair representation across every backend; only the
   *cluster* dict path (gated by `GOLDENMATCH_CLUSTER_FRAMES_OUT`) was retired,
   because frames-out has been the default and output-equivalent since PR #841.
   See [../architecture/datafusion-spine.md](../architecture/datafusion-spine.md).
3. **`build_clusters` stays public, byte-unchanged, as a frames-backed adapter.**
   Removing the gate removed the pipeline's *use* of the dict path, not the public
   function. `core.pipeline` no longer imports `build_clusters` (it imports
   `build_cluster_frames` + `build_clusters_columnar`).
4. **The `:hash:` removal is asymmetric.** Fingerprintable rows resolve to a single
   canonical `:h1:` candidate (the dual-candidate fallback + once-per-process
   warning are gone). Un-fingerprintable rows KEEP `:hash:` as their only id.
   `sail/identity.py` carries a SEPARATE `_id_scheme` and was left untouched.
5. **Provide a migration, gate the publish on a human + green main.** Persisted
   identity DBs migrate via `goldenmatch identity migrate-ids` (shipped in 1.x).
   The irreversible tag/PyPI/MCP publish only fired after CI was green on the
   merged commit AND an explicit maintainer go.

## Consequence
- 2.0.0 is live on PyPI + the MCP registry; pipeline behavior is output-equivalent
  (the breaks are removed escape hatches, internal shims, and the legacy id scheme).
- The opt-in layers that proved inert/net-negative at scale stayed default-OFF
  rather than being cut or flipped: quality-aware blocking + FD-negative-evidence
  (no-ops on Febrl3/NCVR) and `GOLDENMATCH_COLUMNAR_PIPELINE` (~1-2% slower, more
  RSS). Removing an opt-in costs a major; keeping a measured-inert flag costs a row.
- **Verification lesson:** a targeted local pytest run is NOT the sharded CI suite.
  The merge went red on CI shard 3 because `test_score_partition_kernel.py`
  `mock.patch`ed the now-removed `pipeline.build_clusters` — an AttributeError at
  patch time that no targeted local run covered. After deleting a module-level
  import, grep ALL of `tests/` for `mock.patch`/`patch.object` on that symbol.
  The `version_consistency` gate also caught a missed `server.json` bump
  (pyproject + `__init__` + `server.json` must move in lockstep).
- **Parking lot:** `scripts/count_max_vs_last.py` still monkeypatches the removed
  `pipeline.build_clusters` (a dead diagnostic, not pytest-collected; its premise
  reads the old cluster dict). Delete or rewrite when convenient.
